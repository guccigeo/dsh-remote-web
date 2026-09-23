#!/bin/sh
# DSH 远程 Web 控制 —— macOS / Linux 启动脚本
#
# 用法:
#   ./start.sh              启动代理 + 反向隧道（后台，各自带监管自动重启）
#   ./start.sh foreground   前台跑代理（排障用，Ctrl-C 退出）
#   ./start.sh proxy        只跑代理监管循环（内部用）
#   ./start.sh tunnel       只跑隧道监管循环（内部用）
#
# 与 Windows 版等价：run-proxy.ps1 + run-tunnel.ps1，
# 同样写 proxy.pid / tunnel.pid / *.launcher.pid，stop.sh 靠这些 PID 文件停。
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
SELF="$HERE/$(basename "$0")"

# 先存角色 —— 下面 `set --` 会覆盖 $1，不先存的话 case 会拿到主机名
ROLE="${1:-all}"

if ! command -v node >/dev/null 2>&1; then
  echo "[dsh-remote-web] 找不到 node，请先安装 Node.js（brew install node）" >&2
  exit 1
fi

# 隧道参数：host remotePort localPort identityFile
# 注意必须先把命令替换存进变量再 set --，否则 node 失败时 set -- 仍返回 0，
# `|| exit 1` 抓不到。
TN=$(node "$HERE/resolve-tunnel.mjs") || exit 1
set -- $TN
TN_HOST=$1
TN_REMOTE_PORT=$2
TN_LOCAL_PORT=$3
TN_KEY=$4

SSH_BIN=$(command -v ssh || echo /usr/bin/ssh)

proxy_loop() {
  cd "$ROOT" || exit 1
  echo $$ >"$ROOT/proxy.launcher.pid"
  while :; do
    node "$ROOT/dsh-remote-web.mjs" >>"$ROOT/proxy.out.log" 2>&1 &
    child=$!
    echo "$child" >"$ROOT/proxy.pid"
    wait "$child"
    echo "[dsh-remote-web] 代理退出，5 秒后重启" >>"$ROOT/proxy.out.log"
    sleep 5
  done
}

tunnel_loop() {
  echo $$ >"$ROOT/tunnel.launcher.pid"
  tries=0
  while :; do
    "$SSH_BIN" -N \
      -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=25 \
      -o ServerAliveCountMax=4 \
      -o StrictHostKeyChecking=accept-new \
      -o BatchMode=yes \
      -i "$TN_KEY" \
      -R "0.0.0.0:${TN_REMOTE_PORT}:127.0.0.1:${TN_LOCAL_PORT}" \
      "$TN_HOST" >>"$ROOT/tunnel.out.log" 2>&1 &
    child=$!
    echo "$child" >"$ROOT/tunnel.pid"
    wait "$child"
    tries=$((tries + 1))
    if [ "$tries" -lt 5 ]; then sleep 5; else sleep 60; fi
  done
}

case "$ROLE" in
  proxy) proxy_loop ;;
  tunnel) tunnel_loop ;;
  foreground) cd "$ROOT" && exec node "$ROOT/dsh-remote-web.mjs" ;;
  all)
    echo "隧道目标: $TN_HOST  远端端口 $TN_REMOTE_PORT  本地端口 $TN_LOCAL_PORT  密钥 $TN_KEY"
    nohup "$SELF" proxy >>"$ROOT/launcher.log" 2>&1 &
    nohup "$SELF" tunnel >>"$ROOT/launcher.log" 2>&1 &
    sleep 2
    echo "已启动。手机访问地址见 README.md；停止用 ./stop.sh"
    ;;
  *)
    echo "用法: $0 [all|foreground|proxy|tunnel]" >&2
    exit 1
    ;;
esac
