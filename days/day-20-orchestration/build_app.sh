#!/bin/bash
#
# Собирает «День 20.app» — оркестрация трёх MCP-серверов.
#
#   ./build_app.sh
#   open "dist/День 20.app"
#
# Особенность против прошлых дней: внутрь бандла едет ещё и MCP-сервер
# (server.py), потому что приложение запускает его как дочерний процесс.
# Забыть его — значит получить приложение без инструментов.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SUPPORT="$HOME/Library/Application Support/AI Advent"
VENV="$SUPPORT/venv"
MEMORY="$SUPPORT/memory"
APP="$HERE/dist/День 20.app"

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

cp "$HERE"/app.py "$HERE"/web.py "$HERE"/ui.html "$HERE"/routing.py \
   "$HERE"/flow.py "$APP/Contents/Resources/"

# Три MCP-сервера живут в папках своих дней — копируем их, а не переписываем.
# Имена разводим, потому что внутри бандла всё лежит в одной папке;
# routing.py ищет их именно под этими именами.
cp "$ROOT"/days/day-17-mcp-tool/server.py "$APP/Contents/Resources/server_git.py"
cp "$ROOT"/days/day-18-scheduler/server.py "$APP/Contents/Resources/server_sched.py"
cp "$ROOT"/days/day-19-pipeline/server.py "$APP/Contents/Resources/server_pipe.py"

# scheduler.py и artifacts.py нужны серверам 18 и 19 соответственно.
cp "$ROOT"/shared/agent.py "$ROOT"/shared/llm.py "$ROOT"/shared/memory.py \
   "$ROOT"/shared/tokens.py "$ROOT"/shared/mcp.py "$ROOT"/shared/tools.py \
   "$ROOT"/shared/artifacts.py "$ROOT"/shared/scheduler.py \
   "$APP/Contents/Resources/"
echo "  код и три MCP-сервера скопированы внутрь"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>                <string>День 20</string>
  <key>CFBundleDisplayName</key>         <string>День 20 — диспетчер серверов</string>
  <key>CFBundleIdentifier</key>          <string>local.aiadvent.day20</string>
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

echo "→ Зеркало репозитория"
# macOS не даёт приложению, запущенному из Finder, читать ~/Documents:
# git падает с «Operation not permitted», и выпросить доступ из кода нельзя.
# Поэтому кладём bare-зеркало рядом с рантаймом, куда доступ свободный.
# История задним числом не меняется, так что зеркало не врёт — устареть
# может только последний коммит, до следующей пересборки.
MIRROR="$SUPPORT/repo.git"
if [ -d "$MIRROR" ]; then
  git --git-dir="$MIRROR" fetch --prune origin "+refs/*:refs/*" 2>/dev/null \
    || git --git-dir="$MIRROR" fetch --all --prune 2>/dev/null || true
  echo "  обновлено: $MIRROR"
else
  git clone --mirror "$ROOT" "$MIRROR" >/dev/null 2>&1
  echo "  создано: $MIRROR"
fi
echo "  коммитов в зеркале: $(git --git-dir="$MIRROR" rev-list --count HEAD)"

echo "→ Настройки"
if [ -f "$ROOT/.env" ]; then
  cp "$ROOT/.env" "$SUPPORT/.env"
  chmod 600 "$SUPPORT/.env"
  echo "  .env скопирован"
fi
mkdir -p "$MEMORY"

# Git-инструменты читают историю репозитория, поэтому приложению нужно
# знать, где он лежит: внутри бандла .git нет.
cat > "$APP/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/sh
AI_ADVENT_ENV_FILE="$SUPPORT/.env"
AI_ADVENT_MEMORY_DIR="$MEMORY"
AI_ADVENT_REPO="$ROOT"
AI_ADVENT_REPO_MIRROR="$MIRROR"
export AI_ADVENT_ENV_FILE AI_ADVENT_MEMORY_DIR AI_ADVENT_REPO AI_ADVENT_REPO_MIRROR
RESOURCES="\$(cd "\$(dirname "\$0")/../Resources" && pwd)"
exec "$VENV/bin/python" "\$RESOURCES/app.py"
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

codesign --force --sign - "$APP" 2>/dev/null && echo "  подписано ad-hoc"
touch "$APP"

echo
echo "Готово: $APP"
echo "Запустить:  open \"$APP\""
echo "Проверка дня: нажмите готовый сценарий 5 — длинный флоу."
echo "В панели «Маршрут» справа видно, что вызовы ушли на ТРИ разных"
echo "сервера: git, pipe и sched, и в правильном порядке."
