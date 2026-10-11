#!/usr/bin/env python3
"""День 29: настройка 3B под задачу — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Два режима. Первый — один вопрос тремя вариантами рядом: база как в дне 28,
итоговый настроенный и 7B как цель. Видно, что изменилось в ответе,
в цитатах и в секундах.

Второй — прогон вариантов на всех вопросах с повторами. Он долгий, зато
показывает, какой приём сработал, а какой нет: температура и квантование
не дали почти ничего, а смена способа цитирования — дала.
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

import os  # noqa: E402

os.environ.setdefault("AI_ADVENT_EMBEDDINGS", "local")

from index import Index  # noqa: E402
from questions import ВОПРОСЫ as QUESTIONS  # noqa: E402
from tune import run_once, run_variant, summarize  # noqa: E402
from variants import TARGETS, VARIANTS  # noqa: E402

PAGE = (HERE / "ui.html").read_bytes()
INDEX = Index()

# Для сравнения на одном вопросе берём три показательных варианта.
SHOWCASE = ["base", "final", "7b-base"]

SAVED = HERE / "tune-все.json"


class Handler(BaseHTTPRequestHandler):
    server_version = "day29/1.0"

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
        if route == "/sweep":
            self._sweep(payload)
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ── один вопрос, три варианта ───────────────────────────────────────
    def _compare(self, payload: dict) -> None:
        question = str(payload.get("question") or "").strip()
        keys = [k for k in (payload.get("variants") or SHOWCASE)]
        self._start_stream()
        if not question:
            self._line({"error": "Пустой вопрос"})
            return

        reference = next((q for q in QUESTIONS if q["q"] == question), None)
        item = reference or {"q": question, "expect": [], "sources": set()}
        chosen = [v for v in VARIANTS if v["key"] in keys]

        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                for variant in chosen:
                    try:
                        result = run_once(variant, item, INDEX)
                    except Exception as failure:          # noqa: BLE001
                        result = {"failed": True,
                                  "why": f"{type(failure).__name__}: {failure}",
                                  "seconds": 0.0}
                    events.put({"answer": {
                        **result, "key": variant["key"],
                        "title": variant["title"], "note": variant["note"],
                        "model": variant["model"],
                        "chunks": variant["chunks"],
                        "checked": reference is not None}})
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"{type(failure).__name__}: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── прогон вариантов ────────────────────────────────────────────────
    def _sweep(self, payload: dict) -> None:
        runs = max(1, min(3, int(payload.get("runs") or 1)))
        keys = payload.get("variants") or [v["key"] for v in VARIANTS]
        chosen = [v for v in VARIANTS if v["key"] in keys]
        self._start_stream()

        events: queue.Queue = queue.Queue()
        outcome: dict = {}
        items = list(enumerate(QUESTIONS, 1))

        def work() -> None:
            try:
                events.put({"sweep_start": {
                    "variants": [v["key"] for v in chosen], "runs": runs,
                    "total": len(items) * runs * len(chosen)}})
                for variant in chosen:
                    summary = summarize(run_variant(variant, items, runs,
                                                    INDEX, False))
                    summary["title"] = variant["title"]
                    events.put({"summary": summary})
                events.put({"sweep_done": True})
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
        saved = []
        if SAVED.exists():
            try:
                saved = json.loads(SAVED.read_text(encoding="utf-8"))["summaries"]
            except (json.JSONDecodeError, KeyError):
                saved = []
        stats = INDEX.stats("structural")
        return {
            "variants": [{"key": v["key"], "title": v["title"],
                          "note": v["note"], "model": v["model"],
                          "temperature": v["temperature"],
                          "chunks": v["chunks"], "style": v["style"]}
                         for v in VARIANTS],
            "showcase": SHOWCASE,
            "targets": TARGETS,
            "saved": saved,
            "questions": [{"n": n, "q": q["q"],
                           "expect": ["/".join(v) for v in q["expect"]]}
                          for n, q in enumerate(QUESTIONS, 1)],
            "index": {"chunks": stats["chunks"], "file": INDEX.path.name},
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
    print(f"  вариантов {len(VARIANTS)}, вопросов {len(QUESTIONS)}")
    print(f"  индекс {INDEX.path.name}: "
          f"{INDEX.stats('structural')['chunks']} кусков")
    if SAVED.exists():
        print(f"  сохранённый прогон: {SAVED.name}")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
