#!/usr/bin/env node
/**
 * dsh-remote-web —— DSH 远程 Web 控制代理
 *
 * 作用：把「只允许 loopback、且带进程级一次性 token」的 DSH GUI，
 *      变成一个可以用【长期稳定令牌】从外网访问的入口。
 *
 * 为什么不直接给 DSH 加 --host 0.0.0.0：
 *   DSH 出于安全考虑在启动时硬拒绝 0.0.0.0（会把 RCE 暴露到网络），
 *   且 /api 有 Host/Origin 栅栏防 DNS rebinding。本代理不绕过这些设计，
 *   而是把对外鉴权的责任接过来：上游方向一律规范成 loopback 权威，
 *   由本代理自己做令牌校验。
 *
 * 为什么需要它（不能只用 dsh web 打印的 URL）：
 *   dsh web 的 ?token= 是【进程级】的，每次重启 DSH 都会变，没法做手机书签。
 *   DSH 的浏览器会话 cookie 用的是 .credentials.yaml 里持久化的签名密钥，
 *   本代理读该密钥自行签发 cookie，因此对手机而言令牌是长期稳定的。
 *
 * 架构：
 *   手机 ──HTTP+稳定令牌──▶ 你的服务器:17933 ──ssh -R 隧道──▶ 本代理 127.0.0.1:19390
 *                                                          │ 注入 DSH cookie
 *                                                          ▼
 *                                              DSH GUI 127.0.0.1:19387（保持 loopback）
 */
import { createServer, request as httpRequest } from 'node:http'
import { connect as netConnect } from 'node:net'
import { readFileSync, appendFileSync, existsSync, statSync } from 'node:fs'
import { createHash, createHmac, timingSafeEqual } from 'node:crypto'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const CONFIG_PATH = process.env.DSH_REMOTE_CONFIG ?? join(HERE, 'remote.config.json')

// ── 配置 ────────────────────────────────────────────────────────────────────
const config = JSON.parse(readFileSync(CONFIG_PATH, 'utf8'))
const {
  listenHost = '127.0.0.1',
  listenPort = 19390,
  upstream,
  publicBaseUrl = '',
  token,
  cookieDays = 29, // 必须 <= DSH 的 cookieMaxAgeDays（默认 30），否则 isAuthenticated 直接判假
  cookieName = 'dsh-remote',
  accessLog = 'access.log',
  mobileEnhance = true,
  // 跨平台：homedir() 在 Windows 是 C:\Users\X、macOS 是 /Users/x。
  // 早先写成 process.env.USERPROFILE —— macOS 没有这个变量，会拼出相对路径
  // ".dsh"，报「找不到凭证文件」。
  dshHome = process.env.DSH_HOME ?? join(homedir(), '.dsh')
} = config

if (typeof token !== 'string' || token.length < 16) {
  throw new Error('remote.config.json: token 缺失或过短（建议 32 字节随机）')
}
if (cookieDays > 30) throw new Error('remote.config.json: cookieDays 不能超过 30（DSH 默认 cookieMaxAgeDays）')

/**
 * 上游 DSH 的 host:port —— **动态解析**，不是启动时写死。
 *
 * 为什么必须动态：DSH 的 Web 端口是动态的（实测重启后从 19387 变成 3080），
 * 写死配置的话 DSH 每重启一次远程入口就 502 一次。策略：
 *   1. 首选候选：配置里的 upstream、环境变量 DSH_WEB_URL（从 DSH 的 shell 里启动代理时能拿到）；
 *   2. 首选连不上：扫本机处于 LISTEN 的端口 + 常见端口，用 DSH 的特征响应把它认出来；
 *   3. 端口一旦变化就切过去 —— cookie 名与内容都绑定 authority，所以会自动重签。
 */
const DSH_SIGNATURE = 'dsh web authentication required'

