#!/usr/bin/env python3
"""День 30: нагрузка на сервис — устойчивость и ограничения.

    python3 loadtest.py                        # все проверки
    python3 loadtest.py --url http://192.168.1.122:8080
    python3 loadtest.py --clients 8 --each 3
    python3 loadtest.py --only rate            # одна проверка

Задание просит проверить три вещи, и каждая проверяется отдельно:

    доступ        отвечает ли сервис по сети и отбивает ли запрос без ключа
    устойчивость  что происходит при нескольких запросах разом. Локальная
                  модель не умеет в параллель, и важно, чтобы сервис
                  выстраивал очередь, а не падал и не выдавал мусор
    ограничения   срабатывают ли лимит запросов в минуту и потолок
                  контекста, и понятно ли сказано, что делать дальше

Проверка, которая всегда зелёная, бесполезна: лимиты тут нарочно
пробиваются, и ожидается именно отказ с нужным кодом.
"""

import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_URL = "http://127.0.0.1:8080"
# Ключ лежит там, куда смотрит сама служба. Запущенная из терминала,
# она пишет его в memory репозитория; поставленная в автозапуск —
# в Application Support. Ищем в обоих, иначе тест упирается в 401
# и выглядит так, будто сломан сервис.
KEY_FILES = [
    Path.home() / "Library/Application Support/AI Advent/memory/service-key.txt",
    Path.home() / "Documents/ai-challenge/memory/service-key.txt",
]


def read_key() -> str:
    from_env = os.environ.get("AI_ADVENT_SERVICE_KEY", "").strip()
    if from_env:
        return from_env
    for path in KEY_FILES:
        if path.exists():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key
    return ""


