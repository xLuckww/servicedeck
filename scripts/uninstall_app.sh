#!/bin/bash
# 卸载 ServiceDeck.app：删除 /Applications/ServiceDeck.app 和"登录时启动"的配置。
# 不会删除服务清单（~/.config/servicedeck/）和日志（~/Library/Logs/ServiceDeck/），也不会停止正在运行的服务。
# 如需同时停止所有服务：先在项目文件夹里运行 uv run servicedeck down
set -euo pipefail

APP="/Applications/ServiceDeck.app"
PLIST="$HOME/Library/LaunchAgents/local.servicedeck.plist"

if pgrep -f "ServiceDeck.app/Contents/MacOS/ServiceDeck" >/dev/null; then
  echo "ServiceDeck 还在运行，请先点菜单栏 SD 图标 →「退出 ServiceDeck」" >&2
  exit 1
fi
if [ -d "$APP" ]; then
  ID=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$APP/Contents/Info.plist" 2>/dev/null || true)
  if [ "$ID" != "local.servicedeck" ]; then
    echo "$APP 不是 ServiceDeck 生成的，不删除" >&2
    exit 1
  fi
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u "$APP" >/dev/null 2>&1 || true
  rm -rf "$APP"
  echo "已删除 $APP"
fi
if [ -f "$PLIST" ]; then
  rm -f "$PLIST"
  echo "已删除登录项 $PLIST"
fi
echo "卸载完成。服务清单和日志都保留着。"