function normalizeHostPort(value) {
  if (typeof value !== 'string' || value.trim() === '') return null
  const raw = value.trim().replace(/^https?:\/\//u, '').split('/')[0]
  const [host, port] = raw.split(':')
  if (!host || !port || !/^\d+$/u.test(port)) return null
  return `${host}:${port}`
}

/** 配置里显式写的、或 DSH 注入环境变量给的首选上游（可能已经过期）。 */
function preferredUpstream() {
  return normalizeHostPort(upstream) ?? normalizeHostPort(process.env.DSH_WEB_URL ?? '')
}

let upstreamState = (() => {
  const first = preferredUpstream() ?? '127.0.0.1:19387'
  const [host, port] = first.split(':')
  return { host, port, authority: first, origin: `http://${first}` }
})()

/** 切换上游。返回是否真的变了（变了就要重签 cookie，见 currentDshCookie）。 */
function setUpstream(hostPort, why) {
  const [host, port] = hostPort.split(':')
  const authority = `${host}:${port}`
  if (upstreamState.authority === authority) return false
  upstreamState = { host, port, authority, origin: `http://${authority}` }
  log(`upstream 切换为 ${authority}${why ? `（${why}）` : ''}`)
  return true
}

/** 本机处于 LISTEN 的端口。跨平台：Windows 用 netstat，其它优先 ss。失败返回空数组。 */
function listenPorts() {
  const runs = process.platform === 'win32'
    ? [['netstat', ['-ano', '-p', 'tcp']]]
    : [['ss', ['-ltn']], ['netstat', ['-an', '-p', 'tcp']]]
  const ports = new Set()
  for (const [cmd, args] of runs) {
    try {
      const out = spawnSync(cmd, args, { encoding: 'utf8', timeout: 4000 })
      if (out.status !== 0 || typeof out.stdout !== 'string') continue
      for (const m of out.stdout.matchAll(/(?:127\.0\.0\.1|0\.0\.0\.0|\*|\[::1?\]):(\d{2,5})/gmu)) {
        ports.add(Number(m[1]))
      }
      if (ports.size > 0) break
    } catch {
      /* 该命令不存在就试下一个 */
    }
  }
  return [...ports]
}

/** 候选上游，按可信度排序。 */
function upstreamCandidates() {
  const out = []
  const push = (value) => {
    const normalized = normalizeHostPort(value)
    if (normalized !== null && !out.includes(normalized) && !normalized.endsWith(`:${listenPort}`)) out.push(normalized)
  }
  push(preferredUpstream())
  push(upstreamState.authority)
  for (const port of listenPorts()) push(`127.0.0.1:${port}`)
  for (const port of [3080, 19387, 19388, 19389, 3000, 8080]) push(`127.0.0.1:${port}`)
  return out
}

/** 探测 host:port 是不是 DSH 的 Web 服务：GET / 应回 401 且带 DSH 那句固定文案。 */
function probeUpstream(hostPort, timeoutMs = 800) {
  return new Promise((resolve) => {
    const [host, port] = hostPort.split(':')
    const req = httpRequest(
      { host, port: Number(port), path: '/', method: 'GET', timeout: timeoutMs, headers: { host: hostPort } },
      (res) => {
        let body = ''
        res.setEncoding('utf8')
        res.on('data', (chunk) => {
          if (body.length < 2048) body += chunk
        })
        res.on('end', () => resolve(res.statusCode === 401 && body.includes(DSH_SIGNATURE)))
      }
    )
    req.on('timeout', () => {
      req.destroy()
      resolve(false)
    })
    req.on('error', () => resolve(false))
    req.end()
  })
}

let lastDiscoverAt = 0

/** 找出真正的 DSH 上游并切过去。返回是否找到。3 秒去抖，避免一串失败请求把探测打爆。 */
async function discoverUpstream(why) {
  const now = Date.now()
  if (now - lastDiscoverAt < 3000) return false
  lastDiscoverAt = now
  const candidates = upstreamCandidates()
  // 并发探测（串行时 20+ 个候选要 6 秒以上，DSH 重启后第一次访问会干等）
  const results = await Promise.all(candidates.map((candidate) => probeUpstream(candidate)))
  const hit = candidates.find((candidate, index) => results[index] === true)
  if (hit === undefined) {
    log(`upstream 探测失败：${candidates.length} 个候选里没有 DSH（${why}）`)
    return false
  }
  if (!setUpstream(hit, why)) log(`upstream 确认仍是 ${hit}（${why}）`)
  return true
}
const COOKIE_MAX_AGE_SECONDS = Math.round(cookieDays * 86400)
const LOG_PATH = join(HERE, accessLog)

// ── 移动端适配层 ────────────────────────────────────────────────────────────
// DSH 的 Web GUI 是桌面布局：左侧栏展开时靠 grid 轨道占宽，在 393px 手机上把
// 主区域挤成 113px，输入框每行只剩两个字。这里在代理层往 index.html 注入一份
// CSS + 一小段 JS 把它改成「浮层抽屉」。
//
// 为什么放在代理层而不是改 DSH 源码：
//   1. 用户装的是打包好的 Electron App，前端产物在 app.asar 里，没有源码 checkout；
//   2. 注入只影响手机这条路径，桌面 GUI 一个字节都不动；
//   3. DSH 升级后注入依然生效（选择器用 App 的语义化属性 + CSS-module 的 local 名）。
const MOBILE_CSS_PATH = '/__dsh-mobile/mobile.css'
const MOBILE_JS_PATH = '/__dsh-mobile/mobile.js'
const MOBILE_ASSETS = new Map([
  [MOBILE_CSS_PATH, { file: join(HERE, 'mobile.css'), type: 'text/css; charset=utf-8' }],
  [MOBILE_JS_PATH, { file: join(HERE, 'mobile.js'), type: 'text/javascript; charset=utf-8' }]
])

/**
 * 适配层资源版本号 = 两个文件的 mtime。
 * 为什么需要：手机上页面已经加载后，浏览器里跑的是**旧的 JS**，
 * 改完 mobile.js 刷新前不会生效 —— 会让人误以为修复没起作用。
 * 带上 ?v=<mtime> 后每次改动都换 URL，浏览器必然重新拉取。
 */
function mobileAssetVersion() {
  let stamp = 0
  for (const asset of MOBILE_ASSETS.values()) {
    try {
      stamp = Math.max(stamp, statSync(asset.file).mtimeMs)
    } catch {
      /* 文件缺失时用 0，交给 serveMobileAsset 报 404 */
    }
  }
  return Math.round(stamp).toString(36)
}

/** 把适配层标签插进 index.html 的 </head> 之前。 */
function injectMobileEnhance(html) {
  const version = mobileAssetVersion()
  const tags =
    `<link rel="stylesheet" href="${MOBILE_CSS_PATH}?v=${version}">` +
    `<script defer src="${MOBILE_JS_PATH}?v=${version}"></script>`
  const at = html.indexOf('</head>')
  return at === -1 ? tags + html : `${html.slice(0, at)}${tags}${html.slice(at)}`
}

/** 代理自己吐这两个文件，请求不落到上游 DSH。 */
function serveMobileAsset(res, pathname) {
  const asset = MOBILE_ASSETS.get(pathname)
  if (asset === undefined || !existsSync(asset.file)) {
    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
    res.end('dsh-remote-web: mobile asset missing\n')
    return
  }
  const body = readFileSync(asset.file)
  res.writeHead(200, {
    'content-type': asset.type,
    'content-length': String(body.byteLength),
    // 改完立刻生效，方便迭代；体积只有几 KB
    'cache-control': 'no-cache'
  })
  res.end(body)
}

// ── DSH 浏览器会话 cookie 签发 ──────────────────────────────────────────────
// 复刻 @deepseek-ai/dsh-client-connection 的 browser-auth 格式：
//   cookie 名 = "dsh-auth-" + base64url(sha256(authority))
//   cookie 值 = "v1." + base64url(JSON) + "." + base64url(hmacSha256(secret, body))
//   body     = { version:1, authority, issuedAt, expiresAt }
const b64url = (buf) => Buffer.from(buf).toString('base64').replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/u, '')

