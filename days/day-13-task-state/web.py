#!/usr/bin/env python3
"""День 13: состояние задачи как конечный автомат — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Приложение то же, что в днях 11-12 (чаты, память, профиль), плюс панель
автомата: этапы полосой, текущий подсвечен, кнопки переходов — и это
главное — кнопки НЕДОПУСТИМЫХ переходов недоступны. Из «планирования»
нельзя нажать «готово», потому что такого ребра в графе нет.

Каждый чат — отдельная задача со своим состоянием.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import (DEFAULT_SESSION, LONG, LONG_KINDS, STAGE_LABELS,  # noqa: E402
                    STAGES, TRANSITIONS, MemoryError_, PROFILE, Profile,
                    open_store)

PAGE = Path(__file__).with_name("ui.html").read_bytes()

STORE = open_store("sqlite")
AGENT = Agent(name="Исполнитель", store=STORE,
              session=os.environ.get("AI_ADVENT_SESSION", DEFAULT_SESSION),
              memory_turns=5)
AGENT.layered = True
AGENT.tracking = True


class Handler(BaseHTTPRequestHandler):
    server_version = "day13/1.0"

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

        # ── автомат ──
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
            # Отказ — это 200, а не ошибка: автомат отработал как надо,
            # и причину надо показать человеку, а не спрятать в alert.
            self._send_json(200, {**self._state(),
                                  "moved": получилось, "why": почему})
            return

        if route == "/task/update":
            payload = self._payload()
            if payload is None:
                return
            AGENT.update_task(
                step=payload.get("step"), expecting=payload.get("expecting"),
                goal=payload.get("goal"))
            self._send_json(200, self._state())
            return

        # ── профили ──
        if route == "/profile/save":
            payload = self._payload()
            if payload is None:
                return
            имя = str(payload.get("name", "")).strip()
            if not имя:
                self._send_json(400, {"error": "У профиля должно быть имя"})
                return
            AGENT.save_profile(Profile(
                name=имя, tone=str(payload.get("tone", "")).strip(),
                format=str(payload.get("format", "")).strip(),
                level=str(payload.get("level", "")).strip(),
                constraints=str(payload.get("constraints", "")).strip()))
            self._send_json(200, self._state())
            return

        if route == "/profile/use":
            payload = self._payload()
            if payload is None:
                return
            имя = payload.get("name")
            AGENT.use_profile(None if имя in (None, "") else str(имя))
            self._send_json(200, self._state())
            return

        # ── чаты ──
        if route == "/chat/rename":
            payload = self._payload()
            if payload is None:
                return
            if not AGENT.rename(str(payload.get("title", ""))):
                self._send_json(400, {"error": "Имя занято или пустое"})
                return
            self._send_json(200, self._state())
            return

        if route == "/chat/delete":
            payload = self._payload()
            if payload is None:
                return
            AGENT.drop(str(payload.get("session", "")).strip())
            self._send_json(200, self._state())
            return

        if route == "/session":
            payload = self._payload()
            if payload is None:
                return
            AGENT.switch(str(payload.get("session", "")).strip() or DEFAULT_SESSION)
            self._send_json(200, self._state())
            return

        if route == "/reset":
            AGENT.reset()
            self._send_json(200, self._state())
            return

        # ── память ──
        if route == "/remember":
            payload = self._payload()
            if payload is None:
                return
            ключ = str(payload.get("key", "")).strip()
            значение = str(payload.get("value", "")).strip()
            if not ключ or not значение:
                self._send_json(400, {"error": "Нужны и ключ, и значение"})
                return
            вид = str(payload.get("kind", PROFILE))
            try:
                AGENT.remember(ключ, значение, layer=str(payload.get("layer", LONG)),
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

        try:
            for piece in AGENT.stream(message):
                self._line({"delta": piece})
            итог = {}
            if AGENT.journal:
                последний = AGENT.journal[-1]
                if AGENT.tracking:
                    try:
                        итог = AGENT._track_task(последний.question, последний.answer)
                    except LLMError:
                        pass
                if AGENT.layered:
                    try:
                        AGENT._route(последний.question, последний.answer)
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
            "graph": {s: [{"stage": t, "label": STAGE_LABELS[t]}
                          for t in TRANSITIONS[s]] for s in STAGES},
            "profiles": [p.as_dict() for p in AGENT.profiles()],
            "profile": AGENT.profile.name if AGENT.profile else "",
            "layers": AGENT.layers(),
            "tokens": s.total_tokens, "overhead": s.overhead_tokens,
            "spent": AGENT.spent_pretty, "weigh": AGENT.weigh(),
            "history": self._history(),
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
        print(f"Задача «{AGENT.session}»: этап {AGENT.task.label}"
              + (f", шаг: {AGENT.task.step}" if AGENT.task.step else ""))
    else:
        print(f"Задача в чате «{AGENT.session}» ещё не заведена.")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено. Состояние задачи сохранено — продолжите позже.")
