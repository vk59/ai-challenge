#!/usr/bin/env python3
"""День 21: индекс документов — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Окно делает три вещи, которых не видно в CLI:

    поиск бок о бок      один запрос, две стратегии в двух колонках —
                         разница в метаданных и оценках видна сразу
    сборка индекса       с полосой прогресса и ценой в деньгах
    сравнение            прогон всех вопросов с hit@1, hit@3 и MRR

Главный кадр дня — две колонки выдачи: слева у куска написано «знаки
2964–4160», справа «Главные грабли: macOS не пускает приложение».
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

from compare import ВОПРОСЫ, ГЛУБИНА  # noqa: E402
from corpus import НА_СТРАНИЦЕ, собрать, сводка  # noqa: E402
from embeddings import (EmbeddingError, МОДЕЛЬ, РАЗМЕРНОСТЬ,  # noqa: E402
                        РАСХОД, сколько_в_кэше)
from index import (ПЕРЕХЛЁСТ, ПОТОЛОК, РАЗМЕР, СТРАТЕГИИ,  # noqa: E402
                   Index, IndexError_)

PAGE = (ЗДЕСЬ / "ui.html").read_bytes()

ИНДЕКС = Index()

ПОДПИСИ = {
    "fixed": f"окно {РАЗМЕР} знаков, перехлёст {ПЕРЕХЛЁСТ}",
    "structural": f"заголовки и определения, потолок {ПОТОЛОК}",
}

# Недавние запросы: в этом дне чата нет, а возвращаться к прошлому
# вопросу хочется ровно так же.
ИСТОРИЯ: list[str] = []


class Handler(BaseHTTPRequestHandler):
    server_version = "day21/1.0"

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

        if route == "/search":
            payload = self._payload()
            if payload is None:
                return
            запрос = str(payload.get("query") or "").strip()
            сколько = max(1, min(10, int(payload.get("k") or 5)))
            if not запрос:
                self._send_json(400, {"error": "Пустой запрос"})
                return
            if запрос in ИСТОРИЯ:
                ИСТОРИЯ.remove(запрос)
            ИСТОРИЯ.insert(0, запрос)
            del ИСТОРИЯ[12:]
            try:
                выдача = {
                    стратегия: [
                        {**кусок.as_dict(full=True), "score": round(оценка, 4)}
                        for кусок, оценка in ИНДЕКС.search(
                            запрос, стратегия, k=сколько)]
                    for стратегия in СТРАТЕГИИ
                }
            except (IndexError_, EmbeddingError) as сбой:
                self._send_json(400, {"error": str(сбой)})
                return
            self._send_json(200, {**self._state(), "query": запрос,
                                  "results": выдача})
            return

        if route == "/build":
            self._собрать(self._payload() or {})
            return

        if route == "/compare":
            self._сравнить()
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ── сборка индекса ──────────────────────────────────────────────────
    def _собрать(self, payload: dict) -> None:
        какие = [с for с in (payload.get("strategies") or СТРАТЕГИИ)
                 if с in СТРАТЕГИИ] or list(СТРАТЕГИИ)
        self._начать_поток()
        документы = собрать()
        self._line({"corpus": сводка(документы)})

        события: queue.Queue = queue.Queue()
        итог: dict = {}

        def работа() -> None:
            try:
                for стратегия in какие:
                    события.put({"stage": {"strategy": стратегия,
                                           "state": "идёт"}})

                    def шаг(сделано: int, всего: int, с=стратегия) -> None:
                        события.put({"progress": {"strategy": с,
                                                  "done": сделано,
                                                  "total": всего}})

                    статистика = ИНДЕКС.rebuild(документы, стратегия,
                                                на_шаг=шаг)
                    события.put({"stage": {"strategy": стратегия,
                                           "state": "готово",
                                           "stats": статистика}})
            except (IndexError_, EmbeddingError) as сбой:
                итог["error"] = str(сбой)
            except Exception as сбой:                     # noqa: BLE001
                итог["error"] = f"Сбой сборки: {сбой}"
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

    # ── сравнение стратегий ─────────────────────────────────────────────
    def _сравнить(self) -> None:
        self._начать_поток()
        события: queue.Queue = queue.Queue()
        итог: dict = {}

        def работа() -> None:
            try:
                счёт = {с: {"hit1": 0, "hit3": 0, "обратные": []}
                        for с in СТРАТЕГИИ}
                for номер, (вопрос, ожидаем) in enumerate(ВОПРОСЫ, 1):
                    строка = {"n": номер, "question": вопрос,
                              "expect": sorted(ожидаем), "places": {}}
                    for стратегия in СТРАТЕГИИ:
                        найдено = ИНДЕКС.search(вопрос, стратегия, k=ГЛУБИНА)
                        источники = [к.source for к, _ in найдено]
                        место = next((н for н, и in enumerate(источники, 1)
                                      if и in ожидаем), 0)
                        счёт[стратегия]["hit1"] += место == 1
                        счёт[стратегия]["hit3"] += 1 <= место <= 3
                        счёт[стратегия]["обратные"].append(
                            1 / место if место else 0.0)
                        строка["places"][стратегия] = {
                            "place": место, "top": источники[:3],
                            "section": найдено[0][0].section if найдено else "",
                        }
                    события.put({"row": строка})

                всего = len(ВОПРОСЫ)
                сводка_оценок = {
                    с: {"hit1": счёт[с]["hit1"], "hit3": счёт[с]["hit3"],
                        "questions": всего,
                        "mrr": round(sum(счёт[с]["обратные"]) / всего, 3)}
                    for с in СТРАТЕГИИ}
                события.put({"summary": сводка_оценок})
            except (IndexError_, EmbeddingError) as сбой:
                итог["error"] = str(сбой)
            except Exception as сбой:                     # noqa: BLE001
                итог["error"] = f"Сбой сравнения: {сбой}"
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
        return {
            "model": МОДЕЛЬ,
            "dim": РАЗМЕРНОСТЬ,
            "page_chars": НА_СТРАНИЦЕ,
            "strategies": [{"name": с, "caption": ПОДПИСИ[с],
                            "stats": ИНДЕКС.stats(с)} for с in СТРАТЕГИИ],
            "cached": сколько_в_кэше(),
            "spent": РАСХОД.как_словарь(),
            "history": list(ИСТОРИЯ),
            "questions": len(ВОПРОСЫ),
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
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    for стратегия in СТРАТЕГИИ:
        с = ИНДЕКС.stats(стратегия)
        состояние = (f"{с['chunks']} кусков, средняя {с['avg']}, "
                     f"разброс ±{с['spread']}") if с["chunks"] else "не собран"
        print(f"  {стратегия:11} {состояние}")
    print(f"  векторов в кэше: {сколько_в_кэше()}")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
