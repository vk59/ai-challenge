#!/usr/bin/env python3
"""День 15: контролируемые переходы — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Приложение дня 13 плюс условия на рёбрах. В окне это видно так: кнопка
перехода в «выполнение» есть, но она с замком, и рядом написано, чего
не хватает. Утвердить план может только человек — кнопкой.

Плюс права этапа уезжают в промпт, поэтому на планировании агент
отказывается писать реализацию, даже если прямо попросить.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import (DEFAULT_SESSION, GUARDS, STAGE_LABELS, STAGE_RIGHTS,  # noqa: E402
                    STAGES, TRANSITIONS, MemoryError_, open_store)

PAGE = Path(__file__).with_name("ui.html").read_bytes()

STORE = open_store("sqlite")
AGENT = Agent(name="Исполнитель", store=STORE,
              session=os.environ.get("AI_ADVENT_SESSION", DEFAULT_SESSION),
              memory_turns=5)
AGENT.tracking = True
# Условия на рёбрах и права этапа — это и есть день 15.
AGENT.guards = True


class Handler(BaseHTTPRequestHandler):
    server_version = "day15/1.0"

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

        if route == "/task/start":
            payload = self._payload()
            if payload is None:
                return
            AGENT.start_task(str(payload.get("goal", "")).strip())
            self._send_json(200, self._state())
            return

        if route == "/task/advance":
            payload = self._payload()
            if payload is None:
                return
            получилось, почему = AGENT.advance(
                str(payload.get("stage", "")).strip(), "вручную из окна")
            # Отказ — это 200: автомат отработал правильно, и причину надо
            # показать человеку, а не спрятать под ошибкой сети.
            self._send_json(200, {**self._state(), "moved": получилось,
                                  "why": почему})
            return

        if route == "/task/approve":
            payload = self._payload()
            if payload is None:
                return
            ок, текст = AGENT.approve(str(payload.get("key", "")).strip())
            self._send_json(200, {**self._state(), "approved": ок, "why": текст})
            return

        if route == "/task/revoke":
            payload = self._payload()
            if payload is None:
                return
            снято = AGENT.revoke(str(payload.get("key", "")).strip())
            self._send_json(200, {**self._state(), "revoked": снято})
            return

        if route == "/task/update":
            payload = self._payload()
            if payload is None:
                return
            AGENT.update_task(step=payload.get("step"),
                              expecting=payload.get("expecting"),
                              goal=payload.get("goal"))
            self._send_json(200, self._state())
            return

        if route == "/session":
            payload = self._payload()
            if payload is None:
                return
            AGENT.switch(str(payload.get("session", "")).strip() or DEFAULT_SESSION)
            self._send_json(200, self._state())
            return

        if route == "/chat/delete":
            payload = self._payload()
            if payload is None:
                return
            AGENT.drop(str(payload.get("session", "")).strip())
            self._send_json(200, self._state())
            return

        if route == "/reset":
            AGENT.reset()
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

        try:
            for piece in AGENT.stream(message):
                self._line({"delta": piece})
            итог = {}
            if AGENT.journal and AGENT.tracking:
                последний = AGENT.journal[-1]
                try:
                    итог = AGENT._track_task(последний.question, последний.answer)
                except LLMError:
                    pass
            self._line({"done": True, **self._state(), "task_change": итог})
        except LLMError as exc:
            self._line({"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── вспомогательное ─────────────────────────────────────────────────
    def _state(self) -> dict:
        s = AGENT.stats
        return {
            "name": AGENT.name, "model": AGENT.model, "session": AGENT.session,
            "chats": AGENT.chats(),
            "task": AGENT.task_view(),
            "graph": {st: [{"stage": t, "label": STAGE_LABELS[t]}
                           for t in TRANSITIONS[st]] for st in STAGES},
            "rights_map": {st: STAGE_RIGHTS.get(st, {}) for st in STAGES},
            "tokens": s.total_tokens, "spent": AGENT.spent_pretty,
            "weigh": AGENT.weigh(), "history": self._history(),
        }

    def _history(self) -> list[dict]:
        return [{"role": t.role, "content": t.content, "when": t.when}
                for t in AGENT.transcript()]

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
    if AGENT.task:
        v = AGENT.task_view()
        заперто = [g["label"] for g in v["allowed"] if not g["met"]]
        print(f"Задача «{AGENT.session}»: этап {AGENT.task.label}"
              + (f", заперто: {', '.join(заперто)}" if заперто else ""))
    else:
        print("Задача не заведена.")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено. Этап и отметки сохранены.")
