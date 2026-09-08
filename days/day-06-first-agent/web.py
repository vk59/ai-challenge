#!/usr/bin/env python3
"""День 6: тот же агент, но в окне.

    python3 web.py            # http://127.0.0.1:8000
    python3 web.py 9000       # другой порт

Сравните с days/day-01-first-api-call/web.py. Там браузер присылал всю
историю диалога вместе с каждым вопросом, потому что помнить было некому.
Здесь наружу уходит только новое сообщение: память внутри агента.

Импортов из llm тут нет — этот файл про HTTP и вёрстку, а не про LLM.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402

PAGE = Path(__file__).with_name("ui.html").read_bytes()

# Один агент на весь сервер: он и есть состояние приложения.
AGENT = Agent(name="Помощник")


class Handler(BaseHTTPRequestHandler):
    server_version = "day06/1.0"

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif route == "/agent":
            self._send_json(200, self._snapshot())
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        route = urlsplit(self.path).path
        if route == "/reset":
            AGENT.reset()
            self._send_json(200, self._snapshot())
            return
        if route != "/ask":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Тело запроса — не JSON"})
            return

        # Всё, что приходит от браузера, — одна строка. Никакой истории.
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
            "remembers": AGENT.remembers,
            "memory_turns": AGENT.memory_turns,
            "turns": AGENT.stats.turns,
            "tokens": AGENT.stats.total_tokens,
        }

    def _last_call(self) -> dict:
        if not AGENT.journal:
            return {}
        call = AGENT.journal[-1]
        return {"answer_tokens": call.completion_tokens, "seconds": call.seconds}

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
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    print(f"Агент: {AGENT.describe()}")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
