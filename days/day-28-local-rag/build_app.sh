#!/bin/bash
#
# Собирает «День 28.app» — сравнение локального RAG с облачным.
#
#   ./build_app.sh
#   open "dist/День 28.app"
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
APP="$HERE/dist/День 28.app"

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

cp "$HERE"/app.py "$HERE"/web.py "$HERE"/ui.html "$HERE"/paths.py \
   "$HERE"/bench.py "$APP/Contents/Resources/"

# Контрольные вопросы дня 22 — в подпапку: внутри бандла всё плоско,
# и его evaluate.py затёр бы чужие файлы.
mkdir -p "$APP/Contents/Resources/day-22-rag"
cp "$ROOT"/days/day-22-rag/questions.py \
   "$APP/Contents/Resources/day-22-rag/"
# chat.py нужен окну: внизу обычный чат из дня 25, только локальный.
# rerank.py — его ленивый импорт, если включить реранкинг.
cp "$ROOT"/shared/llm.py "$ROOT"/shared/memory.py "$ROOT"/shared/tokens.py \
   "$ROOT"/shared/embeddings.py "$ROOT"/shared/index.py \
   "$ROOT"/shared/cite.py "$ROOT"/shared/chat.py "$ROOT"/shared/rerank.py \
   "$APP/Contents/Resources/"

# Локальный индекс переносим готовым: собирать его внутри приложения
# значит ждать минуту. Он бесплатный, но не мгновенный.
if [ -f "$ROOT/memory/index-local.db" ]; then
  mkdir -p "$MEMORY"
  cp "$ROOT/memory/index-local.db" "$MEMORY/index-local.db"
  cp "$ROOT/memory/embeddings.db" "$MEMORY/embeddings.db" 2>/dev/null || true
  echo "  локальный индекс перенесён"
else
  echo "  ⚠ локального индекса нет → AI_ADVENT_EMBEDDINGS=local \\"
  echo "      python3 ../day-21-indexing/build.py"
fi
if [ -f "$ROOT/memory/index.db" ]; then
  cp "$ROOT/memory/index.db" "$MEMORY/index.db"
fi
echo "  код скопирован внутрь"

# Ollama внутрь не кладём: отдельная программа плюс модель на гигабайты.
# Проверяем, что он отвечает, и подсказываем, если нет.
if curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  echo "  Ollama отвечает: $(curl -s http://127.0.0.1:11434/api/version)"
else
  echo "  ⚠ Ollama не отвечает. Поставьте и запустите:"
  echo "      brew install ollama && ollama serve"
  echo "      ollama pull qwen2.5:3b && ollama pull bge-m3"
fi

# Индекс отдаём готовым: собирать его внутри приложения значит ждать минуту
# и платить заново. Базы — производные кэши, перезаписывать их безопасно.
if [ -f "$ROOT/memory/index.db" ]; then
  mkdir -p "$MEMORY"
  cp "$ROOT/memory/index.db" "$MEMORY/index.db"
  cp "$ROOT/memory/embeddings.db" "$MEMORY/embeddings.db" 2>/dev/null || true
  echo "  индекс и кэш векторов перенесены в Application Support"
else
  echo "  ⚠ индекса нет — соберите: cd ../day-21-indexing && python3 build.py"
fi

# Корпус тоже копируем: по нему questions.py --check сверяет ожидания.
CORPUS="$SUPPORT/corpus"
rm -rf "$CORPUS"
mkdir -p "$CORPUS/days" "$CORPUS/docs" "$CORPUS/shared"
cp "$ROOT"/README.md "$CORPUS/"
cp "$ROOT"/docs/*.md "$CORPUS/docs/" 2>/dev/null || true
for d in "$ROOT"/days/day-*/; do
  name="$(basename "$d")"
  mkdir -p "$CORPUS/days/$name"
  cp "$d"README.md "$CORPUS/days/$name/" 2>/dev/null || true
  cp "$d"video.md "$CORPUS/days/$name/" 2>/dev/null || true
done
cp "$ROOT"/shared/*.py "$CORPUS/shared/"
echo "  корпус скопирован: $(find "$CORPUS" -type f | wc -l | tr -d ' ') файлов"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>                <string>День 28</string>
  <key>CFBundleDisplayName</key>         <string>День 28 — локальный RAG</string>
  <key>CFBundleIdentifier</key>          <string>local.aiadvent.day28</string>
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
AI_ADVENT_CORPUS="$CORPUS"
AI_ADVENT_REPO_MIRROR="$MIRROR"
export AI_ADVENT_ENV_FILE AI_ADVENT_MEMORY_DIR AI_ADVENT_REPO AI_ADVENT_CORPUS
RESOURCES="\$(cd "\$(dirname "\$0")/../Resources" && pwd)"
exec "$VENV/bin/python" "\$RESOURCES/app.py"
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

codesign --force --sign - "$APP" 2>/dev/null && echo "  подписано ad-hoc"
touch "$APP"

echo
echo "Готово: $APP"
echo "Запустить:  open \"$APP\""
echo "Проверка дня: щёлкните вопрос слева — три стека ответят рядом."
echo "Потом «Полный замер»: десять вопросов по три повтора."
