#!/usr/bin/env python3
"""День 27: порог отказа для локальных эмбеддингов.

    python3 tune_offline.py            # обе шкалы рядом
    python3 tune_offline.py --local    # только локальная

Порог 0.39 из дня 24 подобран под облачную модель и к локальной
неприменим: у неё другая шкала. Две несвязанные фразы облачная разводит
до 0.14, а `nomic-embed-text` ставит им 0.61 — у него высокий базовый
уровень похожести, и порог надо перемерять, а не переносить.

Три группы вопросов, как в дне 24: свои, чужие и околотематические.
"""

import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from index import STRUCTURAL, Index, база_для  # noqa: E402
from questions import ВОПРОСЫ as QUESTIONS  # noqa: E402

ALIEN = [
    "Какая столица Австралии?",
    "Как приготовить борщ с пампушками?",
    "Сколько стоит билет на самолёт до Лиссабона?",
    "Кто написал роман «Война и мир»?",
    "Какая погода будет в Москве на выходных?",
    "Как настроить Kubernetes ingress с cert-manager?",
    "Какой у вас тарифный план на интернет?",
]
NEARBY = [
    "Как развернуть этот проект на Kubernetes?",
    "Сколько человек в команде разработки?",
    "Какая лицензия у этого репозитория?",
    "Когда дедлайн сдачи 30-го дня?",
]


def top_scores(index: Index, questions: list[str]) -> list[float]:
    out = []
    for question in questions:
        found = index.search(question, STRUCTURAL, k=5)
        out.append(found[0][1] if found else 0.0)
    return out


def measure(backend: str) -> dict | None:
    import os

    os.environ["AI_ADVENT_EMBEDDINGS"] = backend
    import importlib

    import embeddings
    import index as index_module
    importlib.reload(embeddings)
    importlib.reload(index_module)

    path = index_module.база_для(backend)
    if not path.exists():
        print(f"\n{backend}: индекса нет ({path.name}). Соберите:")
        print(f"  AI_ADVENT_EMBEDDINGS={backend} "
              f"python3 ../day-21-indexing/build.py")
        return None

    index = index_module.Index()
    own = top_scores(index, [q["q"] for q in QUESTIONS])
    alien = top_scores(index, ALIEN)
    nearby = top_scores(index, NEARBY)

    print(f"\n── {backend} · {embeddings.модель_бэкенда(backend)} "
          f"· {embeddings.размерность_бэкенда(backend)} измерений")
    for title, scores in (("свои (ответ есть)", own),
                          ("чужие (тема другая)", alien),
                          ("околотемные (ответа нет)", nearby)):
        print(f"  {title:28} min {min(scores):.3f}  "
              f"медиана {statistics.median(scores):.3f}  max {max(scores):.3f}")

    gap_low, gap_high = max(alien), min(own)
    if gap_low < gap_high:
        suggested = round((gap_low + gap_high) / 2, 2)
        print(f"  зазор между чужими ({gap_low:.3f}) и своими "
              f"({gap_high:.3f}) → порог {suggested:.2f}")
    else:
        suggested = None
        print(f"  ⚠ зазора нет: чужие доходят до {gap_low:.3f}, "
              f"свои начинаются с {gap_high:.3f}")
        print("    порогом эти группы не разделить")
    return {"backend": backend, "own": own, "alien": alien,
            "nearby": nearby, "suggested": suggested}


def main() -> None:
    backends = ["local"] if "--local" in sys.argv else ["cloud", "local"]
    results = [r for r in (measure(b) for b in backends) if r]

    if len(results) == 2:
        print(f"\n{'─' * 70}")
        print("Шкалы разные, и это главное: порог нельзя перенести.")
        for r in results:
            if r["suggested"]:
                print(f"  {r['backend']:6} порог {r['suggested']:.2f}")
            else:
                print(f"  {r['backend']:6} порогом не разделяется")


if __name__ == "__main__":
    main()
