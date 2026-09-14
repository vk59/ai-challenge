#!/usr/bin/env python3
"""День 7: тот же агент в окне, но диалог переживает перезапуск сервера.

    python3 web.py            # http://127.0.0.1:8000
    python3 web.py 9000       # другой порт
    python3 web.py --store json

Что изменилось против дня 6. Там при открытии страницы лента была пустой:
агент жил только в памяти процесса, и перезапуск означал чистый лист.
Здесь GET /agent отдаёт вместе с состоянием ещё и весь транскрипт с диска,
и браузер рисует разговор ровно с того места, где его прервали.

Обратите внимание, что именно отдаётся браузеру:

    history   весь архив — то, что человек видит на экране
    remembers окно контекста — то, что реально уедет в модель

Это разные числа, и в строке состояния они стоят рядом намеренно.

Импортов из llm тут по-прежнему нет — этот файл про HTTP и вёрстку.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import DEFAULT_SESSION, MemoryError_, open_store  # noqa: E402

PAGE = Path(__file__).with_name("ui.html").read_bytes()


def _options(args: list[str]) -> tuple[str, int]:
    """Разбирает `web.py [порт] [--store json|sqlite]` в любом порядке.

    Флаг со значением нельзя просто выкинуть по префиксу `--`: значение
    останется в позиционных и уедет в int() вместо порта.
    """
    kind = os.environ.get("AI_ADVENT_STORE", "sqlite")
    positional: list[str] = []

    index = 0
    while index < len(args):
        if args[index] == "--store":
            if index + 1 >= len(args):
                raise SystemExit("У флага --store не хватает значения (json или sqlite)")
            kind = args[index + 1]
            index += 2
            continue
        positional.append(args[index])
        index += 1

    port = int(positional[0]) if positional else 8000
    return kind, port


STORE_KIND, PORT = _options(sys.argv[1:])


# Агент по-прежнему один на весь сервер и по-прежнему является состоянием
# приложения — только теперь это состояние лежит не в оперативной памяти.
STORE = open_store(STORE_KIND)
AGENT = Agent(
    name="Помощник",
    store=STORE,
    session=os.environ.get("AI_ADVENT_SESSION", DEFAULT_SESSION),
)


class Handler(BaseHTTPRequestHandler):
    server_version = "day07/1.0"

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif route == "/agent":
            # Единственный запрос при открытии страницы — и он же
            # восстанавливает разговор.
            self._send_json(200, {**self._snapshot(), "history": self._history()})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        route = urlsplit(self.path).path

        if route == "/reset":
            # «Забыть» теперь означает «стереть с диска»: иначе перезапуск
            # вернул бы забытое и кнопка врала бы.
            AGENT.reset()
            self._send_json(200, {**self._snapshot(), "history": []})
            return

        if route == "/session":
            payload = self._payload()
            if payload is None:
                return
            name = str(payload.get("session", "")).strip() or DEFAULT_SESSION
            AGENT.switch(name)
            self._send_json(200, {**self._snapshot(), "history": self._history()})
            return

        if route != "/ask":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return

        payload = self._payload()
        if payload is None:
            return

        # Всё, что приходит от браузера, — одна строка. Никакой истории:
        # она и в дне 6 была не его делом, а теперь ещё и лежит на диске.
        message = str(payload.get("message", "")).strip()

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if not message:
            self._line({"error": "Пустой запрос"})
            return

        try:
            for piece in AGENT.stream(message):
                self._line({"delta": piece})
            self._line({"done": True, **self._snapshot(), **self._last_call()})
        except LLMError as exc:
            self._line({"error": str(exc)})
        except MemoryError_ as exc:
            self._line({"error": f"Ответ получен, но не сохранён: {exc}"})
        except (BrokenPipeError, ConnectionResetError):
            pass  # вкладку закрыли посреди ответа — это нормально

    # ── вспомогательное ─────────────────────────────────────────────────
    def _snapshot(self) -> dict:
        """Состояние агента для строки статуса."""
        return {
            "name": AGENT.name,
            "model": AGENT.model,
            "temperature": AGENT.temperature,
            "role": AGENT.role,
            "remembers": AGENT.remembers,        # уедет в модель
            "memory_turns": AGENT.memory_turns,
            "archived": AGENT.archived,          # лежит на диске
            "turns": AGENT.stats.turns,
            "tokens": AGENT.stats.total_tokens,
            "session": AGENT.session,
            "store": str(AGENT.store),
            "store_kind": AGENT.store.kind,
            "sessions": [
                {"name": info.name, "pairs": info.pairs, "updated": info.when}
                for info in AGENT.store.sessions()
            ],
        }

    def _history(self) -> list[dict]:
        """Весь архив сессии — это и есть «продолжить, как будто не выключали»."""
        window = AGENT.remembers * 2
        turns = AGENT.transcript()
        return [
            {
                "role": turn.role,
                "content": turn.content,
                "when": turn.when,
                "tokens": turn.tokens,
                "seconds": turn.seconds,
                # Реплика есть на диске и на экране, но в запрос к модели
                # уже не попадёт — интерфейс показывает это честно.
                "in_window": index >= len(turns) - window,
            }
            for index, turn in enumerate(turns)
        ]

    def _last_call(self) -> dict:
        if not AGENT.journal:
            return {}
        call = AGENT.journal[-1]
        return {"answer_tokens": call.completion_tokens, "seconds": call.seconds}

    def _payload(self) -> dict | None:
        """Читает JSON-тело запроса; при ошибке сам отвечает 400."""
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Тело запроса — не JSON"})
            return None

    def _line(self, payload: dict) -> None:
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        self.wfile.flush()

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {self.command} {self.path} — {args[1]}\n")


if __name__ == "__main__":
    port = PORT
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    print(f"Агент: {AGENT.describe()}")
    if AGENT.archived:
        print(f"Диалог восстановлен с диска: {AGENT.archived} пар, "
              f"в окно контекста поднято {AGENT.remembers}.")
    else:
        print("Архив пуст — это первый запуск с этим диалогом.")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено. Диалог сохранён — запусти снова и проверь.")
