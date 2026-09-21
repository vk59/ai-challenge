#!/usr/bin/env python3
"""День 12: персонализация — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

То же приложение, что в дне 11 (чаты, память, профиль), плюс главное для
этого дня — роут /compare. Он задаёт ОДИН вопрос через все профили подряд
и возвращает ответы рядом.

Это единственный честный способ проверить персонализацию: по одному ответу
не понять, профиль подействовал или модель просто так ответила. А когда
вопрос один, модель одна, температура одна, истории нет, и меняется только
профиль — вся разница в ответах принадлежит ему.
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
                    PROFILE, WORKING, Profile, open_store)

PAGE = Path(__file__).with_name("ui.html").read_bytes()

STORE = open_store("sqlite")
AGENT = Agent(name="Ассистент", store=STORE,
              session=os.environ.get("AI_ADVENT_SESSION", DEFAULT_SESSION),
              memory_turns=4)
AGENT.layered = True

# Заготовки: без них новый пользователь видит пустой список и не понимает,
# что вообще писать в поля профиля.
ЗАГОТОВКИ = [
    Profile("новичок", tone="дружелюбно и ободряюще", level="начинающий",
            format="пошагово, нумерованным списком, с коротким примером",
            language="русский"),
    Profile("senior", tone="сухо, как коллеге", level="senior, 10 лет опыта",
            format="только суть, без введения",
            constraints="не объяснять базовые понятия"),
    Profile("аудитор", tone="формально", level="специалист по безопасности",
            format="маркированный список требований",
            constraints="НЕ приводить примеры кода"),
]
if not AGENT.profiles():
    for заготовка in ЗАГОТОВКИ:
        STORE.save_profile(заготовка)


class Handler(BaseHTTPRequestHandler):
    server_version = "day12/1.0"

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

        # ── главное в этом дне ──
        if route == "/compare":
            payload = self._payload()
            if payload is None:
                return
            вопрос = str(payload.get("message", "")).strip()
            if not вопрос:
                self._send_json(400, {"error": "Нужен вопрос"})
                return
            self._compare(вопрос)
            return

        if route == "/profile/save":
            payload = self._payload()
            if payload is None:
                return
            имя = str(payload.get("name", "")).strip()
            if not имя:
                self._send_json(400, {"error": "У профиля должно быть имя"})
                return
            AGENT.save_profile(Profile(
                name=имя,
                tone=str(payload.get("tone", "")).strip(),
                format=str(payload.get("format", "")).strip(),
                level=str(payload.get("level", "")).strip(),
                constraints=str(payload.get("constraints", "")).strip(),
                language=str(payload.get("language", "")).strip(),
            ))
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

        if route == "/profile/delete":
            payload = self._payload()
            if payload is None:
                return
            имя = str(payload.get("name", "")).strip()
            if AGENT.store:
                AGENT.store.delete_profile(имя)
            if AGENT.profile and AGENT.profile.name == имя:
                AGENT.profile = None
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

        было_р = {f.key for f in AGENT.facts}
        было_д = {m.key for m in AGENT.memos}
        try:
            for piece in AGENT.stream(message):
                self._line({"delta": piece})
            if AGENT.journal:
                последний = AGENT.journal[-1]
                try:
                    AGENT._route(последний.question, последний.answer)
                except LLMError:
                    pass
            self._line({"done": True, **self._state(), "новое": {
                "working": sorted({f.key for f in AGENT.facts} - было_р),
                "long": sorted({m.key for m in AGENT.memos} - было_д)}})
        except LLMError as exc:
            self._line({"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── сравнение профилей ──────────────────────────────────────────────
    def _compare(self, вопрос: str) -> None:
        """Один вопрос через все профили. Отдаём потоком: ответов несколько,
        каждый идёт секунды, и ждать всё скопом — плохо."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        было = AGENT.profile
        # Отдельная сессия под сравнение: она одноразовая, и пачкать ею
        # настоящий чат нельзя. Плюс каждый прогон стартует с чистой
        # историей — иначе второй профиль отвечал бы, видя ответ первого.
        сессия = AGENT.session
        варианты = [None] + [p.name for p in AGENT.profiles()]
        try:
            for имя in варианты:
                AGENT.switch("__сравнение__")
                AGENT.reset()
                AGENT.use_profile(имя)
                подпись = имя or "без профиля"
                self._line({"start": подпись,
                            "prefs": AGENT.profile.as_prompt() if AGENT.profile else ""})
                try:
                    куски = []
                    for кусок in AGENT.stream(вопрос):
                        куски.append(кусок)
                        self._line({"name": подпись, "delta": кусок})
                    текст = "".join(куски)
                    self._line({"name": подпись, "end": True, "chars": len(текст),
                                "code": "```" in текст,
                                "tokens": AGENT.journal[-1].completion_tokens
                                if AGENT.journal else 0})
                except LLMError as exc:
                    self._line({"name": подпись, "error": str(exc)})
            AGENT.switch("__сравнение__")
            AGENT.reset()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            AGENT.switch(сессия)
            AGENT.profile = было
            try:
                self._line({"done": True, **self._state()})
            except (BrokenPipeError, ConnectionResetError, ValueError):
                pass

    # ── вспомогательное ─────────────────────────────────────────────────
    def _state(self) -> dict:
        s = AGENT.stats
        return {
            "name": AGENT.name, "model": AGENT.model, "session": AGENT.session,
            "chats": [c for c in AGENT.chats() if c["name"] != "__сравнение__"],
            "profiles": [p.as_dict() for p in AGENT.profiles()],
            "profile": AGENT.profile.name if AGENT.profile else "",
            "layers": AGENT.layers(),
            "turns": s.turns, "tokens": s.total_tokens,
            "overhead": s.overhead_tokens, "spent": AGENT.spent_pretty,
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
    print(f"Профили: {', '.join(p.name for p in AGENT.profiles()) or 'нет'}")
    print("Кнопка «Сравнить профили» задаёт один вопрос всем сразу.")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
