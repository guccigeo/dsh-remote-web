#!/bin/sh
# 停止 DSH 远程 Web 代理与反向隧道（macOS / Linux）。
#
# 先杀监管循环、再杀子进程 —— 反过来的话监管器会在 5 秒后把子进程重新拉起。
# 只动本功能的进程（靠 PID 文件），不会影响机器上其它 ssh 隧道。
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)

stopped=0

stop_pidfile() {
  file="$ROOT/$1"
  [ -f "$file" ] || return 0
  pid=$(tr -d '[:space:]' <"$file" 2>/dev/null || true)
  case "$pid" in
    '' | *[!0-9]*)
      echo "$1: PID 文件内容不可读，删除"
      rm -f "$file"
      return 0
      ;;
  esac
  if kill -0 "$pid" 2>/dev/null; then
    echo "停止 $1 -> PID $pid"
    kill "$pid" 2>/dev/null
    # 给 2 秒优雅退出，再强杀
    n=0
    while [ "$n" -lt 4 ] && kill -0 "$pid" 2>/dev/null; do
      sleep 0.5
      n=$((n + 1))
    done
    kill -9 "$pid" 2>/dev/null
    stopped=$((stopped + 1))
  else
    echo "$1: PID $pid 已不存在"
  fi
  rm -f "$file"
}

# 监管器优先
stop_pidfile proxy.launcher.pid
stop_pidfile tunnel.launcher.pid
stop_pidfile proxy.pid
stop_pidfile tunnel.pid

if [ "$stopped" -eq 0 ]; then
  echo "没有正在运行的 dsh-remote-web 进程。"
else
  echo "已停止。"
fi
