#!/usr/bin/env python3
"""День 26: локальная модель — сервер приложения.

    python3 web.py            # http://127.0.0.1:8000

Окно делает три вещи: показывает состояние Ollama и список моделей,
гоняет шесть запросов разной сложности с секундомером, и сравнивает
локальную модель с облаком на одних и тех же запросах.

Отдельная кнопка — главный опыт дня: те же вопросы, но с нашими
документами в промпте. Видно, что незнание маленькой модели лечится
поиском, а не только размером.

И главное — внизу обычный чат из дня 25, целиком на локальной модели:
история, раскрытие уточнений, источники под ответом, память задачи.
Вся цепочка без сети, стенд с замерами рядом для сравнения.
"""

import json
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from chat import ChatSession, TaskStore  # noqa: E402
from cite import answer_with_citations  # noqa: E402
from index import STRUCTURAL, Index  # noqa: E402
from memory import DEFAULT_SESSION, open_store  # noqa: E402
from llm import DEFAULT_LOCAL_MODEL, LOCAL, LLMError, ask  # noqa: E402
from probe import PROBES, measure_cold_start  # noqa: E402
from with_rag import PLAIN_SYSTEM, QUESTIONS, matches  # noqa: E402

PAGE = (HERE / "ui.html").read_bytes()
OLLAMA_API = "http://127.0.0.1:11434"
BINARIES = ("ollama", "/opt/homebrew/opt/ollama/bin/ollama")

INDEX = Index()
STORE = open_store("sqlite")
TASKS = TaskStore()
# Сессия чата пересобирается при смене модели: ChatSession держит
# провайдера, и подменять его на живом объекте значило бы получить
# историю, половина которой отвечена одной моделью, половина другой.
CHATS: dict[str, ChatSession] = {}


def pick_model(payload: dict) -> tuple[bool, str | None]:
    """Куда идём и с какой моделью.

    Имя локальной модели в облако посылать нельзя: DeepSeek отвечает
    «HTTP 400: supported API model names are deepseek-flash… but you passed
    qwen2.5:3b». Окно присылает выбранную слева модель всегда, поэтому
    отсекаем здесь, на сервере, а не надеемся на клиент.
    """
    cloud = bool(payload.get("cloud"))
    if cloud:
        return True, None
    model = str(payload.get("model") or "").strip()
    return False, model or None


def chat_session(model: str | None, cloud: bool) -> ChatSession:
    key = "cloud" if cloud else (model or DEFAULT_LOCAL_MODEL)
    name = f"локально-{key}" if not cloud else "облако"
    if key not in CHATS:
        CHATS[key] = ChatSession(
            name, store=STORE, index=INDEX, tasks=TASKS,
            provider=None if cloud else LOCAL,
            model=None if cloud else (model or DEFAULT_LOCAL_MODEL),
            # Реранкинг включён, хотя и стоит ещё одного запроса. Без него
            # локальный чат отказывался отвечать про наверстывание: нужный
            # кусок стоит в выдаче восьмым, а в промпт уходит пять. С ним
            # находится на втором месте. Цена — около шести секунд на
            # реплику вместо одной; точность того стоит.
            rerank=True)
    return CHATS[key]


def ollama_state() -> dict:
    """Запущен ли сервер, какие модели есть, что сейчас в памяти."""
    state = {"running": False, "version": "", "models": [], "loaded": []}
    try:
        with urllib.request.urlopen(f"{OLLAMA_API}/api/version",
                                    timeout=4) as answer:
            state["version"] = json.load(answer).get("version", "")
            state["running"] = True
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return state

    try:
        with urllib.request.urlopen(f"{OLLAMA_API}/api/tags",
                                    timeout=6) as answer:
            for item in json.load(answer).get("models") or []:
                state["models"].append({
                    "name": item.get("name", ""),
                    "size_gb": round((item.get("size") or 0) / 1e9, 1),
                    "family": (item.get("details") or {}).get("family", ""),
                    "params": (item.get("details") or {}).get(
                        "parameter_size", ""),
                    "quant": (item.get("details") or {}).get(
                        "quantization_level", "")})
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        pass

    try:
        with urllib.request.urlopen(f"{OLLAMA_API}/api/ps", timeout=6) as answer:
            state["loaded"] = [item.get("name", "")
                               for item in json.load(answer).get("models") or []]
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        pass
    return state


