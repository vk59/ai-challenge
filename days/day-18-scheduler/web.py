#!/usr/bin/env python3
"""День 18: планировщик фоновых задач — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

В окне видно и настраивается всё: какие задачи заведены, как часто идут,
когда следующий запуск, что накопили. Агент при этом видит те же задачи
через MCP и может ими управлять словами.

Кто крутит фон. Фоновый поток живёт ТОЛЬКО в MCP-сервере (его запускает
это приложение как дочерний процесс). Окно работает с той же базой, но
своего потока не поднимает: иначе две копии планировщика выполняли бы
одну задачу по два раза.
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
from scheduler import Scheduler, SchedulerError  # noqa: E402
from tools import MCPToolset  # noqa: E402

# Исполнители берём из самого MCP-сервера, а не копируем: одна реализация
# на оба пути, и «выполнить сейчас» из окна делает ровно то же, что фон.
from server import ОПИСАНИЯ, исполнить_reminder, исполнить_repo_digest, исполнить_token_report  # noqa: E402

PAGE = (ЗДЕСЬ / "ui.html").read_bytes()

РОЛЬ = ("Ты — помощник по фоновым задачам. У тебя есть инструменты "
        "планировщика: посмотреть задачи, завести новую, выполнить сейчас, "
        "показать накопленную сводку. Опирайся на них, а не на догадки. "
        "Отвечай кратко и по делу.")

СЕРВЕРЫ = {"sched": [sys.executable, str(ЗДЕСЬ / "server.py")]}

TOOLSET = MCPToolset(СЕРВЕРЫ)
TOOLSET.open()

# Своя копия планировщика — для чтения и настройки из окна.
# Фон НЕ запускаем: он крутится в MCP-сервере.
ПЛАН = Scheduler()
ПЛАН.register("repo_digest", исполнить_repo_digest)
ПЛАН.register("token_report", исполнить_token_report)
ПЛАН.register("reminder", исполнить_reminder)

STORE = open_store("sqlite")
AGENT = Agent(name="Диспетчер", role=РОЛЬ, store=STORE,
              session=os.environ.get("AI_ADVENT_SESSION", DEFAULT_SESSION),
              temperature=0.3, max_tokens=1500, memory_turns=6)
AGENT.tools = TOOLSET
AGENT.layered = True


class Handler(BaseHTTPRequestHandler):
    server_version = "day18/1.0"

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif route == "/state":
            self._send_json(200, self._state())
        elif route.startswith("/digest/"):
            try:
                номер = int(route.rsplit("/", 1)[1])
                self._send_json(200, ПЛАН.digest(номер))
            except (ValueError, SchedulerError) as exc:
                self._send_json(400, {"error": str(exc)})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        route = urlsplit(self.path).path

        # ── задачи ──
        if route == "/task/add":
            payload = self._payload()
            if payload is None:
                return
            try:
                минут = int(payload.get("every_minutes") or 0)
                параметры = {}
                if payload.get("text"):
                    параметры["text"] = str(payload["text"])
                if payload.get("days"):
                    параметры["days"] = int(payload["days"])
                ПЛАН.add_task(str(payload.get("name") or ""),
                              str(payload.get("kind") or ""),
                              минут * 60, параметры,
                              start_now=bool(payload.get("start_now")))
            except (SchedulerError, ValueError) as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, self._state())
            return

        if route == "/task/update":
            payload = self._payload()
            if payload is None:
                return
            номер = int(payload.get("id") or 0)
            текущая = ПЛАН.task(номер)
            if текущая is None:
                self._send_json(400, {"error": f"Нет задачи {номер}"})
                return
            параметры = dict(текущая.params)
            if payload.get("text") is not None:
                параметры["text"] = str(payload["text"])
            if payload.get("days") is not None:
                параметры["days"] = int(payload["days"])
            минут = payload.get("every_minutes")
            try:
                ПЛАН.update(номер,
                            name=payload.get("name") or None,
                            interval=int(минут) * 60 if минут else None,
                            params=параметры)
            except SchedulerError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, self._state())
            return

        if route == "/task/toggle":
            payload = self._payload()
            if payload is None:
                return
            ПЛАН.toggle(int(payload.get("id") or 0),
                        bool(payload.get("enabled")))
            self._send_json(200, self._state())
            return

        if route == "/task/delete":
            payload = self._payload()
            if payload is None:
                return
            ПЛАН.delete(int(payload.get("id") or 0))
            self._send_json(200, self._state())
            return

        if route == "/task/run":
            payload = self._payload()
            if payload is None:
                return
            try:
                прогон = ПЛАН.run_task(int(payload.get("id") or 0))
            except SchedulerError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, {**self._state(),
                                  "run": {"ok": прогон.ok,
                                          "summary": прогон.summary,
                                          "seconds": прогон.seconds}})
            return

        # ── чаты ──
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

        события: queue.Queue = queue.Queue()
        итог: dict = {}

        def на_вызов(запись) -> None:
            события.put({"tool": {
                "name": запись.name, "args": запись.arguments,
                "preview": (запись.result or "").strip().splitlines()[:3],
                "lines": len((запись.result or "").splitlines()),
                "chars": len(запись.result or ""),
                "seconds": запись.seconds, "failed": запись.failed}})

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
        задачи = ПЛАН.tasks()
        return {
            "name": AGENT.name, "model": AGENT.model, "session": AGENT.session,
            "chats": AGENT.chats(),
            "mcp": TOOLSET.describe(),
            "tools": [{"name": с["function"]["name"],
                       "description": с["function"]["description"]}
                      for с in TOOLSET.schemas()],
            "kinds": [{"kind": к, "label": о} for к, о in ОПИСАНИЯ.items()],
            "tasks": [з.as_dict() for з in задачи],
            "enabled_count": sum(1 for з in задачи if з.enabled),
            "runs_total": sum(з.runs_count for з in задачи),
            "db": str(ПЛАН.path),
            "tokens": s.total_tokens, "spent": AGENT.spent_pretty,
            "layers": AGENT.layers(),
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
    задачи = ПЛАН.tasks()
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    print(f"База задач: {ПЛАН.path}")
    print(f"Задач: {len(задачи)}, включено {sum(1 for з in задачи if з.enabled)}")
    print(f"MCP: {TOOLSET.describe()} — фон крутится там")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        TOOLSET.close()