function readSessionSecret() {
  const file = join(dshHome, '.credentials.yaml')
  if (!existsSync(file)) throw new Error(`找不到 DSH 凭证文件：${file}（DSH 至少要启动过一次）`)
  const yaml = readFileSync(file, 'utf8')
  const m = /client-connection\/browser-session:[\s\S]*?\bsecret:\s*([A-Za-z0-9_-]+)/u.exec(yaml)
  if (m === null) throw new Error(`${file} 里没有 client-connection/browser-session.secret`)
  const secret = Buffer.from(m[1].replaceAll('-', '+').replaceAll('_', '/'), 'base64')
  if (secret.byteLength !== 32) throw new Error(`browser-session secret 应为 32 字节，实际 ${secret.byteLength}`)
  return { secret, raw: m[1] }
}

let session = readSessionSecret()
let dshCookie = null // { header, expiresAt }

/**
 * 上游回 401 说明我们签的 cookie 已失效（通常是 DSH 的 browser-session 密钥被轮换）。
 * 重新读密钥并作废缓存，让下一个请求用新 cookie；返回变化说明供日志/诊断用。
 */
function refreshSession() {
  dshCookie = null
  try {
    const before = session.raw
    session = readSessionSecret()
    return session.raw === before ? 'secret unchanged, cookie re-minted' : 'secret changed, cookie re-minted'
  } catch (error) {
    return `could not re-read secret: ${error.message}`
  }
}

