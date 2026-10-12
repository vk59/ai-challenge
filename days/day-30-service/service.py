#!/usr/bin/env python3
"""День 30: приватный сервис на локальной модели.

    python3 service.py                    # только для этой машины
    python3 service.py --host 0.0.0.0     # доступен в локальной сети
    python3 service.py --port 8080 --model qwen2.5:7b
    python3 service.py --no-auth          # без ключа, только для отладки

Шлюз перед Ollama. Сам Ollama остаётся на 127.0.0.1 и наружу не смотрит:
у него нет ни ключей, ни ограничений, и выставлять его в сеть — значит
отдать свою машину любому, кто её найдёт.

Что добавляет шлюз:

    ключ          заголовок Authorization: Bearer <...>. Ключ создаётся
                  при первом запуске и лежит в файле с правами 600
    ограничения   запросов в минуту на ключ и потолок одновременных
    очередь       локальная модель не умеет в параллель: два запроса
                  делят одну видеопамять и оба идут вдвое дольше.
                  Поэтому они выполняются по очереди, а лишние ждут
    контекст      промпт длиннее лимита отвергается с понятной ошибкой,
                  а не молча обрезается

Эндпоинт OpenAI-совместимый, поэтому к сервису подходит любой клиент,
включая наш собственный shared/llm.py.
"""

import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from memory import default_dir  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:3b"
PAGE = (HERE / "ui.html")

# Ограничения. Числа не с потолка: на 14 ядрах одна модель отвечает
# за 1–3 секунды, значит минута — это примерно 20–40 запросов подряд.
# Берём 30 с запасом и один поток исполнения: два параллельных запроса
# к одной модели идут не быстрее, а вдвое дольше каждый.
RATE_PER_MINUTE = 30
MAX_CONCURRENT = 1
QUEUE_WAIT = 60.0          # сколько ждать очереди, прежде чем вернуть 503
MAX_PROMPT_CHARS = 24000   # потолок промпта, примерно 8 тысяч токенов
MAX_OUTPUT_TOKENS = 1200

KEY_FILE = default_dir() / "service-key.txt"


def load_or_make_key() -> str:
    """Ключ из файла; если файла нет — создаём и показываем один раз."""
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    key = "sk-local-" + secrets.token_urlsafe(24)
    KEY_FILE.write_text(key + "\n", encoding="utf-8")
    # Ключ в файле с правами на владельца: это не секрет уровня банка,
    # но и класть его так, чтобы прочитал любой процесс, незачем.
    os.chmod(KEY_FILE, 0o600)
    return key


@dataclass
class Limits:
    rate_per_minute: int = RATE_PER_MINUTE
    max_concurrent: int = MAX_CONCURRENT
    queue_wait: float = QUEUE_WAIT
    max_prompt_chars: int = MAX_PROMPT_CHARS
    max_output_tokens: int = MAX_OUTPUT_TOKENS


