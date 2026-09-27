#!/usr/bin/env python3
"""День 16: подключиться к MCP-серверу и показать его инструменты.

    python3 list_tools.py                  свой сервер (память агента)
    python3 list_tools.py --server time    официальный сервер Anthropic
    python3 list_tools.py --raw            показать сырой обмен JSON-RPC
    python3 list_tools.py --call           дополнительно вызвать инструмент
    python3 list_tools.py -- python3 -m mcp_server_fetch     любой свой

Важное про слово «сервер». Никакой аренды и никакой сети: MCP-сервер
поверх stdio — это обычный процесс на этой же машине. Клиент запускает
его как дочерний и разговаривает через stdin/stdout.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ЗДЕСЬ = Path(__file__).resolve().parent
sys.path.insert(0, str(ЗДЕСЬ.parents[1] / "shared"))

from mcp import MCPClient, MCPError, PROTOCOL_VERSION  # noqa: E402

TTY = sys.stdout.isatty()
DIM, BOLD, CYAN, GREEN, RED, YELLOW, MAG = "2", "1", "36", "32", "31", "33", "35"

# venv с официальным сервером. Он нужен ТОЛЬКО для проверки на чужой
# реализации — сам клиент никаких зависимостей не требует.
ЧУЖОЙ_PYTHON = ЗДЕСЬ / ".venv" / "bin" / "python"

СЕРВЕРЫ = {
    "memory": {
        "подпись": "свой, над памятью агента (дни 7–15)",
        "команда": [sys.executable, str(ЗДЕСЬ / "server.py")],
        "зависимости": "нет, стандартная библиотека",
    },
    "time": {
        "подпись": "официальный mcp-server-time от Anthropic",
        "команда": [str(ЧУЖОЙ_PYTHON), "-m", "mcp_server_time"],
        "зависимости": "pip install mcp-server-time (в .venv рядом)",
    },
}


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def показать_обмен(команда: list[str]) -> None:
    """Сырой JSON-RPC: три письма туда, три ответа обратно.

    Нужен, чтобы на видео было видно сам протокол, а не только результат.
    Здесь нарочно нет ни одной абстракции — только строки в stdin и stdout.
    """
    print(paint(BOLD, "\n  Сырой обмен JSON-RPC\n"))
    письма = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "ai-advent-raw", "version": "1.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    процесс = subprocess.Popen(команда, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8", bufsize=1)
    try:
        for письмо in письма:
            строка = json.dumps(письмо, ensure_ascii=False)
            print(paint(CYAN, f"    → {строка[:150]}"))
            процесс.stdin.write(строка + "\n")
            процесс.stdin.flush()
            if "id" not in письмо:
                print(paint(DIM, "      (уведомление — ответа не будет)"))
                continue
            ответ = процесс.stdout.readline().strip()
            коротко = ответ if len(ответ) <= 150 else ответ[:150] + "…"
            print(paint(GREEN, f"    ← {коротко}"))
        print()
    finally:
        процесс.stdin.close()
        try:
            процесс.wait(timeout=5)
        except subprocess.TimeoutExpired:
            процесс.kill()


def main() -> int:
    args = sys.argv[1:]
    сырой = "--raw" in args
    звать = "--call" in args

    if "--" in args:
        команда = args[args.index("--") + 1:]
        подпись, зависимости = "указан вручную", "—"
        if not команда:
            print(paint(RED, "  После -- нужна команда запуска сервера"))
            return 1
    else:
        ключ = args[args.index("--server") + 1] if "--server" in args else "memory"
        if ключ not in СЕРВЕРЫ:
            print(paint(RED, f"  Неизвестный сервер: {ключ}. "
                             f"Доступны: {', '.join(СЕРВЕРЫ)}"))
            return 1
        выбран = СЕРВЕРЫ[ключ]
        команда = выбран["команда"]
        подпись = выбран["подпись"]
        зависимости = выбран["зависимости"]

        if ключ == "time" and not ЧУЖОЙ_PYTHON.exists():
            print(paint(RED, "\n  Официальный сервер не установлен."))
            print(paint(DIM, "  Поставить (только для проверки, коду не нужен):\n"))
            print(paint(DIM, f"    python3 -m venv {ЗДЕСЬ / '.venv'}"))
            print(paint(DIM, f"    {ЧУЖОЙ_PYTHON} -m pip install mcp-server-time\n"))
            return 1

    print(paint(BOLD, "\n  MCP: подключение и список инструментов"))
    print(paint(DIM, f"  сервер:       {подпись}"))
    print(paint(DIM, f"  команда:      {' '.join(команда)}"))
    print(paint(DIM, f"  зависимости:  {зависимости}"))
    print(paint(DIM, f"  наш протокол: {PROTOCOL_VERSION}"))

    if сырой:
        показать_обмен(команда)

    try:
        with MCPClient(команда, timeout=30) as сервер:
            print(paint(GREEN, "\n  ✓ соединение установлено"))
            print(f"    сервер:      {paint(BOLD, str(сервер.info))}")
            согласован = сервер.info.protocol
            совпало = "совпал с нашим" if согласован == PROTOCOL_VERSION \
                else paint(YELLOW, "отличается от нашего — договорились на его")
            print(paint(DIM, f"    протокол:    {согласован} ({совпало})"))
            print(paint(DIM, f"    возможности: "
                             f"{', '.join(сервер.info.abilities) or '—'}"))
            if сервер.info.instructions:
                текст = " ".join(сервер.info.instructions.split())
                print(paint(DIM, f"    о себе:      {текст[:96]}"))

            инструменты = сервер.list_tools()
            print(paint(GREEN, f"\n  ✓ инструментов получено: "
                               f"{len(инструменты)}\n"))
            for номер, и in enumerate(инструменты, 1):
                print(f"    {номер}. {paint(BOLD, и.name)}")
                if и.short:
                    print(paint(DIM, f"       {и.short}"))
                for имя, тип, обяз in и.params:
                    метка = paint(MAG, "обязательный") if обяз \
                        else paint(DIM, "необязательный")
                    print(f"       · {имя}: {paint(DIM, тип)} — {метка}")
                if not и.params:
                    print(paint(DIM, "       · без параметров"))
                print()

            if звать and инструменты:
                первый = инструменты[0]
                аргументы = {}
                # Подставляем что-нибудь осмысленное в обязательные поля,
                # иначе сервер справедливо откажет.
                for имя, тип, обяз in первый.params:
                    if обяз and тип == "string":
                        аргументы[имя] = ("Europe/Moscow" if "timezone" in имя
                                          else "default")
                print(paint(BOLD, f"  Пробный вызов: {первый.name}({аргументы})"))
                итог = сервер.call_tool(первый.name, аргументы)
                куски = итог.get("content") or []
                текст = next((к.get("text") for к in куски
                              if к.get("type") == "text"), "")
                пометка = paint(RED, " [isError]") if итог.get("isError") else ""
                print(paint(DIM, f"  ← {текст[:300]}{пометка}\n"))

            if сервер.stderr:
                print(paint(DIM, "  Сервер писал в stderr "
                                 "(это нормально, там его логи):"))
                for строка in сервер.stderr.splitlines()[-4:]:
                    print(paint(DIM, f"    {строка[:100]}"))
                print()
    except MCPError as exc:
        print(paint(RED, f"\n  ✕ не получилось: {exc}\n"))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
