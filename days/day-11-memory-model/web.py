#!/usr/bin/env python3
"""День 11: агент с тремя слоями памяти — сервер для окна.

    python3 web.py            # http://127.0.0.1:8000

Что нового против дня 7. Там окно показывало одну память — историю диалога.
Здесь их три, и они показаны отдельными панелями, потому что различаются
не форматом, а ОБЛАСТЬЮ ЖИЗНИ:

    краткосрочная   реплики текущего диалога, вытесняются окном
    рабочая         данные текущей задачи, живут в сессии
    долговременная  профиль и решения, живут ВНЕ сессий

Проверяется это одной кнопкой: «Новый диалог» очищает первые два слоя,
а третий остаётся на месте — и агент по-прежнему знает, как вас зовут.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import (DEFAULT_SESSION, LONG, LONG_KINDS, MemoryError_,  # noqa: E402
                    PROFILE, WORKING, open_store)

PAGE = Path(__file__).with_name("ui.html").read_bytes()

STORE = open_store("sqlite")
AGENT = Agent(name="Помощник", store=STORE,
              session=os.environ.get("AI_ADVENT_SESSION", DEFAULT_SESSION),
              memory_turns=4)
AGENT.layered = True          # раскладка по слоям включена


class Handler(BaseHTTPRequestHandler):
    server_version = "day11/1.0"

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif route == "/state":
            self._send_json(200, self._state())
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        route = urlsplit(self.path).path

        if route == "/reset":
            # СТИРАЕТ текущий диалог вместе с рабочей памятью. Долговременная
            # остаётся. Это не то же самое, что «начать новый диалог», —
            # см. /session ниже.
            AGENT.reset()
            self._send_json(200, self._state())
            return

        if route == "/session":
            # Переключение на другой диалог. Именно это, а не /reset, даёт
            # честную проверку слоёв: старый диалог остаётся на диске целым,
            # краткосрочная и рабочая память берутся из нового, а
            # долговременная переезжает — она вне сессий.
            payload = self._payload()
            if payload is None:
                return
            имя = str(payload.get("session", "")).strip() or DEFAULT_SESSION
            AGENT.switch(имя)
            self._send_json(200, self._state())
            return

        if route == "/remember":
            payload = self._payload()
            if payload is None:
                return
            слой = str(payload.get("layer", LONG))
            вид = str(payload.get("kind", PROFILE))
            ключ = str(payload.get("key", "")).strip()
            значение = str(payload.get("value", "")).strip()
            if not ключ or not значение:
                self._send_json(400, {"error": "Нужны и ключ, и значение"})
                return
            try:
                AGENT.remember(ключ, значение, layer=слой,
                               kind=вид if вид in LONG_KINDS else PROFILE)
            except (ValueError, MemoryError_) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, self._state())
            return

        if route == "/forget":
            payload = self._payload()
            if payload is None:
                return
            AGENT.forget(str(payload.get("key", "")))
            self._send_json(200, self._state())
            return

        if route != "/ask":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return

        payload = self._payload()
        if payload is None:
            return
        message = str(payload.get("message", "")).strip()

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if not message:
            self._line({"error": "Пустой запрос"})
            return

        было_рабочих = {f.key for f in AGENT.facts}
        было_долгих = {m.key for m in AGENT.memos}

        try:
            for piece in AGENT.stream(message):
                self._line({"delta": piece})
            # stream() не раскладывает по слоям — это делает _remember при
            # ask(). Догоняем вручную, чтобы поведение совпадало.
            if AGENT.journal:
                последний = AGENT.journal[-1]
                try:
                    AGENT._route(последний.question, последний.answer)
                except LLMError:
                    pass
            self._line({"done": True, **self._state(),
                        "новое": {
                            "working": sorted({f.key for f in AGENT.facts} - было_рабочих),
                            "long": sorted({m.key for m in AGENT.memos} - было_долгих),
                        }})
        except LLMError as exc:
            self._line({"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── вспомогательное ─────────────────────────────────────────────────
    def _state(self) -> dict:
        s = AGENT.stats
        return {
            "name": AGENT.name,
            "model": AGENT.model,
            "session": AGENT.session,
            "sessions": [
                {"name": i.name, "pairs": i.pairs} for i in AGENT.store.sessions()
            ] if AGENT.store else [],
            "layers": AGENT.layers(),
            "turns": s.turns,
            "tokens": s.total_tokens,
            "overhead": s.overhead_tokens,
            "spent": AGENT.spent_pretty,
            "weigh": AGENT.weigh(),
            "history": [
                {"role": t.role, "content": t.content, "when": t.when}
                for t in AGENT.transcript()
            ],
        }

    def _payload(self) -> dict | None:
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
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    print(f"Агент: {AGENT.describe()}")
    print(f"Слои: краткосрочная {AGENT.remembers} пар, "
          f"рабочая {len(AGENT.facts)} записей, "
          f"долговременная {len(AGENT.memos)} записей")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
