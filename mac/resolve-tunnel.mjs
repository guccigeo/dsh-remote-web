/**
 * 解析反向隧道参数，打印成一行空格分隔的值供 shell 读取：
 *   <host> <remotePort> <localPort> <identityFile>
 *
 * 放在独立文件里而不是塞进 `sh -c "node -e '...'"`，是为了避开 shell 引号地狱。
 *
 * 解析顺序（都能从 remote.config.json 推导，Mac 上一般只需补 identityFile）：
 *   host         tunnel.host  →  publicBaseUrl 的主机名（前缀 root@）
 *   remotePort   tunnel.remotePort  →  publicBaseUrl 的端口  →  17933
 *   localPort    tunnel.localPort  →  listenPort  →  19390
 *   identityFile tunnel.identityFile  →  ~/.ssh/id_ed25519（~ 展开为 $HOME）
 */
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))

let config = {}
try {
  config = JSON.parse(readFileSync(join(ROOT, 'remote.config.json'), 'utf8'))
} catch (error) {
  console.error(`resolve-tunnel: 读不到 ${join(ROOT, 'remote.config.json')}：${error.message}`)
  process.exit(1)
}

const tunnel = config.tunnel ?? {}

let base = null
try {
  base = new URL(config.publicBaseUrl ?? '')
} catch {
  base = null
}

const host = tunnel.host ?? (base?.hostname ? `root@${base.hostname}` : 'root@127.0.0.1')
const remotePort = String(tunnel.remotePort ?? base?.port ?? 17933)
const localPort = String(tunnel.localPort ?? config.listenPort ?? 19390)
const identityFile = String(tunnel.identityFile ?? '~/.ssh/id_ed25519').replace(/^~(?=\/|$)/, homedir())

process.stdout.write(`${host} ${remotePort} ${localPort} ${identityFile}\n`)