/** 签发（或复用）上游要用的 DSH cookie。剩余寿命 < 5 天、或上游 authority 变了就重签。 */
function currentDshCookie() {
  const now = Date.now()
  const authority = upstreamState.authority
  if (dshCookie !== null && dshCookie.authority === authority && dshCookie.expiresAt - now > 5 * 86400_000) {
    return dshCookie.header
  }
  const issuedAt = now
  const expiresAt = issuedAt + cookieDays * 86400_000
  // cookie 名与 body 都绑定 authority —— 上游端口一变就必须重签，否则 DSH 会拒
  const name = 'dsh-auth-' + b64url(createHash('sha256').update(authority).digest())
  const body = b64url(Buffer.from(JSON.stringify({ version: 1, authority, issuedAt, expiresAt }), 'utf8'))
  const sig = b64url(createHmac('sha256', session.secret).update(body).digest())
  dshCookie = { header: `${name}=v1.${body}.${sig}`, expiresAt, authority }
  log(`minted DSH session cookie for ${authority}, valid ${cookieDays}d`)
  return dshCookie.header
}

// ── 鉴权 ────────────────────────────────────────────────────────────────────
function safeEqual(a, b) {
  const x = Buffer.from(String(a), 'utf8')
  const y = Buffer.from(String(b), 'utf8')
  return x.byteLength === y.byteLength && timingSafeEqual(x, y)
}

function cookieFrom(headerValue, name) {
  if (typeof headerValue !== 'string') return undefined
  for (const segment of headerValue.split(';')) {
    const at = segment.indexOf('=')
    if (at === -1 || segment.slice(0, at).trim() !== name) continue
    return segment.slice(at + 1).trim()
  }
  return undefined
}

function basicPassword(headerValue) {
  if (typeof headerValue !== 'string' || !headerValue.startsWith('Basic ')) return undefined
  try {
    const decoded = Buffer.from(headerValue.slice(6), 'base64').toString('utf8')
    return decoded.slice(decoded.indexOf(':') + 1)
  } catch {
    return undefined
  }
}

/** 三通道：?token= / cookie / Basic（密码填令牌）。返回 'token' | 'cookie' | 'basic' | undefined */
function authenticate(req, url) {
  const tokens = url.searchParams.getAll('token')
  if (tokens.length === 1 && safeEqual(tokens[0], token)) return 'token'
  if (safeEqual(cookieFrom(req.headers.cookie, cookieName) ?? '', token)) return 'cookie'
  if (safeEqual(basicPassword(req.headers.authorization) ?? '', token)) return 'basic'
  return undefined
}

// ── 日志 ────────────────────────────────────────────────────────────────────
function log(line) {
  const stamped = `[${new Date().toISOString()}] ${line}`
  console.log(stamped)
  try {
    appendFileSync(LOG_PATH, stamped + '\n')
  } catch {
    /* 日志失败不影响服务 */
  }
}

/** 日志里绝不落令牌原文 */
function redact(url) {
  return url.replace(/([?&]token=)[^&]*/gu, '$1<redacted>')
}

// ── 转发：把 Host/Origin 规范成 loopback，并注入 DSH cookie ─────────────────
function upstreamHeaders(req) {
  const headers = { ...req.headers }
  delete headers.authorization // 那是本代理的凭证，不往上游传
  delete headers.cookie // 换成我们签发的 DSH cookie
  for (const hop of ['connection', 'keep-alive', 'proxy-authorization', 'proxy-connection', 'te', 'trailer', 'upgrade']) {
    delete headers[hop]
  }
  // 请求体：有 content-length 就保留；分块传输则交给 Node 自己重新分块
  if (headers['content-length'] === undefined) delete headers['transfer-encoding']
  headers.host = upstreamState.authority
  if (headers.origin !== undefined) headers.origin = upstreamState.origin
  if (typeof headers.referer === 'string') headers.referer = headers.referer.replace(/^https?:\/\/[^/]+/u, upstreamState.origin)
  // 要改写 index.html 就得拿到明文：只对文档导航关掉压缩，
  // 静态资源（JS/CSS 几百 KB）继续走 gzip。
  if (mobileEnhance && typeof headers.accept === 'string' && headers.accept.includes('text/html')) {
    headers['accept-encoding'] = 'identity'
  }
  headers.cookie = currentDshCookie()
  return headers
}

