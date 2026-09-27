#!/bin/bash
#
# Ставит launchd-агент: задачи выполняются, даже когда приложение закрыто.
#
#   ./install_agent.sh          поставить (по умолчанию раз в 5 минут)
#   ./install_agent.sh 60       свой интервал опроса, в секундах
#   ./install_agent.sh --remove снять
#
# Зачем он нужен. Фоновый поток внутри приложения живёт ровно столько,
# сколько открыто окно. Настоящий круглосуточный фон на macOS — это launchd:
# система сама будит наш скрипт по расписанию, даже после перезагрузки.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SUPPORT="$HOME/Library/Application Support/AI Advent"
VENV="$SUPPORT/venv"
МЕТКА="local.aiadvent.scheduler"
PLIST="$HOME/Library/LaunchAgents/$МЕТКА.plist"
ЛОГ="$SUPPORT/scheduler.log"

if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$МЕТКА" 2>/dev/null || \
    launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Агент снят. Фоновые задачи теперь работают только при открытом приложении."
  exit 0
fi

ИНТЕРВАЛ="${1:-300}"
PY="$VENV/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

mkdir -p "$HOME/Library/LaunchAgents" "$SUPPORT"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$МЕТКА</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$HERE/runner.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>AI_ADVENT_ENV_FILE</key><string>$SUPPORT/.env</string>
    <key>AI_ADVENT_MEMORY_DIR</key><string>$SUPPORT/memory</string>
    <key>AI_ADVENT_REPO</key><string>$ROOT</string>
    <key>AI_ADVENT_REPO_MIRROR</key><string>$SUPPORT/repo.git</string>
  </dict>
  <key>StartInterval</key><integer>$ИНТЕРВАЛ</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$ЛОГ</string>
  <key>StandardErrorPath</key><string>$ЛОГ</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$МЕТКА" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST"

echo "Агент поставлен: $МЕТКА"
echo "  опрос каждые $ИНТЕРВАЛ с, даже при закрытом приложении"
echo "  журнал: $ЛОГ"
echo
echo "Проверить:   launchctl list | grep aiadvent"
echo "Посмотреть:  tail -f \"$ЛОГ\""
echo "Снять:       ./install_agent.sh --remove"
