#!/usr/bin/env python3
"""День 19: пайплайн MCP-инструментов — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Окно показывает то, ради чего день: как данные текут по цепочке
search → summarize → save_to_file, и что между шагами они передаются
по ссылке, а не через модель.

Два режима, и разница между ними — содержательная:

    «Запустить пайплайн»  шаги выполняются по очереди, детерминированно
    обычный вопрос        модель сама решает, какие инструменты вызвать

Первый режим стримит события по шагам: видно каждый переход и размеры
данных на входе и выходе.
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
from artifacts import ArtifactError, ArtifactStore  # noqa: E402
from memory import DEFAULT_SESSION, open_store  # noqa: E402
from tools import MCPToolset  # noqa: E402

# Исполнители шагов берём из самого MCP-сервера: одна реализация на оба
# пути, и пайплайн из окна делает ровно то же, что инструменты.
from server import выполнить_поиск, выполнить_сводку, выполнить_сохранение  # noqa: E402

PAGE = (ЗДЕСЬ / "ui.html").read_bytes()

РОЛЬ = ("Ты — помощник по пайплайну инструментов. Инструменты обмениваются "
        "НОМЕРАМИ артефактов, а не данными: search возвращает номер, "
        "summarize принимает номер и возвращает новый, save_to_file берёт "
        "номер и пишет файл. Выстраивай цепочку по номерам. Отвечай кратко.")

СЕРВЕРЫ = {"pipe": [sys.executable, str(ЗДЕСЬ / "server.py")]}

TOOLSET = MCPToolset(СЕРВЕРЫ)
TOOLSET.open()

# Читаем артефакты напрямую: интерфейсу нужны структурированные данные,
# а инструменты отдают текст для модели.
ХРАНИЛИЩЕ = ArtifactStore()

STORE = open_store("sqlite")
AGENT = Agent(name="Конвейер", role=РОЛЬ, store=STORE,
              session=os.environ.get("AI_ADVENT_SESSION", DEFAULT_SESSION),
              temperature=0.3, max_tokens=1500, memory_turns=6)
AGENT.tools = TOOLSET
AGENT.max_tool_rounds = 6      # цепочка из трёх шагов плюс запас


class Handler(BaseHTTPRequestHandler):
    server_version = "day19/1.0"

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif route == "/state":
            self._send_json(200, self._state())
        elif route.startswith("/artifact/"):
            try:
                номер = int(route.rsplit("/", 1)[1])
                self._send_json(200, ХРАНИЛИЩЕ.get(номер).as_dict(full=True))
            except (ValueError, ArtifactError) as exc:
                self._send_json(400, {"error": str(exc)})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        route = urlsplit(self.path).path

        if route == "/pipeline":
            payload = self._payload()
            if payload is None:
                return
            self._прогнать_пайплайн(payload)
            return

        if route == "/artifacts/clear":
            сколько = ХРАНИЛИЩЕ.clear()
            self._send_json(200, {**self._state(), "cleared": сколько})
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

        self._начать_поток()
        if not вопрос:
            self._line({"error": "Пустой запрос"})
            return

        события: queue.Queue = queue.Queue()
        итог: dict = {}

        def на_вызов(запись) -> None:
            события.put({"tool": {
                "name": запись.name, "args": запись.arguments,
                "preview": (запись.result or "").strip().splitlines()[:3],
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

    # ── пайплайн по шагам ───────────────────────────────────────────────
    def _прогнать_пайплайн(self, payload: dict) -> None:
        """Вызывает три инструмента по очереди и стримит каждый шаг.

        Здесь модель не участвует вовсе: шаги известны заранее, номера
        артефактов передаются из ответа в ответ. Это и есть «автоматическое
        выполнение цепочки» — в отличие от режима, где инструменты выбирает
        модель.
        """
        запрос = str(payload.get("query") or "").strip()
        self._начать_поток()
        if not запрос:
            self._line({"error": "Нужен запрос"})
            return

        имя_файла = str(payload.get("filename") or запрос)
        стиль = str(payload.get("style") or "")

        try:
            # Шаг 1
            self._line({"step": {"n": 1, "tool": "search", "state": "идёт",
                                 "note": f"ищу «{запрос}»"}})
            текст_поиска, мета_поиска = выполнить_поиск(запрос)
            найденное = ХРАНИЛИЩЕ.put("search", f"Поиск: {запрос}",
                                      текст_поиска, tool="search",
                                      meta=мета_поиска)
            self._line({"step": {"n": 1, "tool": "search", "state": "готово",
                                 "artifact": найденное.as_dict()}})

            # Шаг 2 — данные берутся ПО НОМЕРУ, а не из ответа шага 1
            self._line({"step": {"n": 2, "tool": "summarize", "state": "идёт",
                                 "note": f"сжимаю #{найденное.id} "
                                         f"({найденное.length} знаков)"}})
            исходный = ХРАНИЛИЩЕ.get(найденное.id)
            текст, мета = выполнить_сводку(исходный.content, стиль)
            сводка = ХРАНИЛИЩЕ.put("summary", f"Выжимка из #{исходный.id}",
                                   текст, tool="summarize",
                                   source_id=исходный.id, meta=мета)
            self._line({"step": {"n": 2, "tool": "summarize", "state": "готово",
                                 "artifact": сводка.as_dict(),
                                 "input_sha": исходный.sha,
                                 "input_len": исходный.length}})

            # Шаг 3
            self._line({"step": {"n": 3, "tool": "save_to_file", "state": "идёт",
                                 "note": f"сохраняю #{сводка.id}"}})
            путь, мета3 = выполнить_сохранение(сводка.content, имя_файла,
                                               f"Выжимка по запросу «{запрос}»")
            файл = ХРАНИЛИЩЕ.put("file", f"Файл {Path(путь).name}", путь,
                                 tool="save_to_file", source_id=сводка.id,
                                 meta=мета3)
            self._line({"step": {"n": 3, "tool": "save_to_file",
                                 "state": "готово", "artifact": файл.as_dict(),
                                 "input_len": сводка.length,
                                 "input_sha": сводка.sha,
                                 "path": путь, "bytes": мета3["bytes"]}})
            self._line({"done": True, **self._state(),
                        "chain": [а.as_dict() for а in ХРАНИЛИЩЕ.chain(файл.id)]})
        except (ValueError, ArtifactError, LLMError) as exc:
            self._line({"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── вспомогательное ─────────────────────────────────────────────────
    def _начать_поток(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _state(self) -> dict:
        s = AGENT.stats
        артефакты = ХРАНИЛИЩЕ.recent(limit=24)
        return {
            "name": AGENT.name, "model": AGENT.model, "session": AGENT.session,
            "chats": AGENT.chats(),
            "mcp": TOOLSET.describe(),
            "tools": [{"name": с["function"]["name"],
                       "description": с["function"]["description"]}
                      for с in TOOLSET.schemas()],
            "artifacts": [а.as_dict() for а in артефакты],
            "artifacts_total": len(артефакты),
            "tokens": s.total_tokens, "spent": AGENT.spent_pretty,
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
    print(f"Артефакты: {ХРАНИЛИЩЕ.path}")
    print(f"MCP: {TOOLSET.describe()}")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        TOOLSET.close()
