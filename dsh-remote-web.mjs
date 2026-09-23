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
 * 上游 DSH 的 host:port。
 * 顺序：配置里的 upstream → 环境变量 DSH_WEB_URL（DSH 会把它注入 shell 环境；
 * macOS 上端口不一定和 Windows 一致，从 DSH 里启动代理时这个最准）→ 默认值。
 */
function resolveUpstream(configured) {
  if (typeof configured === 'string' && configured.trim() !== '') return configured.trim()
  const fromEnv = process.env.DSH_WEB_URL
  if (typeof fromEnv === 'string' && fromEnv !== '') {
    try {
      const url = new URL(fromEnv)
      if (url.port !== '') return `${url.hostname}:${url.port}`
    } catch {
      /* 非法值就当没设 */
    }
  }
  return '127.0.0.1:19387'
}

const UPSTREAM = resolveUpstream(upstream)
const [UPSTREAM_HOST, UPSTREAM_PORT] = UPSTREAM.split(':')
const UPSTREAM_AUTHORITY = `${UPSTREAM_HOST}:${UPSTREAM_PORT}` // 同时用作 Host 与 cookie 的 audience
const UPSTREAM_ORIGIN = `http://${UPSTREAM_AUTHORITY}`
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

/** 签发（或复用）上游要用的 DSH cookie。剩余寿命 < 5 天时重新签发。 */
function currentDshCookie() {
  const now = Date.now()
  if (dshCookie !== null && dshCookie.expiresAt - now > 5 * 86400_000) return dshCookie.header
  const issuedAt = now
  const expiresAt = issuedAt + cookieDays * 86400_000
  const name = 'dsh-auth-' + b64url(createHash('sha256').update(UPSTREAM_AUTHORITY).digest())
  const body = b64url(Buffer.from(JSON.stringify({ version: 1, authority: UPSTREAM_AUTHORITY, issuedAt, expiresAt }), 'utf8'))
  const sig = b64url(createHmac('sha256', session.secret).update(body).digest())
  dshCookie = { header: `${name}=v1.${body}.${sig}`, expiresAt }
  log(`minted DSH session cookie for ${UPSTREAM_AUTHORITY}, valid ${cookieDays}d`)
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
  headers.host = UPSTREAM_AUTHORITY
  if (headers.origin !== undefined) headers.origin = UPSTREAM_ORIGIN
  if (typeof headers.referer === 'string') headers.referer = headers.referer.replace(/^https?:\/\/[^/]+/u, UPSTREAM_ORIGIN)
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

function proxyHttp(req, res, url) {
  const upstreamReq = httpRequest(
    {
      host: UPSTREAM_HOST,
      port: Number(UPSTREAM_PORT),
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
  upstreamReq.on('error', (error) => {
    log(`upstream error: ${error.message}`)
    if (res.headersSent) {
      res.destroy()
      return
    }
    res.writeHead(502, { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' })
    res.end(`dsh-remote-web: 连不上上游 DSH（${UPSTREAM_AUTHORITY}）：${error.message}\n请确认 DSH 正在运行。\n`)
  })
  req.pipe(upstreamReq)
  res.on('close', () => upstreamReq.destroy())
}

// ── WebSocket / 任意 upgrade：原样双向管道（DSH 的 /api/remote.mux 走这里）──
function proxyUpgrade(req, socket, head) {
  const headers = { ...req.headers, host: UPSTREAM_AUTHORITY, cookie: currentDshCookie() }
  delete headers.authorization
  if (headers.origin !== undefined) headers.origin = UPSTREAM_ORIGIN

  const upstreamSocket = netConnect(Number(UPSTREAM_PORT), UPSTREAM_HOST, () => {
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

server.listen(listenPort, listenHost, () => {
  log(`dsh-remote-web listening on http://${listenHost}:${listenPort} -> ${UPSTREAM_ORIGIN}`)
  log(`DSH home: ${dshHome}`)
  if (publicBaseUrl !== '') log(`public entry (via tunnel): ${publicBaseUrl}/?token=<token>`)
  currentDshCookie() // 启动即签发一次，早点暴露配置错误
})
