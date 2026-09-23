#!/bin/sh
# 登录自启（macOS LaunchAgent）。
#
# 用法:
#   ./autostart.sh install     安装 plist 并立即加载（等价于 Windows 的启动文件夹快捷方式）
#   ./autostart.sh uninstall   卸载并删除 plist
#   ./autostart.sh status      查看是否已加载
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
LABEL="com.dsh.remote-web"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM=$(id -u)

install_agent() {
  mkdir -p "$HOME/Library/LaunchAgents"
  cat >"$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>$HERE/start.sh</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$HERE/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$HERE/launchd.err.log</string>
</dict>
</plist>
PLIST_EOF

  # 先卸旧的再装，否则 bootstrap 会因已存在而报错
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null ||
    launchctl unload "$PLIST" 2>/dev/null || true

  if launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/dev/null; then
    echo "已安装并加载: $PLIST"
  elif launchctl load -w "$PLIST" 2>/dev/null; then
    echo "已安装并加载（legacy load）: $PLIST"
  else
    echo "plist 已写入 $PLIST，但 launchctl 加载失败。手动执行：" >&2
    echo "  launchctl load -w \"$PLIST\"" >&2
    exit 1
  fi
  echo "查看日志: $HERE/launchd.err.log"
}

uninstall_agent() {
  launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null ||
    launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "已卸载并删除 $PLIST"
  echo "（自启已禁止；当前正在跑的进程请另跑 ./stop.sh）"
}

status_agent() {
  if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
    echo "已加载: $LABEL"
  else
    echo "未加载: $LABEL"
  fi
  [ -f "$PLIST" ] && echo "plist: $PLIST" || echo "plist 不存在"
}

case "${1:-}" in
  install) install_agent ;;
  uninstall) uninstall_agent ;;
  status) status_agent ;;
  *)
    echo "用法: $0 [install|uninstall|status]" >&2
    exit 1
    ;;
esac