@dataclass
class Stats:
    started: float = field(default_factory=time.time)
    total: int = 0
    done: int = 0
    rejected_auth: int = 0
    rejected_rate: int = 0
    rejected_size: int = 0
    rejected_busy: int = 0
    failed: int = 0
    queued_now: int = 0
    running_now: int = 0
    max_queue_seen: int = 0
    seconds: list = field(default_factory=list)
    tokens: int = 0

    def snapshot(self) -> dict:
        times = sorted(self.seconds[-200:])
        median = times[len(times) // 2] if times else 0.0
        p95 = times[min(len(times) - 1, int(0.95 * (len(times) - 1)))] \
            if times else 0.0
        return {
            "uptime": round(time.time() - self.started),
            "total": self.total, "done": self.done, "failed": self.failed,
            "rejected": {"auth": self.rejected_auth,
                         "rate": self.rejected_rate,
                         "size": self.rejected_size,
                         "busy": self.rejected_busy},
            "now": {"queued": self.queued_now, "running": self.running_now},
            "max_queue_seen": self.max_queue_seen,
            "median": round(median, 2), "p95": round(p95, 2),
            "tokens": self.tokens,
        }


class RateLimiter:
    """Скользящее окно в минуту на каждый ключ."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, who: str) -> tuple[bool, int]:
        now = time.time()
        with self._lock:
            hits = self._hits.setdefault(who, deque())
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                # Сколько ждать до освобождения места.
                return False, int(61 - (now - hits[0]))
            hits.append(now)
            return True, 0

    def used(self, who: str) -> int:
        now = time.time()
        with self._lock:
            hits = self._hits.get(who) or deque()
            while hits and now - hits[0] > 60:
                hits.popleft()
            return len(hits)


class Gate:
    """Очередь исполнения: больше max_concurrent внутрь не пускаем."""

    def __init__(self, limit: int, wait: float, stats: Stats):
        self._sem = threading.Semaphore(limit)
        self._wait = wait
        self._stats = stats
        self._lock = threading.Lock()

    def __enter__(self):
        with self._lock:
            self._stats.queued_now += 1
            self._stats.max_queue_seen = max(self._stats.max_queue_seen,
                                             self._stats.queued_now)
        got = self._sem.acquire(timeout=self._wait)
        with self._lock:
            self._stats.queued_now -= 1
            if got:
                self._stats.running_now += 1
        if not got:
            raise TimeoutError("очередь не разошлась")
        return self

    def __exit__(self, *_):
        with self._lock:
            self._stats.running_now -= 1
        self._sem.release()


STATS = Stats()
LIMITS = Limits()
LIMITER = RateLimiter(LIMITS.rate_per_minute)
GATE = Gate(LIMITS.max_concurrent, LIMITS.queue_wait, STATS)
CONFIG: dict = {"model": DEFAULT_MODEL, "auth": True, "key": ""}


def ollama_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/version",
                                    timeout=3) as answer:
            json.load(answer)
        return True
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False


def call_ollama(payload: dict, timeout: float = 180.0) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{OLLAMA}/v1/chat/completions", body,
        {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return json.load(answer)


class Handler(BaseHTTPRequestHandler):
    server_version = "day30/1.0"
    protocol_version = "HTTP/1.1"

    # ── разбор и проверки ───────────────────────────────────────────────
    def _who(self) -> tuple[bool, str]:
        """Кто стучится. Возвращает (пустили, имя для лимитов)."""
        if not CONFIG["auth"]:
            return True, self.client_address[0]
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") \
            else ""
        if token and secrets.compare_digest(token, CONFIG["key"]):
            # Лимит на ключ, а не на адрес: один ключ с десяти устройств
            # должен упираться в тот же потолок.
            return True, "key:" + token[-6:]
        return False, self.client_address[0]

    def _payload(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            self._error(413, "payload_too_large", "Тело запроса слишком велико")
            return None
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._error(400, "bad_json", "Тело запроса — не JSON")
            return None

    # ── маршруты ────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/health":
            # Здоровье отдаём без ключа: иначе мониторинг пришлось бы
            # учить секретам, а ничего чувствительного тут нет.
            self._json(200, {"ok": True, "model": CONFIG["model"],
                             "ollama": ollama_alive(),
                             "uptime": round(time.time() - STATS.started)})
            return
        if route == "/stats":
            ok, _ = self._who()
            if not ok:
                return
            self._json(200, {**STATS.snapshot(), "limits": {
                "rate_per_minute": LIMITS.rate_per_minute,
                "max_concurrent": LIMITS.max_concurrent,
                "max_prompt_chars": LIMITS.max_prompt_chars,
                "max_output_tokens": LIMITS.max_output_tokens}})
            return
        if route == "/v1/models":
            ok, _ = self._who()
            if not ok:
                return
            self._json(200, {"object": "list", "data": [
                {"id": CONFIG["model"], "object": "model",
                 "owned_by": "local"}]})
            return
        if route in ("/", "/chat"):
            if PAGE.exists():
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(200, b"service is up", "text/plain; charset=utf-8")
            return
        self._error(404, "not_found", "Нет такого пути")

    def do_POST(self) -> None:
        route = urlsplit(self.path).path
        if route not in ("/v1/chat/completions", "/chat/completions"):
            self._error(404, "not_found", "Нет такого пути")
            return

        ok, who = self._who()
        if not ok:
            STATS.rejected_auth += 1
            self._error(401, "unauthorized",
                        "Нужен заголовок Authorization: Bearer <ключ>")
            return

        allowed, retry = LIMITER.allow(who)
        if not allowed:
            STATS.rejected_rate += 1
            self.send_response(429)
            self.send_header("Retry-After", str(retry))
            self._finish_json({"error": {
                "code": "rate_limited",
                "message": f"Не больше {LIMITS.rate_per_minute} запросов "
                           f"в минуту. Повторите через {retry} с."}})
            return

        payload = self._payload()
        if payload is None:
            return

        messages = payload.get("messages") or []
        size = sum(len(str(m.get("content") or "")) for m in messages)
        if size > LIMITS.max_prompt_chars:
            STATS.rejected_size += 1
            self._error(413, "context_too_long",
                        f"Промпт {size} знаков, потолок "
                        f"{LIMITS.max_prompt_chars}. Сократите контекст: "
                        f"молча обрезать запрос сервис не будет.")
            return

        STATS.total += 1
        started = time.monotonic()
        try:
            with GATE:
                answer = call_ollama({
                    "model": payload.get("model") or CONFIG["model"],
                    "messages": messages,
                    "temperature": payload.get("temperature", 0.3),
                    "max_tokens": min(int(payload.get("max_tokens")
                                          or LIMITS.max_output_tokens),
                                      LIMITS.max_output_tokens),
                    **({"response_format": payload["response_format"]}
                       if payload.get("response_format") else {}),
                })
        except TimeoutError:
            STATS.rejected_busy += 1
            self.send_response(503)
            self.send_header("Retry-After", "5")
            self._finish_json({"error": {
                "code": "busy",
                "message": "Сервис занят: очередь не разошлась за "
                           f"{int(LIMITS.queue_wait)} с. Попробуйте позже."}})
            return
        except urllib.error.HTTPError as failure:
            STATS.failed += 1
            detail = failure.read()[:300].decode("utf-8", "replace")
            self._error(502, "upstream_error",
                        f"Ollama ответил {failure.code}: {detail}")
            return
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as failure:
            STATS.failed += 1
            self._error(502, "upstream_down",
                        f"Ollama не отвечает: {failure}")
            return

        spent = time.monotonic() - started
        STATS.done += 1
        STATS.seconds.append(spent)
        usage = answer.get("usage") or {}
        STATS.tokens += int(usage.get("total_tokens") or 0)

        answer.setdefault("x_service", {})
        answer["x_service"] = {"seconds": round(spent, 2),
                               "queued": STATS.queued_now,
                               "used_this_minute": LIMITER.used(who),
                               "rate_per_minute": LIMITS.rate_per_minute}
        self._json(200, answer)

    # ── ответы ──────────────────────────────────────────────────────────
    def _error(self, status: int, code: str, message: str) -> None:
        self.send_response(status)
        self._finish_json({"error": {"code": code, "message": message}})

    def _json(self, status: int, payload: dict) -> None:
        self.send_response(status)
        self._finish_json(payload)

    def _finish_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {self.command} {self.path} — {args[1]}\n")


def lan_address() -> str:
    """Адрес машины в локальной сети — его и диктовать клиентам."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Пакет никуда не уходит, connect на UDP только выбирает маршрут.
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def main() -> None:
    # Без этого вывод буферизуется при запуске через nohup или systemd,
    # и ключ с адресом появляются в логе только после остановки сервиса.
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    argv = sys.argv[1:]
    host, port = "127.0.0.1", 8080
    if "--host" in argv:
        position = argv.index("--host")
        if position + 1 < len(argv):
            host = argv[position + 1]
    if "--port" in argv:
        position = argv.index("--port")
        if position + 1 < len(argv):
            port = int(argv[position + 1])
    if "--model" in argv:
        position = argv.index("--model")
        if position + 1 < len(argv):
            CONFIG["model"] = argv[position + 1]
    CONFIG["auth"] = "--no-auth" not in argv
    CONFIG["key"] = load_or_make_key() if CONFIG["auth"] else ""

    print(f"Приватный сервис на {CONFIG['model']}")
    print(f"  слушает {host}:{port}")
    if host in ("0.0.0.0", "::"):
        address = lan_address()
        print(f"  в локальной сети: http://{address}:{port}")
        print(f"  чат с телефона:   http://{address}:{port}/chat")
    else:
        print(f"  только с этой машины: http://127.0.0.1:{port}")
        print(f"  чтобы открыть сети:   --host 0.0.0.0")
    if CONFIG["auth"]:
        print(f"\n  ключ: {CONFIG['key']}")
        print(f"  лежит в {KEY_FILE}")
    else:
        print(f"\n  ⚠ запущен БЕЗ КЛЮЧА — только для отладки")
    print(f"\n  ограничения: {LIMITS.rate_per_minute} запросов в минуту, "
          f"{LIMITS.max_concurrent} одновременно,")
    print(f"               промпт до {LIMITS.max_prompt_chars} знаков, "
          f"ответ до {LIMITS.max_output_tokens} токенов")
    print(f"\n  Ollama: {'отвечает' if ollama_alive() else '⚠ НЕ ОТВЕЧАЕТ'}")
    print(f"\n  Проверка:")
    print(f"    curl http://{host if host != '0.0.0.0' else lan_address()}"
          f":{port}/health")
    print(f"\nCtrl+C — остановить")

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    main()
