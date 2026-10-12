#!/usr/bin/env bash
# Ставит сервис в автозапуск через launchd: он поднимется при входе
# в систему и переживёт закрытие терминала.
#
#   ./install_service.sh            # только для этой машины
#   ./install_service.sh --lan      # доступен в локальной сети
#   ./install_service.sh --remove   # снять
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="local.aiadvent.service"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SUPPORT="$HOME/Library/Application Support/AI Advent"
LOG="$SUPPORT/service.log"
PORT="${PORT:-8080}"
HOST="127.0.0.1"

if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  rm -rf "$SUPPORT/service"
  echo "Сервис снят с автозапуска."
  exit 0
fi

[ "${1:-}" = "--lan" ] && HOST="0.0.0.0"

mkdir -p "$HOME/Library/LaunchAgents" "$SUPPORT"

# Процесс из launchd не имеет доступа к ~/Documents: macOS TCC выдаёт
# разрешение терминалу, а не службе. Та же ловушка, что в дне 17 с .app.
# Поэтому код копируем в Application Support и запускаем оттуда.
# Интерпретатор прописываем полным путём. Через /usr/bin/env launchd
# подхватывает python3 из Xcode (3.9), а код требует 3.10+ — служба
# падала на аннотации `str | None` ещё до первой строки работы.
PYTHON="$(command -v python3)"
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "⚠ python3 здесь версии $("$PYTHON" -V 2>&1), нужен 3.10 или новее"
  exit 1
fi

MEMORY="$SUPPORT/memory"
mkdir -p "$MEMORY"

RUNTIME="$SUPPORT/service"
mkdir -p "$RUNTIME"
cp "$HERE/service.py" "$HERE/ui.html" "$HERE/paths.py" "$RUNTIME/"
ROOT="$(cd "$HERE/../.." && pwd)"
cp "$ROOT"/shared/memory.py "$ROOT"/shared/llm.py "$ROOT"/shared/tokens.py \
   "$RUNTIME/" 2>/dev/null || true
echo "  код скопирован в $RUNTIME"

# Ollama нужен сервису. Если он не поставлен как служба, сервис будет
# подниматься и сразу упираться в молчащий бэкенд — скажем об этом сразу.
if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  echo "⚠ Ollama не отвечает. Поставьте его службой, иначе сервис"
  echo "  будет запускаться, но отвечать не сможет:"
  echo "      brew services start ollama"
fi

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>              <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$RUNTIME/service.py</string>
    <string>--host</string><string>$HOST</string>
    <string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key>   <string>$RUNTIME</string>
  <key>EnvironmentVariables</key>
  <dict>
    <!-- Тот же каталог памяти, что у приложений прошлых дней. Без этого
         служба заводит отдельный ключ рядом с собой, и ключ из
         репозитория к ней не подходит. -->
    <key>AI_ADVENT_MEMORY_DIR</key> <string>$MEMORY</string>
  </dict>
  <key>RunAtLoad</key>          <true/>
  <key>KeepAlive</key>          <true/>
  <key>StandardOutPath</key>    <string>$LOG</string>
  <key>StandardErrorPath</key>  <string>$LOG</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# Даём службе создать ключ, прежде чем его показывать.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
echo "Сервис поставлен в автозапуск."
echo "  слушает $HOST:$PORT"
if [ "$HOST" = "0.0.0.0" ]; then
  LAN="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1)"
  echo "  в сети:  http://$LAN:$PORT/chat"
fi
echo "  питон:   $PYTHON"
echo "  код:     $RUNTIME"
echo "  лог:     $LOG"
KEYFILE="$MEMORY/service-key.txt"
echo "  ключ:    $(cat "$KEYFILE" 2>/dev/null || echo 'появится после старта')"
echo "           ($KEYFILE)"
echo
echo "  состояние: launchctl list | grep aiadvent"
echo "  снять:     ./install_service.sh --remove"

if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "  ✓ отвечает"
else
  echo "  ⚠ пока не отвечает, посмотрите лог"
fi
