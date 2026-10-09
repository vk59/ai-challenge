#!/usr/bin/env python3
"""День 28: локальный RAG против облачного — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Два режима. Первый — один вопрос, три ответа рядом: две локальные модели
и облако. Видно сразу всё, ради чего день: кто ответил верно, за сколько
секунд и сколько цитат выдержало проверку дословности.

Второй — полный замер: десять вопросов, несколько повторов на каждый,
три оси сразу. Повторы нужны именно для третьей оси: разброс задержки
и воспроизводимость ответа однократным прогоном не увидеть.
"""

import json
import queue
import statistics
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

from bench import (DEFAULT_RUNS, ask_once, load_stack,  # noqa: E402
                   parse_stacks, stack_label, summarize)
from questions import ВОПРОСЫ as QUESTIONS  # noqa: E402

PAGE = (HERE / "ui.html").read_bytes()
STACKS = [("local", "qwen2.5:3b"), ("local", "qwen2.5:7b"), ("cloud", None)]

# Стеки держим загруженными: переключение перезагружает модули, и делать
# это на каждый вопрос значило бы мерить не модель, а импорт Python.
LOADED: dict[str, dict] = {}


def stack_modules(backend: str) -> dict:
    if backend not in LOADED:
        LOADED[backend] = load_stack(backend)
    else:
        # Переменная окружения общая, её надо вернуть перед обращением.
        import os
        os.environ["AI_ADVENT_EMBEDDINGS"] = backend
        load_stack(backend)
        LOADED[backend] = load_stack(backend)
    return LOADED[backend]


def describe_stack(backend: str, model: str) -> dict:
    mods = load_stack(backend)
    path = mods["index"].база_для(backend)
    stats = mods["index"].Index().stats("structural") if path.exists() else {}
    from llm import DEFAULT_LOCAL_MODEL
    return {
        "key": f"{backend}:{model or ''}",
        "label": stack_label(backend, model),
        "backend": backend,
        "answer_model": ("deepseek (облако)" if backend == "cloud"
                         else (model or DEFAULT_LOCAL_MODEL)),
        "embeddings": mods["embeddings"].модель_бэкенда(backend).split("/")[-1],
        "dim": mods["embeddings"].размерность_бэкенда(backend),
        "threshold": mods["cite"].порог_отказа(backend),
        "index": path.name,
        "chunks": stats.get("chunks", 0),
        "ready": path.exists(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "day28/1.0"

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
        payload = self._payload() or {}
        if route == "/compare":
            self._compare(payload)
            return
        if route == "/bench":
            self._bench(payload)
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ── один вопрос, три стека ──────────────────────────────────────────
    def _compare(self, payload: dict) -> None:
        question = str(payload.get("question") or "").strip()
        self._start_stream()
        if not question:
            self._line({"error": "Пустой вопрос"})
            return

        reference = next((q for q in QUESTIONS if q["q"] == question), None)
        item = reference or {"q": question, "expect": [], "sources": set()}

        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                for backend, model in STACKS:
                    mods = load_stack(backend)
                    attempt = ask_once(mods, backend, item, model)
                    attempt["label"] = stack_label(backend, model)
                    attempt["backend"] = backend
                    attempt["checked"] = reference is not None
                    events.put({"answer": attempt})
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"{type(failure).__name__}: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── полный замер ────────────────────────────────────────────────────
    def _bench(self, payload: dict) -> None:
        runs = max(1, min(5, int(payload.get("runs") or DEFAULT_RUNS)))
        self._start_stream()

        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                events.put({"bench_start": {
                    "runs": runs, "questions": len(QUESTIONS),
                    "stacks": [stack_label(b, m) for b, m in STACKS],
                    "total": len(QUESTIONS) * runs * len(STACKS)}})
                summaries = []
                for backend, model in STACKS:
                    label = stack_label(backend, model)
                    mods = load_stack(backend)
                    rows = []
                    for number, item in enumerate(QUESTIONS, 1):
                        attempts = [ask_once(mods, backend, item, model)
                                    for _ in range(runs)]
                        good = [a for a in attempts if not a["failed"]]
                        right = sum(1 for a in good if a["right"])
                        seconds = [a["seconds"] for a in good]
                        row = {
                            "n": number, "question": item["q"],
                            "runs": len(attempts), "ok": len(good),
                            "right": right,
                            "steady": right == 0 or right == len(good),
                            "sources_ok": sum(1 for a in good
                                              if a["sources_ok"]),
                            "verified": sum(a["verified"] for a in good),
                            "fabricated": sum(a["fabricated"] for a in good),
                            "abstained": sum(1 for a in good if a["abstained"]),
                            "blank": sum(1 for a in good if a["blank"]),
                            "parse_failed": sum(1 for a in good
                                                if a["parse_failed"]),
                            "tokens": sum(a["tokens"] for a in good),
                            "seconds": seconds,
                            "median": (statistics.median(seconds)
                                       if seconds else 0.0),
                        }
                        rows.append(row)
                        events.put({"row": {"label": label, **row}})
                    summary = summarize({"backend": backend, "rows": rows,
                                         "runs": runs, "label": label,
                                         "model": label})
                    summaries.append(summary)
                    events.put({"stack_done": summary})
                events.put({"bench_done": {"summaries": summaries}})
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
                self._line({"done": True})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _state(self) -> dict:
        return {
            "stacks": [describe_stack(b, m) for b, m in STACKS],
            "questions": [{"n": n, "q": q["q"],
                           "expect": ["/".join(v) for v in q["expect"]]}
                          for n, q in enumerate(QUESTIONS, 1)],
            "default_runs": DEFAULT_RUNS,
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
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    for backend, model in STACKS:
        info = describe_stack(backend, model)
        mark = "✓" if info["ready"] else "✗"
        print(f"  {mark} {info['label']:10} {info['answer_model']:18} "
              f"+ {info['embeddings']:24} {info['chunks']} кусков")
    print(f"  вопросов {len(QUESTIONS)} · повторов по умолчанию {DEFAULT_RUNS}")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
