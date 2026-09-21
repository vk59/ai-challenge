#!/bin/bash
#
# Собирает «День 13.app» — задача как конечный автомат.
#
#   ./build_app.sh
#   open "dist/День 13.app"
#
# Устройство как в днях 6-12: рантайм, .env и память живут в
# ~/Library/Application Support. Состояние задач лежит там же и переживает
# пересборку — иначе «пауза и продолжение» ломались бы от каждой сборки.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SUPPORT="$HOME/Library/Application Support/AI Advent"
VENV="$SUPPORT/venv"
MEMORY="$SUPPORT/memory"
APP="$HERE/dist/День 13.app"

echo "→ Рантайм"
if [ ! -x "$VENV/bin/python" ]; then
  mkdir -p "$SUPPORT"
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet pyobjc-framework-WebKit
echo "  PyObjC на месте"

echo "→ Иконка"
ICONSET_DIR="$(mktemp -d)"
"$VENV/bin/python" "$HERE/make_icon.py" "$ICONSET_DIR/icon.iconset"

echo "→ Бандл"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
iconutil --convert icns "$ICONSET_DIR/icon.iconset" --output "$APP/Contents/Resources/icon.icns"
rm -rf "$ICONSET_DIR"

cp "$HERE"/app.py "$HERE"/web.py "$HERE"/ui.html "$APP/Contents/Resources/"
cp "$ROOT"/shared/agent.py "$ROOT"/shared/llm.py "$ROOT"/shared/memory.py \
   "$ROOT"/shared/tokens.py "$APP/Contents/Resources/"
echo "  код скопирован внутрь"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>                <string>День 13</string>
  <key>CFBundleDisplayName</key>         <string>День 13 — состояние задачи</string>
  <key>CFBundleIdentifier</key>          <string>local.aiadvent.day13</string>
  <key>CFBundleExecutable</key>          <string>launcher</string>
  <key>CFBundleIconFile</key>            <string>icon</string>
  <key>CFBundlePackageType</key>         <string>APPL</string>
  <key>CFBundleVersion</key>             <string>1.0</string>
  <key>CFBundleShortVersionString</key>  <string>1.0</string>
  <key>LSMinimumSystemVersion</key>      <string>11.0</string>
  <key>NSHighResolutionCapable</key>     <true/>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST

echo "→ Настройки"
if [ -f "$ROOT/.env" ]; then
  cp "$ROOT/.env" "$SUPPORT/.env"
  chmod 600 "$SUPPORT/.env"
  echo "  .env скопирован"
fi
mkdir -p "$MEMORY"
echo "  память и состояния задач: $MEMORY"

cat > "$APP/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/sh
AI_ADVENT_ENV_FILE="$SUPPORT/.env"
AI_ADVENT_MEMORY_DIR="$MEMORY"
export AI_ADVENT_ENV_FILE AI_ADVENT_MEMORY_DIR
RESOURCES="\$(cd "\$(dirname "\$0")/../Resources" && pwd)"
exec "$VENV/bin/python" "\$RESOURCES/app.py"
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

codesign --force --sign - "$APP" 2>/dev/null && echo "  подписано ad-hoc"
touch "$APP"

echo
echo "Готово: $APP"
echo "Запустить:  open \"$APP\""
echo
echo "Проверка дня: дойдите до этапа «выполнение», закройте на ⌘Q,"
echo "откройте снова — этап и шаг на месте, объяснять заново ничего не надо."
