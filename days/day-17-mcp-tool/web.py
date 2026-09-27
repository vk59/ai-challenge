#!/usr/bin/env python3
"""День 17: агент с MCP-инструментами — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Окно показывает то, ради чего день: агент сам решает вызвать инструмент,
и каждый вызов виден в ленте отдельной плашкой — что дёрнули, с какими
аргументами, что вернулось и за сколько.

Вызовы приходят браузеру по мере выполнения, а не пачкой в конце. Для
этого агенту передан колбэк on_tool, который складывает события в очередь,
а она отдаётся в поток ndjson.
"""

import json
import os
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ЗДЕСЬ = Path(__file__).resolve().parent
sys.path.insert(0, str(ЗДЕСЬ.parents[1] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import DEFAULT_SESSION, open_store  # noqa: E402
from tools import MCPToolset  # noqa: E402

PAGE = (ЗДЕСЬ / "ui.html").read_bytes()

РОЛЬ = ("Ты — летописец проекта AI Advent. У тебя есть инструменты, которые "
        "читают историю репозитория. Опирайся на них, а не на догадки: если "
        "спрашивают про конкретный день, файл или цифры — сначала посмотри. "
        "Отвечай кратко и по делу.")

СЕРВЕРЫ = {"git": [sys.executable, str(ЗДЕСЬ / "server.py")]}

# Набор живёт всё время работы приложения: поднимать MCP-сервер на каждый
# вопрос — значит платить запуском процесса за каждую реплику.
TOOLSET = MCPToolset(СЕРВЕРЫ)
TOOLSET.open()

STORE = open_store("sqlite")
AGENT = Agent(name="Летописец", role=РОЛЬ, store=STORE,
              session=os.environ.get("AI_ADVENT_SESSION", DEFAULT_SESSION),
              temperature=0.3, max_tokens=1500, memory_turns=6)
AGENT.tools = TOOLSET
# Наследие прошлых дней никуда не делось: память по слоям (день 11)
# и слежение за этапом задачи (день 13) продолжают работать, просто
# в окне они убраны под каты — главное здесь инструменты.
AGENT.layered = True
AGENT.tracking = True


class Handler(BaseHTTPRequestHandler):
    server_version = "day17/1.0"

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
            AGENT.reset()
            self._send_json(200, self._state())
            return

        if route == "/session":
            payload = self._payload()
            if payload is None:
                return
            AGENT.switch(str(payload.get("session", "")).strip() or DEFAULT_SESSION)
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

        if route != "/ask":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return

        payload = self._payload()
        if payload is None:
            return
        вопрос = str(payload.get("message", "")).strip()

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        if not вопрос:
            self._line({"error": "Пустой запрос"})
            return

        # Агент работает в отдельном потоке, события идут через очередь.
        # Иначе пришлось бы ждать весь цикл вызовов молча.
        события: queue.Queue = queue.Queue()
        итог: dict = {}

        def на_вызов(запись) -> None:
            события.put({"tool": {
                "name": запись.name,
                "args": запись.arguments,
                "preview": (запись.result or "").strip().splitlines()[:3],
                "lines": len((запись.result or "").splitlines()),
                "chars": len(запись.result or ""),
                "seconds": запись.seconds,
                "failed": запись.failed,
            }})

        def работа() -> None:
            try:
                итог["text"] = AGENT.ask(вопрос)
            except LLMError as exc:
                итог["error"] = str(exc)
            except Exception as exc:                 # noqa: BLE001
                итог["error"] = f"Сбой: {exc}"
            finally:
                события.put(None)

        AGENT.on_tool = на_вызов
        поток = threading.Thread(target=работа, daemon=True)
        поток.start()

        try:
            while True:
                событие = события.get()
                if событие is None:
                    break
                self._line(событие)
            поток.join(timeout=5)
            if "error" in итог:
                self._line({"error": итог["error"]})
            else:
                self._line({"answer": итог.get("text", "")})
                self._line({"done": True, **self._state()})
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            AGENT.on_tool = None

    # ── вспомогательное ─────────────────────────────────────────────────
    def _state(self) -> dict:
        s = AGENT.stats
        последний = AGENT.journal[-1] if AGENT.journal else None
        return {
            "name": AGENT.name,
            "model": AGENT.model,
            "session": AGENT.session,
            "chats": AGENT.chats(),
            "mcp": TOOLSET.describe(),
            "tools": [
                {"name": с["function"]["name"],
                 "description": с["function"]["description"],
                 "params": sorted((с["function"].get("parameters") or {})
                                  .get("properties", {}))}
                for с in TOOLSET.schemas()
            ],
            "calls_total": len(TOOLSET.log),
            "tokens": s.total_tokens,
            "spent": AGENT.spent_pretty,
            "last": ({"prompt": последний.prompt_tokens,
                      "completion": последний.completion_tokens}
                     if последний else {}),
            # Наследие дней 11-15. В окне это под катами, но данные
            # отдаются всегда: спрятать не значит выбросить.
            "layers": AGENT.layers(),
            "task": AGENT.task_view(),
            "profiles": [p.as_dict() for p in AGENT.profiles()],
            "profile": AGENT.profile.name if AGENT.profile else "",
            "invariants": [i.as_dict() for i in AGENT.invariants],
            "weigh": AGENT.weigh(),
            "history": [{"role": t.role, "content": t.content, "when": t.when}
                        for t in AGENT.transcript()],
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
    print(f"MCP: {TOOLSET.describe()}")
    print(f"Инструменты: {', '.join(TOOLSET.names)}")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        TOOLSET.close()
