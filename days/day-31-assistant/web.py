#!/usr/bin/env python3
"""День 31: ассистент разработчика — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Окно показывает то, что в переписке обычно спрятано: откуда взят ответ.
Под каждым ответом видно, какие инструменты git дёрнул ассистент и какие
выдержки из документации он читал. Это и есть суть дня: источник важнее
складности текста.

Слева — состояние проекта живьём: ветка, незакоммиченное, последние
коммиты. Оно обновляется само, потому что меняется, пока вы работаете.
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

from assistant import Assistant, SERVERS  # noqa: E402
from check import QUESTIONS, mentions  # noqa: E402
from index import STRUCTURAL  # noqa: E402
from llm import DEFAULT_LOCAL_MODEL, LLMError  # noqa: E402

PAGE = (HERE / "ui.html").read_bytes()

# Два ассистента: облачный и локальный. Держим обоих поднятыми, чтобы
# переключение не перезапускало MCP-серверы на каждый вопрос.
ASSISTANTS: dict[str, Assistant] = {}


def assistant_for(local: bool) -> Assistant:
    key = "local" if local else "cloud"
    if key not in ASSISTANTS:
        ASSISTANTS[key] = Assistant(local=local)
    return ASSISTANTS[key]


class Handler(BaseHTTPRequestHandler):
    server_version = "day31/1.0"

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif route == "/state":
            self._send_json(200, self._state())
        elif route == "/project":
            self._send_json(200, self._project())
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        route = urlsplit(self.path).path
        payload = self._payload() or {}
        if route == "/help":
            self._help(payload)
            return
        if route == "/check":
            self._check(payload)
            return
        if route == "/clear":
            assistant_for(bool(payload.get("local"))).history.clear()
            self._send_json(200, {"ok": True})
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ── /help ───────────────────────────────────────────────────────────
    def _help(self, payload: dict) -> None:
        question = str(payload.get("question") or "").strip()
        local = bool(payload.get("local"))
        self._start_stream()
        if not question:
            self._line({"error": "Пустой вопрос"})
            return

        reference = next((q for q in QUESTIONS if q["q"] == question), None)
        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                answer = assistant_for(local).help(question)
                record = answer.as_dict()
                if reference:
                    called = [t["name"] for t in answer.used_tools]
                    record["kind"] = reference["kind"]
                    record["source_ok"] = (not called
                                           if reference["kind"] == "docs"
                                           else bool(set(called)
                                                     & set(reference["tools"])))
                    record["said_ok"] = mentions(answer.text,
                                                 reference["expect"])
                events.put({"answer": record})
            except LLMError as failure:
                outcome["error"] = str(failure)
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"{type(failure).__name__}: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── прогон проверки ─────────────────────────────────────────────────
    def _check(self, payload: dict) -> None:
        local = bool(payload.get("local"))
        self._start_stream()
        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                assistant = assistant_for(local)
                tally = {"state": [0, 0], "docs": [0, 0], "both": [0, 0]}
                wrong = 0
                events.put({"check_start": {"total": len(QUESTIONS),
                                            "local": local}})
                for number, item in enumerate(QUESTIONS, 1):
                    answer = assistant.help(item["q"])
                    called = [t["name"] for t in answer.used_tools]
                    source_ok = (not called if item["kind"] == "docs"
                                 else bool(set(called) & set(item["tools"])))
                    said_ok = mentions(answer.text, item["expect"])
                    ok = source_ok and said_ok
                    tally[item["kind"]][1] += 1
                    tally[item["kind"]][0] += ok
                    wrong += not source_ok
                    events.put({"row": {
                        "n": number, "question": item["q"],
                        "kind": item["kind"], "ok": ok,
                        "source_ok": source_ok, "said_ok": said_ok,
                        "called": called, "wanted": item["tools"],
                        "tokens": answer.tokens,
                        "seconds": round(answer.seconds, 1)}})
                events.put({"check_done": {"tally": tally, "wrong": wrong,
                                           "local": local}})
            except LLMError as failure:
                outcome["error"] = str(failure)
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

    def _project(self) -> dict:
        """Состояние проекта напрямую, без модели: быстро и дёшево."""
        assistant = assistant_for(False)
        out = {}
        for key, name in (("branch", "state__current_branch"),
                          ("work", "state__working_state"),
                          ("commits", "state__recent_commits")):
            record = assistant.tools.call(
                name, {"limit": 5} if name.endswith("recent_commits") else {})
            out[key] = record.result
        return out

    def _state(self) -> dict:
        assistant = assistant_for(False)
        stats = assistant.index.stats(STRUCTURAL)
        return {
            "index": {"chunks": stats["chunks"],
                      "documents": stats["documents"]},
            "mcp": assistant.tools.describe(),
            "tools": [{"name": n, "server": n.split("__")[0]}
                      for n in assistant.tools.names],
            "servers": list(SERVERS),
            "local_model": DEFAULT_LOCAL_MODEL,
            "questions": [{"n": n, "q": q["q"], "kind": q["kind"],
                           "tools": q["tools"]}
                          for n, q in enumerate(QUESTIONS, 1)],
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
    assistant = assistant_for(False)
    troubles = assistant.ready()
    stats = assistant.index.stats(STRUCTURAL)
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    print(f"  документы: {stats['chunks']} кусков из {stats['documents']}")
    print(f"  MCP: {assistant.tools.describe()}")
    for trouble in troubles:
        print(f"  ⚠ {trouble}")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        for item in ASSISTANTS.values():
            item.close()
