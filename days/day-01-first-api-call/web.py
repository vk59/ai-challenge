#!/usr/bin/env python3
"""День 1: тот же запрос к LLM, но через окно в браузере.

    python3 web.py            # http://127.0.0.1:8000
    python3 web.py 9000       # другой порт

Сервер на http.server из стандартной библиотеки — Flask сюда не нужен.
Всего три маршрута: страница, модель для подзаголовка и сам запрос к LLM.

Ответ отдаётся потоком: по одному JSON на строку (NDJSON), браузер читает
их по мере поступления и дорисовывает текст в пузырь.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from llm import DEFAULT_MODEL, LLMError, ask_stream, load_env

PAGE = Path(__file__).with_name("ui.html").read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = "day01/1.0"

    def do_GET(self) -> None:
        # app.py открывает страницу как /?native=1 — query-строку отбрасываем
        route = urlsplit(self.path).path

        if route == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif route == "/model":
            try:
                load_env()
            except LLMError:
                pass  # про недоступный .env расскажем при первом же запросе
            model = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
            self._send_json(200, {"model": model})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/ask":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Тело запроса — не JSON"})
            return

        prompt = str(payload.get("prompt", "")).strip()
        history = payload.get("history") or []

        # Заголовки уходят сразу, тело дописывается по мере генерации,
        # поэтому Content-Length здесь не выставляем.
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if not prompt:
            self._line({"error": "Пустой запрос"})
            return

        stats: dict = {}
        try:
            for delta in ask_stream(prompt, history=history, stats=stats):
                self._line({"delta": delta})
            self._line({"done": True, **stats})
        except LLMError as exc:
            self._line({"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass  # вкладку закрыли посреди ответа — это нормально

    def _line(self, payload: dict) -> None:
        """Одна строка потока: компактный JSON + перевод строки."""
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

    def log_message(self, fmt, *args):  # чуть тише стандартного лога
        sys.stderr.write(f"  {self.command} {self.path} — {args[1]}\n")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
