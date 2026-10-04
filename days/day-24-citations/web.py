#!/usr/bin/env python3
"""День 24: цитаты и источники — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Окно показывает то, чего нет в терминале: цитата подсвечивается прямо
в тексте выданного куска. Видно, что фрагмент действительно оттуда —
не на слово проверялки, а глазами.

Плюс прогон десяти вопросов с тремя проверками задания и отдельная
панель для режима «не знаю».
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

from abstain_probe import ALIEN, NEARBY  # noqa: E402
from check import answer_has_fact, quotes_support_fact  # noqa: E402
from cite import ABSTAIN_BELOW, answer_with_citations, normalize  # noqa: E402
from embeddings import МОДЕЛЬ as EMBEDDING_MODEL  # noqa: E402
from index import STRUCTURAL, Index, IndexError_  # noqa: E402
from llm import LLMError  # noqa: E402
from questions import ВОПРОСЫ as QUESTIONS  # noqa: E402

PAGE = (HERE / "ui.html").read_bytes()

INDEX = Index()
RECENT: list[str] = []


def retrieval_for(question: str, rerank: bool):
    """Выдача с реранкингом дня 23, если попросили."""
    if not rerank:
        return None
    from modes import options_for
    from rerank import retrieve

    return retrieve(question, index=INDEX, **options_for("rerank"))


def find_spans(quote: str, text: str) -> list[list[int]]:
    """Где цитата лежит в тексте куска — для подсветки в окне.

    Ищем по нормализованному тексту, а отдаём смещения в исходном: иначе
    подсветка съедет на всех переносах строк и двойных пробелах.
    """
    needle = normalize(quote)
    if len(needle) < 12:
        return []
    # Карта: позиция в нормализованном тексте → позиция в исходном.
    mapping: list[int] = []
    flat: list[str] = []
    previous_space = True
    for position, char in enumerate(text):
        if char.isspace():
            if previous_space:
                continue
            flat.append(" ")
            mapping.append(position)
            previous_space = True
        else:
            flat.append(char.lower())
            mapping.append(position)
            previous_space = False
    hay = "".join(flat)

    spans: list[list[int]] = []
    start = hay.find(needle)
    if start != -1:
        finish = start + len(needle) - 1
        spans.append([mapping[start], mapping[finish] + 1])
        return spans

    # Цитата с многоточием: подсвечиваем части по отдельности.
    import re
    parts = [p for p in re.split(r"\s*(?:…|\.\.\.)\s*", needle) if len(p) > 3]
    position = 0
    for part in parts:
        found = hay.find(part, position)
        if found == -1:
            continue
        spans.append([mapping[found], mapping[found + len(part) - 1] + 1])
        position = found + len(part)
    return spans


class Handler(BaseHTTPRequestHandler):
    server_version = "day24/1.0"

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
        if route == "/check":
            self._check(self._payload() or {})
            return
        if route == "/abstain":
            self._abstain(self._payload() or {})
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ── один вопрос ─────────────────────────────────────────────────────
    def _ask(self, payload: dict) -> None:
        question = str(payload.get("question") or "").strip()
        rerank = bool(payload.get("rerank"))
        gate = payload.get("gate", True)

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
                extra: dict = {}
                if rerank:
                    extra["retrieval"] = retrieval_for(question, True)
                if not gate:
                    extra["abstain_below"] = None
                result = answer_with_citations(question, index=INDEX, **extra)
                record = result.as_dict()
                # Подсветку считаем на сервере: здесь уже есть и цитата,
                # и текст куска, а в окне пришлось бы повторять нормализацию.
                for quote, raw in zip(record["quotes"], result.quotes):
                    chunk = (result.chunks[raw.n - 1]
                             if 1 <= raw.n <= len(result.chunks) else None)
                    quote["spans"] = (find_spans(raw.text, chunk["text"])
                                      if chunk else [])
                if reference:
                    record["expect"] = ["/".join(v)
                                        for v in reference["expect"]]
                    record["answer_ok"] = answer_has_fact(result.answer,
                                                          reference["expect"])
                    record["support"] = quotes_support_fact(result,
                                                            reference["expect"])
                events.put({"answer": record})
            except (LLMError, IndexError_) as failure:
                outcome["error"] = str(failure)
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"Сбой: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── прогон десяти вопросов ──────────────────────────────────────────
    def _check(self, payload: dict) -> None:
        rerank = bool(payload.get("rerank"))
        self._start_stream()
        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                tally = {"sources": 0, "quotes": 0, "all_verified": 0,
                         "fabricated": 0, "support": 0, "answer_ok": 0,
                         "tokens": 0}
                for number, item in enumerate(QUESTIONS, 1):
                    extra: dict = {}
                    if rerank:
                        extra["retrieval"] = retrieval_for(item["q"], True)
                    result = answer_with_citations(item["q"], index=INDEX,
                                                   **extra)
                    support = quotes_support_fact(result, item["expect"])
                    answer_ok = answer_has_fact(result.answer, item["expect"])
                    tally["sources"] += result.has_sources
                    tally["quotes"] += result.has_quotes
                    tally["all_verified"] += (bool(result.quotes)
                                              and result.fabricated_quotes == 0)
                    tally["fabricated"] += result.fabricated_quotes
                    tally["support"] += support
                    tally["answer_ok"] += answer_ok
                    tally["tokens"] += result.total_tokens
                    events.put({"row": {
                        "n": number, "question": item["q"],
                        "expect": ["/".join(v) for v in item["expect"]],
                        "has_sources": result.has_sources,
                        "sources": [s.as_dict() for s in result.sources],
                        "has_quotes": result.has_quotes,
                        "verified": result.verified_quotes,
                        "fabricated": result.fabricated_quotes,
                        "support": support, "answer_ok": answer_ok,
                        "confident": result.confident,
                        "tokens": result.total_tokens}})
                events.put({"summary": tally, "total": len(QUESTIONS),
                            "rerank": rerank})
            except (LLMError, IndexError_) as failure:
                outcome["error"] = str(failure)
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"Сбой: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── режим «не знаю» ─────────────────────────────────────────────────
    def _abstain(self, payload: dict) -> None:
        self._start_stream()
        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        groups = [("свои", [q["q"] for q in QUESTIONS][:4], False),
                  ("чужие", ALIEN[:4], True),
                  ("околотемные", NEARBY[:4], True)]

        def work() -> None:
            try:
                saved = 0
                for title, questions, must_refuse in groups:
                    for question in questions:
                        result = answer_with_citations(question, index=INDEX)
                        if result.abstained:
                            kind, saved = "отказ до модели", saved + 1
                        elif not result.confident:
                            kind = "модель отказалась"
                        else:
                            kind = "ответил"
                        events.put({"probe": {
                            "group": title, "question": question,
                            "score": round(result.top_score, 3),
                            "kind": kind,
                            "gate": result.abstained,
                            "quotes": len(result.quotes),
                            "right": must_refuse == (result.abstained
                                                     or not result.confident),
                            "tokens": result.total_tokens}})
                events.put({"probe_done": True, "free_refusals": saved,
                            "threshold": ABSTAIN_BELOW})
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
            "strategy": STRUCTURAL,
            "index": {"chunks": stats["chunks"],
                      "documents": stats["documents"]},
            "questions": [{"n": n, "q": q["q"],
                           "expect": ["/".join(v) for v in q["expect"]],
                           "sources": sorted(q["sources"])}
                          for n, q in enumerate(QUESTIONS, 1)],
            "alien": ALIEN, "nearby": NEARBY,
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
    print(f"  порог отказа: {ABSTAIN_BELOW:.2f} по топ-1 близости")
    print(f"  контрольных вопросов: {len(QUESTIONS)}")
    if not stats["chunks"]:
        print("  ⚠ индекс пуст — соберите: "
              "cd ../day-21-indexing && python3 build.py")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
