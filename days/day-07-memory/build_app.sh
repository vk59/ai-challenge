#!/bin/bash
#
# Собирает macOS-приложение «День 7.app» — агент, который помнит диалог
# между запусками.
#
#   ./build_app.sh          собрать
#   open "dist/День 7.app"  запустить
#
# Устройство то же, что в дне 6 (см. его build_app.sh): рантайм и .env живут
# в ~/Library/Application Support, потому что macOS не пускает приложения
# в ~/Documents без явного разрешения TCC.
#
# Что добавилось в дне 7: хранилище диалогов. Оно не может лежать внутри
# бандла — пересборка стёрла бы всю переписку, — и не может лежать в репозитории
# по той же причине TCC. Поэтому уезжает туда же, в Application Support,
# и путь передаётся через AI_ADVENT_MEMORY_DIR.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SUPPORT="$HOME/Library/Application Support/AI Advent"
VENV="$SUPPORT/venv"
MEMORY="$SUPPORT/memory"
APP="$HERE/dist/День 7.app"

echo "→ Рантайм"
if [ ! -x "$VENV/bin/python" ]; then
  echo "  создаю venv в $VENV"
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
cp "$ROOT"/shared/agent.py "$ROOT"/shared/llm.py "$ROOT"/shared/memory.py "$ROOT"/shared/tokens.py \
   "$APP/Contents/Resources/"
echo "  код скопирован внутрь"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>                <string>День 7</string>
  <key>CFBundleDisplayName</key>         <string>День 7 — память между запусками</string>
  <key>CFBundleIdentifier</key>          <string>local.aiadvent.day07</string>
  <key>CFBundleExecutable</key>          <string>launcher</string>
  <key>CFBundleIconFile</key>            <string>icon</string>
  <key>CFBundlePackageType</key>         <string>APPL</string>
  <key>CFBundleVersion</key>             <string>1.0</string>
  <key>CFBundleShortVersionString</key>  <string>1.0</string>
  <key>LSMinimumSystemVersion</key>      <string>11.0</string>
  <key>NSHighResolutionCapable</key>     <true/>
  <!-- окно ходит в собственный сервер на 127.0.0.1 по http -->
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST

echo "→ Настройки"
if [ -f "$ROOT/.env" ]; then
  cp "$ROOT/.env" "$SUPPORT/.env"
  chmod 600 "$SUPPORT/.env"
  echo "  .env скопирован в $SUPPORT"
else
  echo "  .env в репозитории не найден — приложение скажет об этом при запросе"
fi

mkdir -p "$MEMORY"
echo "  хранилище диалогов: $MEMORY"
if [ -e "$MEMORY/agent.db" ]; then
  echo "  прошлые диалоги на месте — пересборка их не трогает"
fi

cat > "$APP/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/sh
# .env и история лежат рядом с рантаймом: в ~/Documents приложение не пускают.
# История принципиально снаружи бандла — иначе ./build_app.sh стирал бы память.
AI_ADVENT_ENV_FILE="$SUPPORT/.env"
AI_ADVENT_MEMORY_DIR="$MEMORY"
export AI_ADVENT_ENV_FILE AI_ADVENT_MEMORY_DIR
RESOURCES="\$(cd "\$(dirname "\$0")/../Resources" && pwd)"
exec "$VENV/bin/python" "\$RESOURCES/app.py"
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

# Подпись «для себя»: без неё Gatekeeper ругается на неопознанное приложение.
codesign --force --sign - "$APP" 2>/dev/null && echo "  подписано ad-hoc"

touch "$APP"   # иначе Finder может показывать иконку из кеша

echo
echo "Готово: $APP"
echo "Запустить:  open \"$APP\""
echo
echo "Проверка задания: написать что-нибудь, закрыть на ⌘Q, открыть снова —"
echo "диалог должен оказаться на месте."
echo
echo "После правок в коде или смены ключа в .env — пересобрать: ./build_app.sh"
