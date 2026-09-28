#!/usr/bin/env python3
"""День 20: оркестрация нескольких MCP-серверов — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Три сервера подключены разом (дни 17, 18, 19), 19 инструментов в одном
списке. Инструмент выбирает агент в чате — ни кнопок «вызвать search»,
ни жёсткой цепочки здесь нет.

Окно показывает то, что иначе не видно: к КАКОМУ серверу ушёл каждый
вызов и в каком порядке. Маршрут — главный артефакт этого дня, поэтому
он живёт отдельной панелью и не пропадает после ответа.
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
sys.path.insert(0, str(ЗДЕСЬ))

from agent import Agent, LLMError  # noqa: E402
from memory import DEFAULT_SESSION, open_store  # noqa: E402
from routing import ОБЛАСТИ, РОЛЬ, СЕРВЕРЫ  # noqa: E402
from tools import MCPToolset  # noqa: E402
from flow import СЦЕНАРИИ, проверить  # noqa: E402

PAGE = (ЗДЕСЬ / "ui.html").read_bytes()

TOOLSET = MCPToolset(СЕРВЕРЫ)
TOOLSET.open()

STORE = open_store("sqlite")
AGENT = Agent(name="Диспетчер", role=РОЛЬ, store=STORE,
              session=os.environ.get("AI_ADVENT_SESSION", DEFAULT_SESSION),
              temperature=0.25, max_tokens=1500, memory_turns=6)
AGENT.tools = TOOLSET
# Длинный флоу дня — пять вызовов; запас нужен на уточняющие.
AGENT.max_tool_rounds = 10

# Маршрут последнего запроса: его рисует панель справа.
МАРШРУТ: list[dict] = []


def сервер_из(имя: str) -> str:
    return имя.split("__", 1)[0]


class Handler(BaseHTTPRequestHandler):
    server_version = "day20/1.0"

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

        if route == "/flow/clear":
            МАРШРУТ.clear()
            self._send_json(200, self._state())
            return

        if route == "/session":
            payload = self._payload()
            if payload is None:
                return
            AGENT.switch(str(payload.get("session", "")).strip() or DEFAULT_SESSION)
            МАРШРУТ.clear()
            self._send_json(200, self._state())
            return

        if route == "/chat/rename":
            payload = self._payload()
            if payload is None:
                return
            if not AGENT.rename(str(payload.get("title", ""))):
                self._send_json(400, {"error": "Имя занято, пустое или чат ещё без реплик"})
                return
            self._send_json(200, self._state())
            return

        if route == "/chat/delete":
            payload = self._payload()
            if payload is None:
                return
            AGENT.drop(str(payload.get("session", "")).strip())
            МАРШРУТ.clear()
            self._send_json(200, self._state())
            return

        if route != "/ask":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return

        payload = self._payload()
        if payload is None:
            return
        вопрос = str(payload.get("message", "")).strip()
        # Если запрос пришёл из готового сценария — сверим маршрут с ожиданием.
        номер = payload.get("scenario")
        сценарий = (СЦЕНАРИИ[int(номер) - 1]
                    if isinstance(номер, int) and 1 <= номер <= len(СЦЕНАРИИ)
                    else None)

        self._начать_поток()
        if not вопрос:
            self._line({"error": "Пустой запрос"})
            return

        МАРШРУТ.clear()
        self._line({"flow_reset": True})

        события: queue.Queue = queue.Queue()
        итог: dict = {}
        было = len(TOOLSET.log)

        def на_вызов(запись) -> None:
            шаг = {
                "n": len(МАРШРУТ) + 1,
                "server": сервер_из(запись.name),
                "tool": запись.name.split("__", 1)[1],
                "name": запись.name,
                "args": запись.arguments,
                "preview": (запись.result or "").strip().splitlines()[:3],
                "chars": len(запись.result or ""),
                "seconds": запись.seconds,
                "failed": запись.failed,
            }
            МАРШРУТ.append(шаг)
            события.put({"step": шаг})

        def работа() -> None:
            try:
                итог["text"] = AGENT.ask(вопрос)
            except LLMError as exc:
                итог["error"] = str(exc)
            except Exception as exc:                      # noqa: BLE001
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
                return

            self._line({"answer": итог.get("text", "")})
            хвост = {"done": True, **self._state()}
            if сценарий is not None:
                вызовы = [з.name for з in TOOLSET.log[было:]]
                хвост["checks"] = [{"ok": ладно, "text": текст}
                                   for ладно, текст in проверить(сценарий, вызовы)]
            self._line(хвост)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            AGENT.on_tool = None

    # ── вспомогательное ─────────────────────────────────────────────────
    def _state(self) -> dict:
        s = AGENT.stats
        по_серверам: dict[str, list[str]] = {}
        for полное in TOOLSET.names:
            по_серверам.setdefault(сервер_из(полное), []).append(
                полное.split("__", 1)[1])
        return {
            "name": AGENT.name, "model": AGENT.model, "session": AGENT.session,
            "chats": AGENT.chats(),
            "servers": [{"name": имя, "area": ОБЛАСТИ.get(имя, ""),
                         "tools": сколько}
                        for имя, сколько in sorted(по_серверам.items())],
            "mcp": TOOLSET.describe(),
            "flow": list(МАРШРУТ),
            "scenarios": [{"n": н, "title": с["имя"], "prompt": с["запрос"]}
                          for н, с in enumerate(СЦЕНАРИИ, 1)],
            "tokens": s.total_tokens, "spent": AGENT.spent_pretty,
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

    def _начать_поток(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

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
    print(f"Серверов: {len(СЕРВЕРЫ)} · {TOOLSET.describe()}")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        TOOLSET.close()
