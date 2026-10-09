#!/usr/bin/env python3
"""День 27: офлайн-помощник — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Приложение, которое не ходит в сеть. Главное в окне — не чат (он был
в дне 26), а счётчик обращений наружу: он считает попытки соединения
с любым адресом, кроме 127.0.0.1, и в офлайн-режиме обязан стоять на нуле.

Счётчик не декоративный. В дне 26 окно считалось локальным, а на каждую
реплику уходил запрос в OpenRouter за вектором вопроса — и это было
незаметно, пока не начали считать.
"""

import json
import queue
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

PAGE = (HERE / "ui.html").read_bytes()
LOCALHOST = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


class NetworkWatch:
    """Считает попытки выйти наружу. Не запрещает — только считает.

    Запрещать в работающем приложении нельзя: пользователь вправе
    переключиться на облако. А вот знать, ходили наружу или нет,
    он должен всегда — поэтому счётчик включён постоянно.
    """

    def __init__(self):
        self.outside = 0
        self.local = 0
        self.hosts: list[str] = []
        self._real = socket.socket.connect
        self._lock = threading.Lock()

    def install(self) -> None:
        real, lock = self._real, self._lock
        watch = self

        def counting(sock, address, *rest):
            host = address[0] if isinstance(address, tuple) else str(address)
            with lock:
                if host in LOCALHOST:
                    watch.local += 1
                else:
                    watch.outside += 1
                    if host not in watch.hosts:
                        watch.hosts.append(host)
            return real(sock, address, *rest)

        socket.socket.connect = counting

    def snapshot(self) -> dict:
        with self._lock:
            return {"outside": self.outside, "local": self.local,
                    "hosts": list(self.hosts)}

    def reset(self) -> None:
        with self._lock:
            self.outside = self.local = 0
            self.hosts.clear()


WATCH = NetworkWatch()
WATCH.install()

# Состояние пересобирается при смене бэкенда: индексы и пороги разные.
STATE: dict = {"backend": "local"}
SESSIONS: dict = {}


def switch_backend(backend: str) -> None:
    import os

    if STATE.get("backend") == backend and SESSIONS:
        return
    os.environ["AI_ADVENT_EMBEDDINGS"] = backend
    import importlib

    import cite
    import embeddings
    import index as index_module
    for module in (embeddings, index_module, cite):
        importlib.reload(module)
    import chat
    importlib.reload(chat)
    STATE["backend"] = backend
    STATE["modules"] = {"embeddings": embeddings, "index": index_module,
                        "cite": cite, "chat": chat}
    SESSIONS.clear()


def modules() -> dict:
    if "modules" not in STATE:
        switch_backend(STATE.get("backend", "local"))
    return STATE["modules"]


def session_for(backend: str):
    switch_backend(backend)
    if backend not in SESSIONS:
        mods = modules()
        from llm import DEFAULT_LOCAL_MODEL, LOCAL
        SESSIONS[backend] = mods["chat"].ChatSession(
            f"офлайн-{backend}", index=mods["index"].Index(),
            provider=None if backend == "cloud" else LOCAL,
            model=None if backend == "cloud" else DEFAULT_LOCAL_MODEL,
            rerank=False)
    return SESSIONS[backend]


class Handler(BaseHTTPRequestHandler):
    server_version = "day27/1.0"

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif route == "/state":
            self._send_json(200, self._state())
        elif route == "/net":
            self._send_json(200, WATCH.snapshot())
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        route = urlsplit(self.path).path
        payload = self._payload() or {}

        if route == "/net/reset":
            WATCH.reset()
            self._send_json(200, self._state())
            return
        if route == "/backend":
            backend = ("cloud" if str(payload.get("backend")) == "cloud"
                       else "local")
            switch_backend(backend)
            self._send_json(200, self._state())
            return
        if route == "/chat/clear":
            session = session_for(STATE.get("backend", "local"))
            mods = modules()
            session.store.clear(session.session)
            session.tasks.drop(session.session)
            session.task = session.tasks.load(session.session)
            self._send_json(200, self._state())
            return
        if route == "/ask":
            self._ask(payload)
            return
        if route == "/proof":
            self._proof()
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ── реплика ─────────────────────────────────────────────────────────
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
                before = WATCH.snapshot()
                session = session_for(STATE.get("backend", "local"))
                started = time.monotonic()
                reply = session.ask(message)
                after = WATCH.snapshot()
                events.put({"reply": {
                    **reply.as_dict(),
                    "wall_seconds": round(time.monotonic() - started, 2),
                    "net": {
                        "outside": after["outside"] - before["outside"],
                        "local": after["local"] - before["local"],
                        "hosts": [h for h in after["hosts"]
                                  if h not in before["hosts"]]},
                    "backend": STATE.get("backend")}})
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"{type(failure).__name__}: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── доказательство офлайна ──────────────────────────────────────────
    def _proof(self) -> None:
        self._start_stream()
        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            from offline_proof import Airgap, WentOutside

            dialogue = [
                "Какая стратегия нарезки документов победила в сравнении?",
                "А на сколько именно?",
                "Какая столица Австралии?",
            ]
            airgap = Airgap()
            try:
                session = session_for(STATE.get("backend", "local"))
                session.store.clear(session.session)
                session.tasks.drop(session.session)
                session.task = session.tasks.load(session.session)
                events.put({"proof_start": {
                    "backend": STATE.get("backend"), "steps": len(dialogue)}})
                with airgap:
                    for number, message in enumerate(dialogue, 1):
                        reply = session.ask(message)
                        events.put({"proof_step": {
                            "n": number, "message": message,
                            "query": reply.query, "rewritten": reply.rewritten,
                            "answer": reply.answer,
                            "sources": reply.sources,
                            "abstained": reply.abstained,
                            "confident": reply.cited.confident}})
                events.put({"proof_done": {"ok": True, "attempts": []}})
            except WentOutside as failure:
                events.put({"proof_done": {"ok": False,
                                           "attempts": airgap.attempts,
                                           "why": str(failure)}})
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"{type(failure).__name__}: {failure}"
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
        backend = STATE.get("backend", "local")
        mods = modules()
        index = mods["index"].Index()
        stats = index.stats("structural")
        session = session_for(backend)
        from llm import DEFAULT_LOCAL_MODEL
        return {
            "backend": backend,
            "embedding_model": mods["embeddings"].модель_бэкенда(backend),
            "dim": mods["embeddings"].размерность_бэкенда(backend),
            "answer_model": (DEFAULT_LOCAL_MODEL if backend == "local"
                             else "deepseek (облако)"),
            "threshold": mods["cite"].порог_отказа(backend),
            "index": {"chunks": stats["chunks"],
                      "documents": stats["documents"],
                      "file": mods["index"].база_для(backend).name},
            "net": WATCH.snapshot(),
            "task": session.task.as_dict(),
            "history": [{"role": t.role, "content": t.content}
                        for t in session.transcript()],
        }

    def _payload(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

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
    switch_backend("local")
    mods = modules()
    stats = mods["index"].Index().stats("structural")
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    print(f"  эмбеддинги: {mods['embeddings'].модель_бэкенда('local')} "
          f"· индекс {stats['chunks']} кусков")
    print(f"  порог отказа {mods['cite'].порог_отказа('local')}")
    print(f"  счётчик обращений наружу включён")
    if not stats["chunks"]:
        print("  ⚠ индекс пуст → AI_ADVENT_EMBEDDINGS=local "
              "python3 ../day-21-indexing/build.py")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
