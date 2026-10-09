#!/usr/bin/env python3
"""День 27: что теряется, когда эмбеддинги считает своя машина.

    python3 compare_backends.py          # оба бэкенда на 10 вопросах
    python3 compare_backends.py -v       # плюс что нашлось

Переезд на локальные эмбеддинги — не бесплатный размен. Здесь он измерен
на том же наборе из дня 22, где у каждого вопроса известен файл с ответом
и проверяемая подстрока.

Мера — место золотого куска в выдаче. Золотой кусок это тот, который
и из нужного файла, и содержит ожидаемое: проверять по файлу слишком
мягко, в дне 22 нужный файл стоял первым, а раздел с ответом восьмым.
"""

import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from questions import ВОПРОСЫ as QUESTIONS  # noqa: E402

WIDE = 20


def is_gold(chunk, item) -> bool:
    low = (chunk.text or "").lower()
    if chunk.source not in item["sources"]:
        return False
    return all(any(v.lower() in low for v in variants)
               for variants in item["expect"])


def measure(backend: str, verbose: bool) -> dict | None:
    os.environ["AI_ADVENT_EMBEDDINGS"] = backend
    import importlib

    import embeddings
    import index as index_module
    importlib.reload(embeddings)
    importlib.reload(index_module)

    path = index_module.база_для(backend)
    if not path.exists():
        print(f"\n{backend}: индекса нет ({path.name})")
        return None

    index = index_module.Index()
    stats = index.stats("structural")
    print(f"\n── {backend} · {embeddings.модель_бэкенда(backend)} "
          f"· {stats['chunks']} кусков")

    ranks, seconds = [], 0.0
    import time
    for number, item in enumerate(QUESTIONS, 1):
        started = time.monotonic()
        found = index.search(item["q"], "structural", k=WIDE)
        seconds += time.monotonic() - started
        place = next((n for n, (chunk, _) in enumerate(found, 1)
                      if is_gold(chunk, item)), 0)
        ranks.append(place)
        if verbose:
            top = found[0][0].source.split("/")[-2] if found else "—"
            print(f"  {number:2}. место {place or '—':>3}  "
                  f"топ-1 {found[0][1]:.3f} {top}")

    in5 = sum(1 for r in ranks if 1 <= r <= 5)
    in20 = sum(1 for r in ranks if r)
    first = sum(1 for r in ranks if r == 1)
    places = [r for r in ranks if r]
    mrr = sum(1 / r for r in ranks if r) / len(ranks)
    print(f"  первым {first}/{len(ranks)}   в топ-5 {in5}/{len(ranks)}   "
          f"в топ-{WIDE} {in20}/{len(ranks)}   MRR {mrr:.3f}")
    print(f"  медиана места {statistics.median(places) if places else 0:.0f}"
          f"   поиск {seconds / len(ranks) * 1000:.0f} мс на вопрос")
    return {"backend": backend, "first": first, "in5": in5, "in20": in20,
            "mrr": mrr, "ranks": ranks, "ms": seconds / len(ranks) * 1000}


def main() -> None:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    results = [r for r in (measure(b, verbose) for b in ("cloud", "local"))
               if r]
    if len(results) != 2:
        return

    cloud, local = results
    print(f"\n{'═' * 70}")
    print(f"{'бэкенд':8} {'первым':>8} {'топ-5':>8} {'топ-20':>8} "
          f"{'MRR':>8} {'мс':>7} {'цена сборки':>14}")
    for r, price in ((cloud, "$0.006"), (local, "$0")):
        n = len(r["ranks"])
        print(f"{r['backend']:8} {r['first']:>5}/{n:<2} {r['in5']:>5}/{n:<2} "
              f"{r['in20']:>5}/{n:<2} {r['mrr']:>8.3f} {r['ms']:>7.0f} "
              f"{price:>14}")

    delta = local["mrr"] - cloud["mrr"]
    print(f"\nMRR {delta:+.3f}. ", end="")
    if abs(delta) < 0.05:
        print("Разница в пределах шума: локальные эмбеддинги не хуже.")
    elif delta < 0:
        print("Локальные слабее — это цена офлайна.")
    else:
        print("Локальные оказались точнее на этом наборе.")


if __name__ == "__main__":
    main()
