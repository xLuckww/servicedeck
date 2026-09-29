#!/bin/bash
# 生成 ServiceDeck.app（轻量包装：Info.plist + 启动脚本 + 图标），默认装到 /Applications。
# App 运行时直接使用本项目的代码和 .venv，所以改代码后只需在菜单里"退出"再重新打开 App，不用重新安装。
# 代价：本项目文件夹不能移动或删除（移动后重新运行本脚本即可）。
#
# 用法：scripts/install_app.sh
# 测试用参数：--dest 目录  --name 名称  --id 标识  --home 配置目录（写进启动脚本的 SERVICEDECK_HOME）  --test-hook
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="/Applications"
NAME="ServiceDeck"
BUNDLE_ID="local.servicedeck"
SD_HOME=""
TEST_HOOK=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --id) BUNDLE_ID="$2"; shift 2 ;;
    --home) SD_HOME="$2"; shift 2 ;;
    --test-hook) TEST_HOOK=1; shift ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

PY="$PROJECT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "找不到 $PY，请先在项目文件夹里运行 uv sync" >&2
  exit 1
fi
if [ ! -f "$PROJECT/assets/icon.png" ]; then
  echo "找不到图标 assets/icon.png" >&2
  exit 1
fi

APP="$DEST/$NAME.app"
if [ -e "$APP" ]; then
  # 只替换自己生成的 App，防止误删同名的其他程序
  OLD_ID=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$APP/Contents/Info.plist" 2>/dev/null || true)
  if [ "$OLD_ID" != "$BUNDLE_ID" ]; then
    echo "$APP 已存在且不是 ServiceDeck 生成的（标识：${OLD_ID:-未知}），不覆盖" >&2
    exit 1
  fi
  rm -rf "$APP"
fi

VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$PROJECT/pyproject.toml" | head -1)
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>ServiceDeck</string>
  <key>CFBundleIconFile</key><string>ServiceDeck</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

# 图标：assets/icon.png → .icns（系统自带的 sips 和 iconutil）
ICONSET="$(mktemp -d /tmp/sdiconXXXX)/ServiceDeck.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$PROJECT/assets/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$PROJECT/assets/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/ServiceDeck.icns"
rm -rf "$(dirname "$ICONSET")"

# App 的启动程序就是 Python 本体（2026-09-28 实测）：若用启动脚本再 exec 成 Python，新版 macOS 会把菜单栏图标藏起来，
# 系统设置 → 菜单栏里也看不到它。本机的 Python（uv 安装的独立版本）不依赖外部动态库，可以直接复制；换了 Python 版本后重新运行本脚本。
REAL_PY="$(/usr/bin/python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$PROJECT/.venv/bin/python")"
PYLIB="$(basename "$(ls -d "$PROJECT"/.venv/lib/python3.* | head -1)")"
cp "$REAL_PY" "$APP/Contents/MacOS/ServiceDeck"
chmod 755 "$APP/Contents/MacOS/ServiceDeck"
# 让这个 Python 把 .app 包当作自己的虚拟环境：标准库用原来的，包里只放引导文件
sed "s#^home = .*#home = $(dirname "$REAL_PY")#" "$PROJECT/.venv/pyvenv.cfg" > "$APP/Contents/pyvenv.cfg"
SITE_DIR="$APP/Contents/lib/$PYLIB/site-packages"
mkdir -p "$SITE_DIR"

if [ -n "$SD_HOME" ]; then LOG_DIR="$SD_HOME/logs"; else LOG_DIR="~/Library/Logs/ServiceDeck"; fi
/usr/bin/python3 - "$PROJECT/scripts/app_bootstrap.py" "$SITE_DIR/servicedeck_bootstrap.py" \
  "$PROJECT" "$PROJECT/.venv/lib/$PYLIB/site-packages" "$LOG_DIR" "$SD_HOME" "$TEST_HOOK" <<'PY'
import sys
src, dst, project, site_packages, log_dir, sd_home, test_hook = sys.argv[1:]
extra_env = {}
if sd_home:
    extra_env["SERVICEDECK_HOME"] = sd_home  # 测试用：配置目录指向临时目录
if test_hook:
    extra_env["SERVICEDECK_TEST_HOOK"] = "1"
text = open(src, encoding="utf-8").read()
for key, value in (("__PROJECT__", project), ("__SITE_PACKAGES__", site_packages), ("__LOG_DIR__", log_dir)):
    text = text.replace(key, value)
text = text.replace("__EXTRA_ENV__", repr(extra_env))
open(dst, "w", encoding="utf-8").write(text)
PY
echo "import servicedeck_bootstrap" > "$SITE_DIR/zz_servicedeck_bootstrap.pth"

# 让系统（访达、open -a、登录项）认识这个 App
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" >/dev/null 2>&1 || true

echo "已安装：$APP"