def post(url: str, key: str, payload: dict, timeout: float = 240.0) -> dict:
    """Запрос к сервису. Ошибки возвращаем, а не поднимаем: отказ сервиса
    в нагрузочном тесте — это результат, а не авария."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url + "/v1/chat/completions", body,
                                     headers)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            data = json.load(answer)
        return {"status": 200, "seconds": time.monotonic() - started,
                "data": data}
    except urllib.error.HTTPError as failure:
        raw = failure.read().decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"error": {"code": "unknown", "message": raw[:200]}}
        return {"status": failure.code, "seconds": time.monotonic() - started,
                "data": data, "retry_after": failure.headers.get("Retry-After")}
    except (urllib.error.URLError, OSError) as failure:
        return {"status": 0, "seconds": time.monotonic() - started,
                "data": {"error": {"code": "unreachable",
                                   "message": str(failure)}}}


def get(url: str, path: str, key: str = "") -> dict:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    request = urllib.request.Request(url + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:
            return {"status": 200, "data": json.load(answer)}
    except urllib.error.HTTPError as failure:
        return {"status": failure.code, "data": {}}
    except (urllib.error.URLError, OSError) as failure:
        return {"status": 0, "data": {"error": str(failure)}}


# ── проверка 1: доступ ──────────────────────────────────────────────────
def check_access(url: str, key: str) -> bool:
    print(f"\n{'─' * 72}\n1. Доступ по сети")
    health = get(url, "/health")
    if health["status"] != 200:
        print(f"   ✗ /health не ответил ({health['status']}). "
              f"Сервис запущен? Адрес верный?")
        return False
    data = health["data"]
    print(f"   ✓ /health отвечает: модель {data.get('model')}, "
          f"Ollama {'жив' if data.get('ollama') else 'МОЛЧИТ'}, "
          f"аптайм {data.get('uptime')}с")

    anonymous = post(url, "", {"messages": [{"role": "user",
                                             "content": "привет"}]})
    ok_closed = anonymous["status"] == 401
    print(f"   {'✓' if ok_closed else '✗'} без ключа: "
          f"{anonymous['status']} "
          f"{anonymous['data'].get('error', {}).get('code', '')}")

    with_key = post(url, key, {"messages": [{"role": "user",
                                             "content": "Скажи ровно: да"}]})
    ok_open = with_key["status"] == 200
    text = ""
    if ok_open:
        text = (with_key["data"].get("choices") or [{}])[0] \
            .get("message", {}).get("content", "").strip()
    print(f"   {'✓' if ok_open else '✗'} с ключом: {with_key['status']} "
          f"за {with_key['seconds']:.2f}с  «{text[:40]}»")
    return ok_closed and ok_open


# ── проверка 2: устойчивость ────────────────────────────────────────────
def check_stability(url: str, key: str, clients: int, each: int) -> bool:
    print(f"\n{'─' * 72}\n2. Устойчивость: {clients} клиентов по {each} "
          f"запросу разом")
    questions = [
        "Что такое наверстывание в планировщике?",
        "Какая стратегия нарезки победила?",
        "Зачем нужна память задачи?",
        "Как проверяются цитаты?",
    ]
    results: list[dict] = []
    lock = threading.Lock()

    def client(number: int) -> None:
        for round_number in range(each):
            question = questions[(number + round_number) % len(questions)]
            answer = post(url, key, {
                "messages": [{"role": "user", "content": question}],
                "max_tokens": 120})
            with lock:
                results.append(answer)

    started = time.monotonic()
    threads = [threading.Thread(target=client, args=(n,), daemon=True)
               for n in range(clients)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    spent = time.monotonic() - started

    ok = [r for r in results if r["status"] == 200]
    busy = [r for r in results if r["status"] == 503]
    limited = [r for r in results if r["status"] == 429]
    broken = [r for r in results if r["status"] not in (200, 429, 503)]
    times = sorted(r["seconds"] for r in ok)

    print(f"   всего {len(results)} запросов за {spent:.1f}с")
    print(f"   ✓ успешных {len(ok)}"
          + (f", занят {len(busy)}" if busy else "")
          + (f", лимит {len(limited)}" if limited else ""))
    if times:
        print(f"   задержка: медиана {statistics.median(times):.2f}с, "
              f"быстрее {times[0]:.2f}с, медленнее {times[-1]:.2f}с")
    # Сервис должен отвечать либо делом, либо понятным отказом.
    # Пятисотки и обрывы соединения — это поломка.
    if broken:
        print(f"   ✗ сломанных ответов: {len(broken)}")
        for bad in broken[:3]:
            print(f"      {bad['status']} "
                  f"{bad['data'].get('error', {}).get('message', '')[:80]}")
        return False
    print(f"   ✓ сломанных ответов нет: сервис либо отвечает, "
          f"либо честно отказывает")

    stats = get(url, "/stats", key)
    if stats["status"] == 200:
        now = stats["data"]
        print(f"   очередь доходила до {now.get('max_queue_seen')}, "
              f"сейчас в работе {now.get('now', {}).get('running')}")
    return True


# ── проверка 3: ограничения ─────────────────────────────────────────────
def check_limits(url: str, key: str) -> bool:
    print(f"\n{'─' * 72}\n3. Ограничения")
    stats = get(url, "/stats", key)
    limits = (stats["data"] or {}).get("limits", {})
    rate = int(limits.get("rate_per_minute") or 30)
    max_chars = int(limits.get("max_prompt_chars") or 24000)
    print(f"   объявлено: {rate} запросов в минуту, "
          f"промпт до {max_chars} знаков")

    big = post(url, key, {"messages": [
        {"role": "user", "content": "а" * (max_chars + 2000)}]})
    ok_size = big["status"] == 413
    print(f"   {'✓' if ok_size else '✗'} длинный промпт: {big['status']} "
          f"{big['data'].get('error', {}).get('code', '')}")
    if ok_size:
        print(f"      «{big['data']['error']['message'][:88]}»")

    # Лимит частоты пробиваем дешёвыми запросами: нам нужен отказ,
    # а не содержательный ответ, поэтому max_tokens минимальный.
    print(f"   бью лимит частоты: {rate + 5} запросов подряд…")
    codes: list[int] = []
    retry_after = None
    for _ in range(rate + 5):
        answer = post(url, key, {
            "messages": [{"role": "user", "content": "."}],
            "max_tokens": 1}, timeout=60)
        codes.append(answer["status"])
        if answer["status"] == 429:
            retry_after = answer.get("retry_after")
            break
    hit = 429 in codes
    print(f"   {'✓' if hit else '✗'} лимит сработал на "
          f"{len(codes)}-м запросе"
          + (f", Retry-After: {retry_after}с" if retry_after else ""))
    if not hit:
        print(f"      за {len(codes)} запросов отказа не было — "
              f"лимит либо выше, либо не работает")
    return ok_size and hit


def main() -> None:
    argv = sys.argv[1:]
    url = DEFAULT_URL
    if "--url" in argv:
        position = argv.index("--url")
        if position + 1 < len(argv):
            url = argv[position + 1].rstrip("/")
    clients, each = 6, 2
    if "--clients" in argv:
        position = argv.index("--clients")
        if position + 1 < len(argv):
            clients = max(1, int(argv[position + 1]))
    if "--each" in argv:
        position = argv.index("--each")
        if position + 1 < len(argv):
            each = max(1, int(argv[position + 1]))
    only = None
    if "--only" in argv:
        position = argv.index("--only")
        if position + 1 < len(argv):
            only = argv[position + 1]

    key = read_key()
    print(f"Сервис: {url}")
    print(f"Ключ: {'есть' if key else 'НЕТ — проверки с ключом упадут'}")

    outcomes = {}
    if only in (None, "access"):
        outcomes["доступ"] = check_access(url, key)
    if only in (None, "stability"):
        outcomes["устойчивость"] = check_stability(url, key, clients, each)
    if only in (None, "limits", "rate"):
        outcomes["ограничения"] = check_limits(url, key)

    print(f"\n{'═' * 72}")
    for name, ok in outcomes.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    bad = sum(1 for ok in outcomes.values() if not ok)
    print(f"\nпроверок не прошло: {bad}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