function responseHeaders(upstreamRes) {
  const headers = { ...upstreamRes.headers }
  for (const hop of ['connection', 'keep-alive', 'proxy-authenticate', 'te', 'trailer', 'transfer-encoding', 'upgrade']) {
    delete headers[hop]
  }
  return headers
}

async function proxyHttp(req, res, url, retried = false) {
  const upstreamReq = httpRequest(
    {
      host: upstreamState.host,
      port: Number(upstreamState.port),
      method: req.method,
      path: url.pathname + url.search,
      headers: upstreamHeaders(req)
    },
    (upstreamRes) => {
      // 我们签的 cookie 被上游拒了：不要把这个裸 401 透给手机（会显示成
      // DSH 那句让人摸不着头脑的 "reopen the URL printed by dsh web"），
      // 而是给出可操作的诊断，并顺手换一份新 cookie。
      if (upstreamRes.statusCode === 401) {
        upstreamRes.resume()
        const detail = refreshSession()
        log(`upstream 401 on ${req.method} ${redact(req.url ?? '/')} -> ${detail}`)
        if (res.headersSent) {
          res.destroy()
          return
        }
        res.writeHead(502, { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' })
        res.end(
          'dsh-remote-web: DSH 拒绝了代理注入的会话 cookie。\n' +
            `已自动重新读取密钥（${detail}）。请刷新页面重试；若持续失败，重启代理进程。\n`
        )
        return
      }
      // 文档导航：缓冲整页 HTML，注入移动端适配层后再发。
      // 只对 text/html 这么做，且此时上游是明文（见 upstreamHeaders）。
      const contentType = String(upstreamRes.headers['content-type'] ?? '')
      if (mobileEnhance && contentType.includes('text/html')) {
        const chunks = []
        upstreamRes.on('data', (chunk) => chunks.push(chunk))
        upstreamRes.on('end', () => {
          const html = injectMobileEnhance(Buffer.concat(chunks).toString('utf8'))
          const body = Buffer.from(html, 'utf8')
          const headers = responseHeaders(upstreamRes)
          delete headers['content-encoding'] // 已解压/明文，长度也变了
          headers['content-length'] = String(body.byteLength)
          res.writeHead(upstreamRes.statusCode ?? 502, headers)
          res.end(body)
        })
        upstreamRes.on('error', () => res.destroy())
        return
      }
      res.socket?.setNoDelay(true)
      res.writeHead(upstreamRes.statusCode ?? 502, responseHeaders(upstreamRes))
      upstreamRes.pipe(res)
    }
  )
  upstreamReq.on('error', async (error) => {
    log(`upstream error: ${error.message}`)
    // 连不上上游，多半是 DSH 重启后换了端口：探测一次再重试。
    // 只重试「没有请求体」的请求（GET/HEAD 等），否则重发会把 body 丢掉。
    const bodyless =
      req.method === 'GET' ||
      req.method === 'HEAD' ||
      (req.headers['content-length'] === undefined && req.headers['transfer-encoding'] === undefined)
    if (!retried && bodyless && (await discoverUpstream(`连接失败：${error.message}`)) && !res.headersSent) {
      proxyHttp(req, res, url, true)
      return
    }
    if (res.headersSent) {
      res.destroy()
      return
    }
    res.writeHead(502, { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' })
    res.end(
      `dsh-remote-web: 连不上上游 DSH（${upstreamState.authority}）：${error.message}\n` +
        '如果 DSH 刚重启过，代理会自动探测它的新端口；仍失败就重启代理进程（start-all.bat / ./mac/start.sh）。\n'
    )
  })
  req.pipe(upstreamReq)
  res.on('close', () => upstreamReq.destroy())
}

// ── WebSocket / 任意 upgrade：原样双向管道（DSH 的 /api/remote.mux 走这里）──
function proxyUpgrade(req, socket, head) {
  const headers = { ...req.headers, host: upstreamState.authority, cookie: currentDshCookie() }
  delete headers.authorization
  if (headers.origin !== undefined) headers.origin = upstreamState.origin

  const upstreamSocket = netConnect(Number(upstreamState.port), upstreamState.host, () => {
    let raw = `${req.method} ${req.url} HTTP/1.1\r\n`
    for (const [key, value] of Object.entries(headers)) {
      if (Array.isArray(value)) for (const item of value) raw += `${key}: ${item}\r\n`
      else if (value !== undefined) raw += `${key}: ${value}\r\n`
    }
    raw += '\r\n'
    upstreamSocket.write(raw)
    if (head !== undefined && head.length > 0) upstreamSocket.write(head)
    upstreamSocket.pipe(socket)
    socket.pipe(upstreamSocket)
  })
  const fail = (error) => {
    log(`upgrade error: ${error.message}`)
    // WebSocket 不好中途重试（握手已完成），这里只探测新端口，让下一次连接用对
    void discoverUpstream(`upgrade 连接失败：${error.message}`)
    socket.destroy()
    upstreamSocket.destroy()
  }
  upstreamSocket.on('error', fail)
  socket.on('error', fail)
  socket.on('close', () => upstreamSocket.destroy())
  upstreamSocket.on('close', () => socket.destroy())
}

// ── HTTP 入口 ───────────────────────────────────────────────────────────────
const server = createServer((req, res) => {
  const url = new URL(req.url ?? '/', 'http://dsh-remote.invalid')
  const mode = authenticate(req, url)

  if (mode === undefined) {
    log(`401 ${req.method} ${redact(req.url ?? '/')} from ${req.socket.remoteAddress ?? '?'}`)
    res.writeHead(401, {
      'cache-control': 'no-store',
      'content-type': 'text/plain; charset=utf-8',
      'www-authenticate': `Basic realm="dsh-remote", charset="UTF-8"`
    })
    res.end('dsh-remote-web: 需要令牌。\n手机请用带 ?token=... 的地址访问一次（之后靠 Cookie，可收藏干净地址）。\n')
    return
  }

  // 带令牌访问：种下长期 Cookie 并 303 跳到不含令牌的干净地址
  if (mode === 'token') {
    url.searchParams.delete('token')
    const target = url.pathname + (url.search === '?' ? '' : url.search)
    log(`302 token-exchange -> ${redact(target)} from ${req.socket.remoteAddress ?? '?'}`)
    res.writeHead(303, {
      'cache-control': 'no-store',
      location: target === '' ? '/' : target,
      'referrer-policy': 'no-referrer',
      'set-cookie': `${cookieName}=${token}; Max-Age=${COOKIE_MAX_AGE_SECONDS}; Path=/; HttpOnly; SameSite=Strict`
    })
    res.end()
    return
  }

  // 适配层资源由代理直接吐，不落到上游（同样要求已鉴权）
  if (mobileEnhance && MOBILE_ASSETS.has(url.pathname)) {
    serveMobileAsset(res, url.pathname)
    return
  }

  proxyHttp(req, res, url)
})

server.on('upgrade', (req, socket, head) => {
  const url = new URL(req.url ?? '/', 'http://dsh-remote.invalid')
  if (authenticate(req, url) === undefined) {
    log(`401 upgrade ${redact(req.url ?? '/')} from ${req.socket.remoteAddress ?? '?'}`)
    socket.write('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
    socket.destroy()
    return
  }
  proxyUpgrade(req, socket, head)
})

server.on('error', (error) => {
  log(`listen error: ${error.message}`)
  process.exitCode = 1
})

server.listen(listenPort, listenHost, async () => {
  log(`dsh-remote-web listening on http://${listenHost}:${listenPort}`)
  log(`DSH home: ${dshHome}`)
  if (publicBaseUrl !== '') log(`public entry (via tunnel): ${publicBaseUrl}/?token=<token>`)
  // 首选上游探一下：DSH 的端口是动态的，过期了就在这里换掉（否则第一次访问会 502）
  const preferred = upstreamState.authority
  if (await probeUpstream(preferred)) {
    log(`upstream 可用：${preferred}`)
  } else {
    log(`upstream ${preferred} 不可用，开始探测 DSH 的真实端口…`)
    await discoverUpstream('启动时首选上游不可用')
  }
  currentDshCookie() // 用最终确定的 authority 签发一次，早点暴露配置错误
})