def unload(model: str) -> bool:
    for binary in BINARIES:
        try:
            subprocess.run([binary, "stop", model], capture_output=True,
                           timeout=30, check=False)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False


class Handler(BaseHTTPRequestHandler):
    server_version = "day26/1.0"

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

        if route == "/unload":
            ok = unload(str(payload.get("model") or DEFAULT_LOCAL_MODEL))
            self._send_json(200, {**self._state(), "unloaded": ok})
            return
        if route == "/probe":
            self._probe(payload)
            return
        if route == "/rag":
            self._rag(payload)
            return
        if route == "/ask":
            self._ask(payload)
            return
        if route == "/chat":
            self._chat(payload)
            return
        if route == "/chat/clear":
            cloud, model = pick_model(payload)
            session = chat_session(model, cloud)
            STORE.clear(session.session)
            TASKS.drop(session.session)
            session.task = session.tasks.load(session.session)
            self._send_json(200, {**self._state(),
                                  "chat": self._chat_state(model, cloud)})
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    # ── шесть запросов ──────────────────────────────────────────────────
    def _probe(self, payload: dict) -> None:
        cloud, model = pick_model(payload)
        cold = bool(payload.get("cold"))
        self._start_stream()

        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                provider = None if cloud else LOCAL
                cold_seconds = None
                if cold and not cloud:
                    cold_seconds = measure_cold_start(model)
                    events.put({"cold": cold_seconds})
                passed = 0
                for number, probe in enumerate(PROBES, 1):
                    name, level, prompt, check, limit = probe
                    started = time.monotonic()
                    answer = ask(prompt, provider=provider, model=model,
                                 max_tokens=limit, temperature=0.2)
                    spent = time.monotonic() - started
                    text = (answer.text or "").strip()
                    ok = bool(check(text))
                    passed += ok
                    out = int(answer.usage.get("completion_tokens") or 0)
                    events.put({"probe": {
                        "n": number, "name": name, "level": level,
                        "prompt": prompt, "answer": text, "ok": ok,
                        "seconds": round(spent, 2), "tokens": out,
                        "rate": round(out / spent, 1) if spent else 0.0}})
                events.put({"probe_done": {"passed": passed,
                                           "total": len(PROBES),
                                           "cloud": cloud,
                                           "model": model or DEFAULT_LOCAL_MODEL,
                                           "cold": cold_seconds}})
            except LLMError as failure:
                outcome["error"] = str(failure)
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"Сбой: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── RAG против чистой модели ────────────────────────────────────────
    def _rag(self, payload: dict) -> None:
        cloud, model = pick_model(payload)
        self._start_stream()

        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                provider = None if cloud else LOCAL
                plain_right = rag_right = verified = fabricated = 0
                for number, (question, wanted) in enumerate(QUESTIONS, 1):
                    bare = ask(question, system=PLAIN_SYSTEM,
                               provider=provider, model=model,
                               max_tokens=240, temperature=0.2)
                    bare_ok = matches(bare.text, wanted)
                    plain_right += bare_ok
                    cited = answer_with_citations(
                        question, index=INDEX, k=5, abstain_below=None,
                        provider=provider, model=model, max_tokens=700)
                    rag_ok = matches(cited.answer, wanted)
                    rag_right += rag_ok
                    verified += cited.verified_quotes
                    fabricated += cited.fabricated_quotes
                    events.put({"rag": {
                        "n": number, "question": question,
                        "wanted": wanted,
                        "plain": (bare.text or "").strip(), "plain_ok": bare_ok,
                        "rag": (cited.answer or "").strip(), "rag_ok": rag_ok,
                        "sources": [s.as_dict() for s in cited.sources],
                        "quotes": [q.as_dict() for q in cited.quotes],
                        "verified": cited.verified_quotes,
                        "fabricated": cited.fabricated_quotes}})
                events.put({"rag_done": {
                    "plain": plain_right, "rag": rag_right,
                    "total": len(QUESTIONS), "verified": verified,
                    "fabricated": fabricated,
                    "model": model or DEFAULT_LOCAL_MODEL, "cloud": cloud}})
            except LLMError as failure:
                outcome["error"] = str(failure)
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"Сбой: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── свободный вопрос ────────────────────────────────────────────────
    def _ask(self, payload: dict) -> None:
        question = str(payload.get("question") or "").strip()
        cloud, model = pick_model(payload)
        self._start_stream()
        if not question:
            self._line({"error": "Пустой вопрос"})
            return

        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                started = time.monotonic()
                answer = ask(question, system=PLAIN_SYSTEM,
                             provider=None if cloud else LOCAL, model=model,
                             max_tokens=400, temperature=0.3)
                spent = time.monotonic() - started
                out = int(answer.usage.get("completion_tokens") or 0)
                events.put({"answer": {
                    "text": (answer.text or "").strip(),
                    "model": answer.model, "seconds": round(spent, 2),
                    "tokens": out,
                    "rate": round(out / spent, 1) if spent else 0.0,
                    "where": "облако" if cloud else "локально"}})
            except LLMError as failure:
                outcome["error"] = str(failure)
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"Сбой: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    # ── чат на локальной модели ─────────────────────────────────────────
    def _chat(self, payload: dict) -> None:
        message = str(payload.get("message") or "").strip()
        cloud, model = pick_model(payload)
        self._start_stream()
        if not message:
            self._line({"error": "Пустая реплика"})
            return

        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work() -> None:
            try:
                session = chat_session(model, cloud)
                started = time.monotonic()
                reply = session.ask(message)
                events.put({"turn": {
                    **reply.as_dict(),
                    "wall_seconds": round(time.monotonic() - started, 2),
                    "where": "облако" if cloud else (model or DEFAULT_LOCAL_MODEL),
                    "session": session.session}})
            except LLMError as failure:
                outcome["error"] = str(failure)
            except Exception as failure:                  # noqa: BLE001
                outcome["error"] = f"Сбой: {failure}"
            finally:
                events.put(None)

        self._pump(events, outcome, threading.Thread(target=work, daemon=True))

    def _chat_state(self, model: str | None, cloud: bool) -> dict:
        session = chat_session(model, cloud)
        return {
            "session": session.session,
            "task": session.task.as_dict(),
            "history": [{"role": t.role, "content": t.content}
                        for t in session.transcript()],
        }

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
            "ollama": ollama_state(),
            "default_model": DEFAULT_LOCAL_MODEL,
            "endpoint": LOCAL.base_url,
            "index": {"chunks": stats["chunks"],
                      "documents": stats["documents"]},
            "probes": [{"n": n, "name": p[0], "level": p[1], "prompt": p[2]}
                       for n, p in enumerate(PROBES, 1)],
            "rag_questions": [{"n": n, "question": q, "wanted": w}
                              for n, (q, w) in enumerate(QUESTIONS, 1)],
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
    state = ollama_state()
    print(f"Открой http://127.0.0.1:{port}  (Ctrl+C — остановить)")
    if state["running"]:
        print(f"  Ollama {state['version']} на {LOCAL.base_url}")
        for item in state["models"]:
            print(f"    {item['name']:18} {item['size_gb']:>4} ГБ  "
                  f"{item['params']} {item['quant']}")
    else:
        print("  ⚠ Ollama не отвечает. Запустите: ollama serve")
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
