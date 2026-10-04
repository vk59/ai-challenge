#!/usr/bin/env python3
"""День 22: RAG в двух режимах — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Окно показывает то, ради чего день: один вопрос, два ответа рядом.
Слева модель отвечает из памяти о мире, справа — по нашим документам,
со ссылками, которые можно проверить тут же.

Кнопка «10 контрольных вопросов» прогоняет весь набор в обоих режимах
и считает три исхода: попал, признался, соврал.
"""

import json
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ЗДЕСЬ = Path(__file__).resolve().parent
sys.path.insert(0, str(ЗДЕСЬ.parents[1] / "shared"))
sys.path.insert(0, str(ЗДЕСЬ))
sys.path.insert(0, str(ЗДЕСЬ.parents[0] / "day-21-indexing"))

from embeddings import МОДЕЛЬ as МОДЕЛЬ_ЭМБЕДДИНГОВ  # noqa: E402
from evaluate import исход  # noqa: E402
from index import СТРАТЕГИИ, STRUCTURAL, Index, IndexError_  # noqa: E402
from questions import ВОПРОСЫ  # noqa: E402
from rag import КУСКОВ, LLMError, спросить  # noqa: E402

PAGE = (ЗДЕСЬ / "ui.html").read_bytes()

ИНДЕКС = Index()
ИСТОРИЯ: list[str] = []


class Handler(BaseHTTPRequestHandler):
    server_version = "day22/1.0"

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

        if route == "/history/clear":
            ИСТОРИЯ.clear()
            self._send_json(200, self._state())
            return

        if route == "/ask":
            self._спросить(self._payload() or {})
            return

        if route == "/evaluate":
            self._прогнать(self._payload() or {})
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ── один вопрос в двух режимах ──────────────────────────────────────
    def _спросить(self, payload: dict) -> None:
        вопрос = str(payload.get("question") or "").strip()
        кусков = max(1, min(12, int(payload.get("chunks") or КУСКОВ)))
        режимы = [р for р in (payload.get("modes") or ["plain", "rag"])
                  if р in ("plain", "rag")] or ["plain", "rag"]

        self._начать_поток()
        if not вопрос:
            self._line({"error": "Пустой вопрос"})
            return

        if вопрос in ИСТОРИЯ:
            ИСТОРИЯ.remove(вопрос)
        ИСТОРИЯ.insert(0, вопрос)
        del ИСТОРИЯ[12:]

        # Ожидание из контрольного набора, если вопрос оттуда: тогда окно
        # сможет сразу показать вердикт, а не только текст ответа.
        эталон = next((в for в in ВОПРОСЫ if в["q"] == вопрос), None)

        события: queue.Queue = queue.Queue()
        итог: dict = {}

        def работа() -> None:
            try:
                for режим in режимы:
                    настройки = ({"индекс": ИНДЕКС, "кусков": кусков}
                                 if режим == "rag" else {})
                    о = спросить(вопрос, режим=режим, **настройки)
                    запись = о.as_dict()
                    if эталон:
                        запись["verdict"] = исход(о.text, эталон["expect"])
                        запись["expect"] = ["/".join(в) for в in эталон["expect"]]
                        if режим == "rag":
                            запись["sources_ok"] = bool(
                                set(о.sources) & эталон["sources"])
                    события.put({"answer": запись})
            except (LLMError, IndexError_) as сбой:
                итог["error"] = str(сбой)
            except Exception as сбой:                     # noqa: BLE001
                итог["error"] = f"Сбой: {сбой}"
            finally:
                события.put(None)

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
                self._line({"done": True, **self._state()})
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── весь контрольный набор ──────────────────────────────────────────
    def _прогнать(self, payload: dict) -> None:
        кусков = max(1, min(12, int(payload.get("chunks") or КУСКОВ)))
        self._начать_поток()

        события: queue.Queue = queue.Queue()
        итог: dict = {}

        def работа() -> None:
            try:
                счёт = {р: {"попал": 0, "признался": 0, "соврал": 0,
                            "источники": 0, "выдуманные": 0, "токенов": 0}
                        for р in ("plain", "rag")}
                for номер, в in enumerate(ВОПРОСЫ, 1):
                    строка = {"n": номер, "question": в["q"],
                              "expect": ["/".join(вар) for вар in в["expect"]],
                              "modes": {}}
                    for режим in ("plain", "rag"):
                        настройки = ({"индекс": ИНДЕКС, "кусков": кусков}
                                     if режим == "rag" else {})
                        о = спросить(в["q"], режим=режим, **настройки)
                        к = исход(о.text, в["expect"])
                        счёт[режим][к] += 1
                        счёт[режим]["токенов"] += о.total_tokens
                        верны = bool(set(о.sources) & в["sources"])
                        if режим == "rag":
                            счёт[режим]["источники"] += верны
                            счёт[режим]["выдуманные"] += len(о.invented)
                        строка["modes"][режим] = {
                            "verdict": к, "text": о.text,
                            "sources": о.sources, "sources_ok": верны,
                            "tokens": о.total_tokens,
                            "invented": о.invented}
                    события.put({"row": строка})
                события.put({"summary": счёт, "total": len(ВОПРОСЫ),
                             "chunks": кусков})
            except (LLMError, IndexError_) as сбой:
                итог["error"] = str(сбой)
            except Exception as сбой:                     # noqa: BLE001
                итог["error"] = f"Сбой: {сбой}"
            finally:
                события.put(None)

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
                self._line({"done": True, **self._state()})
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── вспомогательное ─────────────────────────────────────────────────
    def _state(self) -> dict:
        статистика = ИНДЕКС.stats(STRUCTURAL)
        return {
            "embedding_model": МОДЕЛЬ_ЭМБЕДДИНГОВ,
            "chunks_default": КУСКОВ,
            "strategy": STRUCTURAL,
            "strategies": list(СТРАТЕГИИ),
            "index": {"chunks": статистика["chunks"],
                      "documents": статистика["documents"]},
            "questions": [{"n": н, "q": в["q"],
                           "expect": ["/".join(вар) for вар in в["expect"]],
                           "sources": sorted(в["sources"]),
                           "why": в["why"]}
                          for н, в in enumerate(ВОПРОСЫ, 1)],
            "history": list(ИСТОРИЯ),
            "db": str(ИНДЕКС.path),
        }

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
    статистика = ИНДЕКС.stats(STRUCTURAL)
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    print(f"  индекс: {статистика['chunks']} кусков из "
          f"{статистика['documents']} документов ({STRUCTURAL})")
    print(f"  в промпт уходит выдержек: {КУСКОВ}")
    print(f"  контрольных вопросов: {len(ВОПРОСЫ)}")
    if not статистика["chunks"]:
        print("  ⚠ индекс пуст — соберите его: "
              "cd ../day-21-indexing && python3 build.py")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
