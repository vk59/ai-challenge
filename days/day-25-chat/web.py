#!/usr/bin/env python3
"""День 25: мини-чат с RAG и памятью задачи — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Производственная версия всего, что делалось по частям. В окне видно то,
что обычно спрятано: раскрытый запрос, источники под каждым ответом,
и панель памяти задачи, которая меняется по ходу разговора.

Плюс кнопка прогона двух длинных сценариев — с проверкой, что цель
не теряется и источники продолжают приходить.
"""

import json
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from chat import (HISTORY_FOR_ANSWER, HISTORY_FOR_REWRITE,  # noqa: E402
                  ChatSession, TaskStore)
from cite import ABSTAIN_BELOW  # noqa: E402
from embeddings import МОДЕЛЬ as EMBEDDING_MODEL  # noqa: E402
from index import STRUCTURAL, Index, IndexError_  # noqa: E402
from llm import LLMError  # noqa: E402
from memory import DEFAULT_SESSION, open_store  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

PAGE = (HERE / "ui.html").read_bytes()

INDEX = Index()
STORE = open_store("sqlite")
TASKS = TaskStore()
SESSION = ChatSession(DEFAULT_SESSION, store=STORE, index=INDEX, tasks=TASKS)


def goal_holds(goal: str, words: list[str]) -> bool:
    low = (goal or "").lower()
    return any(word in low for word in words)


class Handler(BaseHTTPRequestHandler):
    server_version = "day25/1.0"

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
        payload = self._payload() if route != "/scenario" else (self._payload() or {})

        if route == "/session":
            if payload is None:
                return
            SESSION.switch(str(payload.get("session", "")).strip()
                           or DEFAULT_SESSION)
            self._send_json(200, self._state())
            return

        if route == "/chat/rename":
            if payload is None:
                return
            if not SESSION.rename(str(payload.get("title", ""))):
                self._send_json(400, {"error": "Имя занято, пустое или "
                                               "чат ещё без реплик"})
                return
            self._send_json(200, self._state())
            return

        if route == "/chat/delete":
            if payload is None:
                return
            SESSION.drop(str(payload.get("session", "")).strip())
            self._send_json(200, self._state())
            return

        if route == "/task/forget":
            SESSION.forget_task()
            self._send_json(200, self._state())
            return

        if route == "/ask":
            self._ask(payload or {})
            return

        if route == "/scenario":
            self._scenario(payload or {})
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ── одна реплика ────────────────────────────────────────────────────
    def _ask(self, payload: dict) -> None:
        message = str(payload.get("message") or "").strip()
        self._start_stream()
        if not message:
            self._line({"error": "Пустая реплика"})
            return

        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                reply = SESSION.ask(message)
                events.put({"reply": reply.as_dict()})
            except (LLMError, IndexError_) as failure:
                outcome["error"] = str(failure)
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"Сбой: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── прогон сценария ─────────────────────────────────────────────────
    def _scenario(self, payload: dict) -> None:
        number = int(payload.get("n") or 1)
        self._start_stream()
        if not 1 <= number <= len(SCENARIOS):
            self._line({"error": f"Нет сценария {number}"})
            return

        scenario = SCENARIOS[number - 1]
        name = f"сценарий-{number}"
        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                STORE.clear(name)
                TASKS.drop(name)
                session = ChatSession(name, store=STORE, index=INDEX,
                                      tasks=TASKS)
                tally = {"turns": 0, "needed": 0, "got": 0, "goal_kept": 0,
                         "goal_checked": 0, "refusals": 0, "rewritten": 0,
                         "verified": 0, "fabricated": 0, "blanks": 0,
                         "tokens": 0}
                events.put({"scenario": {"n": number, "name": scenario["name"],
                                         "total": len(scenario["messages"]),
                                         "goal_words": scenario["goal_words"]}})
                for position, (message, kind) in enumerate(
                        scenario["messages"], 1):
                    reply = session.ask(message)
                    confident = reply.cited.confident and not reply.abstained
                    needed = confident and kind != "meta"
                    tally["turns"] += 1
                    tally["tokens"] += reply.total_tokens
                    tally["rewritten"] += reply.rewritten
                    tally["verified"] += reply.cited.verified_quotes
                    tally["fabricated"] += reply.cited.fabricated_quotes
                    tally["blanks"] += reply.cited.blank
                    if needed:
                        tally["needed"] += 1
                        tally["got"] += reply.has_sources
                    if not confident:
                        tally["refusals"] += 1
                    kept = None
                    if position >= 3:
                        tally["goal_checked"] += 1
                        kept = goal_holds(reply.task.goal,
                                          scenario["goal_words"])
                        tally["goal_kept"] += kept
                    events.put({"step": {
                        "n": position, "kind": kind, "message": message,
                        "query": reply.query, "rewritten": reply.rewritten,
                        "answer": reply.answer,
                        "sources": reply.sources,
                        "needed_sources": needed,
                        "has_sources": reply.has_sources,
                        "confident": confident, "abstained": reply.abstained,
                        "goal": reply.task.goal, "goal_kept": kept,
                        "verified": reply.cited.verified_quotes,
                        "fabricated": reply.cited.fabricated_quotes,
                        "tokens": reply.total_tokens}})
                events.put({"tally": tally,
                            "task": session.task.as_dict()})
            except (LLMError, IndexError_) as failure:
                outcome["error"] = str(failure)
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"Сбой: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── вспомогательное ─────────────────────────────────────────────────
    def _pump(self, events: queue.Queue, outcome: dict,
              worker: threading.Thread) -> None:
        worker.start()
        try:
            while True:
                event = events.get()
                if event is None:
                    break
                self._line(event)
            worker.join(timeout=5)
            if "error" in outcome:
                self._line({"error": outcome["error"]})
            else:
                self._line({"done": True, **self._state()})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _state(self) -> dict:
        stats = INDEX.stats(STRUCTURAL)
        return {
            "embedding_model": EMBEDDING_MODEL,
            "threshold": ABSTAIN_BELOW,
            "history_for_rewrite": HISTORY_FOR_REWRITE,
            "history_for_answer": HISTORY_FOR_ANSWER,
            "index": {"chunks": stats["chunks"],
                      "documents": stats["documents"]},
            "session": SESSION.session,
            "chats": SESSION.chats(),
            "task": SESSION.task.as_dict(),
            "history": [{"role": t.role, "content": t.content, "when": t.when}
                        for t in SESSION.transcript()],
            "scenarios": [{"n": n, "name": s["name"],
                           "messages": len(s["messages"]),
                           "goal_words": s["goal_words"]}
                          for n, s in enumerate(SCENARIOS, 1)],
        }

    def _payload(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Тело запроса — не JSON"})
            return None

    def _start_stream(self) -> None:
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
    stats = INDEX.stats(STRUCTURAL)
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    print(f"  индекс: {stats['chunks']} кусков из {stats['documents']} документов")
    print(f"  порог отказа {ABSTAIN_BELOW:.2f} · истории в запрос "
          f"{HISTORY_FOR_REWRITE} пар, в ответ {HISTORY_FOR_ANSWER}")
    print(f"  сценариев: {len(SCENARIOS)}")
    if not stats["chunks"]:
        print("  ⚠ индекс пуст — соберите: "
              "cd ../day-21-indexing && python3 build.py")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
