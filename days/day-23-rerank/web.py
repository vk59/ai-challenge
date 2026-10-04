#!/usr/bin/env python3
"""День 23: реранкинг и фильтрация — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Окно показывает то, что в CLI видно плохо: как второй этап ПЕРЕСТАВЛЯЕТ
куски. Для каждого куска видно место в исходной выдаче и место после
реранкинга, поэтому подъём с восьмого на первое читается одним взглядом.

Плюс кнопка прогона всех режимов на десяти вопросах с двумя мерами:
попал ли золотой кусок в промпт и правильный ли вышел ответ.
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

from answer import answer_question  # noqa: E402
from embeddings import МОДЕЛЬ as EMBEDDING_MODEL  # noqa: E402
from gold import is_gold  # noqa: E402
from evaluate_shim import verdict_of  # noqa: E402
from index import STRUCTURAL, Index, IndexError_  # noqa: E402
from llm import LLMError  # noqa: E402
from modes import FINAL_K, MODES, ORDER, THRESHOLD, WIDE_K  # noqa: E402
from questions import ВОПРОСЫ as QUESTIONS  # noqa: E402

PAGE = (HERE / "ui.html").read_bytes()

INDEX = Index()
RECENT: list[str] = []


class Handler(BaseHTTPRequestHandler):
    server_version = "day23/1.0"

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

        if route == "/recent/clear":
            RECENT.clear()
            self._send_json(200, self._state())
            return

        if route == "/ask":
            self._ask(self._payload() or {})
            return

        if route == "/evaluate":
            self._evaluate(self._payload() or {})
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ── один вопрос в выбранных режимах ─────────────────────────────────
    def _ask(self, payload: dict) -> None:
        question = str(payload.get("question") or "").strip()
        modes = [m for m in (payload.get("modes") or ORDER) if m in MODES]
        modes = modes or list(ORDER)

        self._start_stream()
        if not question:
            self._line({"error": "Пустой вопрос"})
            return

        if question in RECENT:
            RECENT.remove(question)
        RECENT.insert(0, question)
        del RECENT[12:]

        reference = next((q for q in QUESTIONS if q["q"] == question), None)
        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                for mode in modes:
                    result = answer_question(question, mode=mode, index=INDEX)
                    record = result.as_dict(full=True)
                    if reference:
                        record["verdict"] = verdict_of(result.text,
                                                       reference["expect"])
                        record["expect"] = ["/".join(v)
                                            for v in reference["expect"]]
                        record["sources_ok"] = bool(
                            set(result.sources) & reference["sources"])
                        record["gold_place"] = next(
                            (n for n, hit in enumerate(result.retrieval.hits, 1)
                             if is_gold(hit.chunk, reference)), 0)
                    events.put({"answer": record})
            except (LLMError, IndexError_) as failure:
                outcome["error"] = str(failure)
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"Сбой: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── все режимы на всех вопросах ─────────────────────────────────────
    def _evaluate(self, payload: dict) -> None:
        modes = [m for m in (payload.get("modes") or ORDER) if m in MODES]
        modes = modes or list(ORDER)

        self._start_stream()
        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                tally = {m: {"попал": 0, "признался": 0, "соврал": 0,
                             "context": 0, "sources": 0, "invented": 0,
                             "tokens": 0, "stage_tokens": 0}
                         for m in modes}
                for number, item in enumerate(QUESTIONS, 1):
                    row = {"n": number, "question": item["q"],
                           "expect": ["/".join(v) for v in item["expect"]],
                           "modes": {}}
                    for mode in modes:
                        result = answer_question(item["q"], mode=mode,
                                                 index=INDEX)
                        mark = verdict_of(result.text, item["expect"])
                        place = next(
                            (n for n, hit in enumerate(result.retrieval.hits, 1)
                             if is_gold(hit.chunk, item)), 0)
                        ok = bool(set(result.sources) & item["sources"])
                        box = tally[mode]
                        box[mark] += 1
                        box["context"] += bool(place)
                        box["sources"] += ok
                        box["invented"] += len(result.invented)
                        box["tokens"] += result.total_tokens
                        box["stage_tokens"] += result.stage_tokens
                        row["modes"][mode] = {
                            "verdict": mark, "gold_place": place,
                            "sources_ok": ok, "tokens": result.total_tokens,
                            "rewritten": result.retrieval.rewritten,
                            "query": result.retrieval.query}
                    events.put({"row": row})
                events.put({"summary": tally, "total": len(QUESTIONS),
                            "modes": modes})
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
            "wide_k": WIDE_K, "final_k": FINAL_K, "threshold": THRESHOLD,
            "strategy": STRUCTURAL,
            "modes": [{"name": m, "title": MODES[m]["title"],
                       "caption": MODES[m]["caption"]} for m in ORDER],
            "index": {"chunks": stats["chunks"],
                      "documents": stats["documents"]},
            "questions": [{"n": n, "q": q["q"],
                           "expect": ["/".join(v) for v in q["expect"]],
                           "sources": sorted(q["sources"])}
                          for n, q in enumerate(QUESTIONS, 1)],
            "recent": list(RECENT),
            "db": str(INDEX.path),
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
    print(f"  топ-K: {WIDE_K} до фильтрации → {FINAL_K} после, порог {THRESHOLD}")
    print(f"  режимов: {len(ORDER)} · вопросов: {len(QUESTIONS)}")
    if not stats["chunks"]:
        print("  ⚠ индекс пуст — соберите: "
              "cd ../day-21-indexing && python3 build.py")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
