#!/usr/bin/env python3
"""День 14: инварианты — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Приложение то же (чаты, память, состояние задачи), плюс панель ограничений
и — главное для этого дня — проверка каждого ответа. Под ответом появляется
либо «✓ нарушений нет», либо красное «⚠ нарушено ограничение N» с цитатой.

Зачем проверка, если ограничения уже в системном промпте: блок в промпте —
это просьба, а не гарантия. Без проверки мы бы не знали, выполнена ли она.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import (ARCH, DEFAULT_SESSION, INVARIANT_SCOPES, LONG,  # noqa: E402
                    LONG_KINDS, RULE, SCOPE_LABELS, STACK, STAGE_LABELS,
                    STAGES, TRANSITIONS, MemoryError_, PROFILE, open_store)

PAGE = Path(__file__).with_name("ui.html").read_bytes()

STORE = open_store("sqlite")
AGENT = Agent(name="Архитектор", store=STORE,
              session=os.environ.get("AI_ADVENT_SESSION", DEFAULT_SESSION),
              memory_turns=5)
AGENT.auditing = True
AGENT.tracking = True

# Примеры при первом запуске: иначе окно открывается с пустой панелью
# и непонятно, что туда писать.
ПРИМЕРЫ = [
    ("Только PostgreSQL 16. NoSQL-хранилища не предлагать.",
     "вся аналитика построена на SQL и оконных функциях", STACK),
    ("Архитектура монолитная. Микросервисы не вводить.",
     "команда из двух человек, эксплуатировать некому", ARCH),
    ("Персональные данные не покидают контур РФ.", "требование 152-ФЗ", RULE),
]
if not AGENT.invariants:
    for текст, причина, область in ПРИМЕРЫ:
        AGENT.add_invariant(текст, rationale=причина, scope=область)


class Handler(BaseHTTPRequestHandler):
    server_version = "day14/1.0"

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

        # ── инварианты ──
        if route == "/inv/add":
            payload = self._payload()
            if payload is None:
                return
            текст = str(payload.get("text", "")).strip()
            if not текст:
                self._send_json(400, {"error": "Ограничение не может быть пустым"})
                return
            область = str(payload.get("scope", STACK))
            AGENT.add_invariant(
                текст, rationale=str(payload.get("rationale", "")).strip(),
                scope=область if область in INVARIANT_SCOPES else STACK)
            self._send_json(200, self._state())
            return

        if route == "/inv/toggle":
            payload = self._payload()
            if payload is None:
                return
            AGENT.toggle_invariant(int(payload.get("id", 0) or 0),
                                   bool(payload.get("active", True)))
            self._send_json(200, self._state())
            return

        if route == "/inv/delete":
            payload = self._payload()
            if payload is None:
                return
            AGENT.drop_invariant(int(payload.get("id", 0) or 0))
            self._send_json(200, self._state())
            return

        # ── чаты и задача ──
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

        if route == "/chat/rename":
            payload = self._payload()
            if payload is None:
                return
            if not AGENT.rename(str(payload.get("title", ""))):
                self._send_json(400, {"error": "Имя занято или пустое"})
                return
            self._send_json(200, self._state())
            return

        if route == "/task/advance":
            payload = self._payload()
            if payload is None:
                return
            получилось, почему = AGENT.advance(
                str(payload.get("stage", "")).strip(), "вручную из окна")
            self._send_json(200, {**self._state(), "moved": получилось,
                                  "why": почему})
            return

        if route == "/reset":
            AGENT.reset()
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

            аудит = {}
            if AGENT.journal:
                последний = AGENT.journal[-1]
                if AGENT.auditing:
                    # Сообщаем отдельной строкой, что идёт проверка: она
                    # занимает пару секунд после того, как ответ уже дописан,
                    # и без подсказки выглядит зависанием.
                    self._line({"auditing": True})
                    try:
                        аудит = AGENT.audit(последний.answer)
                    except LLMError as exc:
                        аудит = {"error": str(exc)}
                if AGENT.tracking:
                    try:
                        AGENT._track_task(последний.question, последний.answer)
                    except LLMError:
                        pass
            self._line({"done": True, **self._state(), "audit": аудит})
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
            "invariants": [i.as_dict() for i in AGENT.invariants],
            "scopes": [{"scope": s_, "label": SCOPE_LABELS[s_]}
                       for s_ in INVARIANT_SCOPES],
            "task": AGENT.task_view(),
            "graph": {st: [{"stage": t, "label": STAGE_LABELS[t]}
                           for t in TRANSITIONS[st]] for st in STAGES},
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
    живых = len([i for i in AGENT.invariants if i.active])
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    print(f"Действует ограничений: {живых} из {len(AGENT.invariants)}")
    print("Проверка ответов включена: под каждым ответом видно, нарушено или нет.")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
