#!/usr/bin/env python3
"""День 26: запросы к локальной модели, от простого к сложному.

    python3 probe.py                 # все запросы
    python3 probe.py --model qwen2.5:7b
    python3 probe.py --cloud         # то же самое в облаке, для сравнения
    python3 probe.py 2 5             # только выбранные

Задание просит минимум три запроса разной сложности. Их здесь шесть, и
подобраны они так, чтобы было видно, где трёхмиллиардная модель на ноутбуке
справляется, а где начинает выдумывать.

У каждого запроса есть машинная проверка — иначе «отвечает» значит только
«что-то написала». Проверки нарочно простые: искомая подстрока, число,
разбираемый JSON.

Холодный старт мерится отдельно, флагом `--cold`: он выгружает модель
из памяти и замеряет первый запрос. Считать холодным просто первый запрос
подряд нельзя — модель могла остаться в памяти с прошлого прогона, и тогда
цифра получается бессмысленной. У меня так и вышло в первой версии:
«первый запрос 0.11с, в нём загрузка модели» — при настоящей загрузке
в 0.62с.
"""

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from llm import DEFAULT_LOCAL_MODEL, LOCAL, LLMError, ask  # noqa: E402


def has(text: str, *needles: str) -> bool:
    low = (text or "").lower()
    return all(n.lower() in low for n in needles)


def is_json_with(text: str, *keys: str) -> bool:
    block = re.search(r"\{.*\}", text or "", re.S)
    if not block:
        return False
    try:
        data = json.loads(block.group(0))
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and all(k in data for k in keys)


# (название, сложность, промпт, проверка, max_tokens)
PROBES: list[tuple[str, str, str, callable, int]] = [
    ("факт одним словом", "простое",
     "Ответь одним словом без пояснений: столица Японии?",
     lambda t: has(t, "токио"), 20),

    ("следование формату", "простое",
     "Верни ТОЛЬКО JSON вида {\"city\": \"...\", \"country\": \"...\"} "
     "для города Лиссабон.",
     lambda t: is_json_with(t, "city", "country"), 80),

    ("арифметика в три шага", "среднее",
     "В корзине 17 яблок. Три забрали, потом добавили вдвое больше, "
     "чем забрали. Сколько яблок стало? Ответь числом и покажи вычисление.",
     lambda t: has(t, "20"), 200),

    ("русский язык и стиль", "среднее",
     "Перепиши канцелярит человеческим языком, одним предложением: "
     "«Осуществление процедуры верификации пользовательских данных "
     "производится в автоматическом режиме».",
     lambda t: len((t or "").split()) >= 4 and "осуществл" not in (t or "").lower(),
     120),

    ("код по описанию", "сложное",
     "Напиши функцию на Python: принимает список чисел, возвращает "
     "второе по величине уникальное значение или None, если его нет. "
     "Только код, без объяснений.",
     lambda t: has(t, "def") and ("set(" in (t or "") or "sorted" in (t or "")),
     260),

    ("знание предметной области", "сложное",
     "Что такое MRR в оценке поисковых систем? Ответь в двух "
     "предложениях, с формулой.",
     lambda t: has(t, "reciprocal") or has(t, "обратн"), 220),
]


def run_probe(probe, *, provider, model: str | None, number: int,
              verbose: bool) -> dict:
    name, level, prompt, check, limit = probe
    started = time.monotonic()
    try:
        answer = ask(prompt, provider=provider, model=model,
                     max_tokens=limit, temperature=0.2)
    except LLMError as failure:
        print(f"  {number}. {name:26} СБОЙ: {failure}")
        return {"ok": False, "seconds": 0.0, "tokens": 0, "failed": True}
    spent = time.monotonic() - started

    text = (answer.text or "").strip()
    ok = bool(check(text))
    out_tokens = int(answer.usage.get("completion_tokens") or 0)
    rate = out_tokens / spent if spent > 0 else 0.0

    print(f"  {number}. {name:26} [{level:8}] {'✓' if ok else '✗'}  "
          f"{spent:5.2f}с  {out_tokens:4} ток.  {rate:5.1f} ток/с")
    if verbose or not ok:
        print(f"       {' '.join(text.split())[:200]}")
    return {"ok": ok, "seconds": spent, "tokens": out_tokens, "failed": False}


def measure_cold_start(model: str | None) -> float | None:
    """Выгрузить модель и замерить первый запрос.

    Выгружаем через `ollama stop`: ждать таймаута выгрузки (по умолчанию
    пять минут) в замере бессмысленно.
    """
    import subprocess

    name = model or DEFAULT_LOCAL_MODEL
    for binary in ("ollama", "/opt/homebrew/opt/ollama/bin/ollama"):
        try:
            subprocess.run([binary, "stop", name], capture_output=True,
                           timeout=30, check=False)
            break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    else:
        print("  (не нашёл ollama, чтобы выгрузить модель — холодный старт "
              "не замерен)")
        return None

    started = time.monotonic()
    try:
        ask("Скажи ровно: да", provider=LOCAL, model=model, max_tokens=8,
            temperature=0.0)
    except LLMError:
        return None
    return time.monotonic() - started


def main() -> None:
    argv = sys.argv[1:]
    verbose = "-v" in argv or "--verbose" in argv
    cloud = "--cloud" in argv
    model = None
    if "--model" in argv:
        position = argv.index("--model")
        if position + 1 < len(argv):
            model = argv[position + 1]
    numbers = [int(a) for a in argv if a.isdigit()]
    chosen = [(n, p) for n, p in enumerate(PROBES, 1)
              if not numbers or n in numbers]

    provider = None if cloud else LOCAL
    where = "облако (DeepSeek)" if cloud else "локально (Ollama)"
    print(f"Запросы: {where}"
          + (f" · модель {model or DEFAULT_LOCAL_MODEL}" if not cloud else ""))

    cold_seconds = None
    if "--cold" in argv and not cloud:
        cold_seconds = measure_cold_start(model)
    print(f"{'─' * 76}")

    results = []
    for number, probe in chosen:
        results.append(run_probe(probe, provider=provider, model=model,
                                 number=number, verbose=verbose))

    good = [r for r in results if not r["failed"]]
    passed = sum(1 for r in good if r["ok"])
    print(f"{'─' * 76}")
    print(f"пройдено {passed} из {len(good)}")
    if good:
        total = sum(r["seconds"] for r in good)
        tokens = sum(r["tokens"] for r in good)
        print(f"всего {total:.1f}с, выдано {tokens} токенов, "
              f"в среднем {tokens / total:.1f} ток/с")
        if cold_seconds is not None:
            print(f"холодный старт {cold_seconds:.2f}с — разовая загрузка "
                  f"модели в память")
        elif not cloud:
            print("холодный старт не мерился (модель уже была в памяти); "
                  "для замера: python3 probe.py --cold")


if __name__ == "__main__":
    main()
