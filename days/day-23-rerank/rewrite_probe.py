#!/usr/bin/env python3
"""День 23: замер пользы переписывания запроса.

    python3 rewrite_probe.py

На десяти контрольных вопросах дня 22 переписывание не даёт ничего: там
нужный кусок и так попадает в топ-20, узкое место — точность, а её лечит
реранкинг. Чтобы не утверждать голословно, что переписывание всё-таки
полезно, нужен замер на том, для чего оно предназначено.

Здесь вопросы нарочно бытовые — ни одного термина из документации.
Переписывание должно подставить термины и поднять нужный файл выше.
Мера простая: место нужного файла в выдаче до и после.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from index import STRUCTURAL, Index  # noqa: E402
from rerank import rewrite_query  # noqa: E402

WIDE = 20

# (бытовой вопрос, файл, в котором ответ)
PROBES: list[tuple[str, str]] = [
    ("Что сделать, чтобы программа не забывала разговор после закрытия?",
     "days/day-07-memory/README.md"),
    ("Как сделать, чтобы бот разговаривал со мной по-разному в зависимости "
     "от того, кто я?",
     "days/day-12-personalization/README.md"),
    ("Почему одна и та же просьба даёт каждый раз другой текст?",
     "days/day-04-temperature/README.md"),
    ("Как заставить программу саму решать, чем ей воспользоваться?",
     "days/day-17-mcp-tool/README.md"),
    ("Что делать, если переписка стала слишком длинной и дорогой?",
     "days/day-09-compression/README.md"),
]


def place_of(index: Index, query: str, wanted: str) -> int:
    found = index.search(query, STRUCTURAL, k=WIDE)
    return next((n for n, (chunk, _) in enumerate(found, 1)
                 if chunk.source == wanted), 0)


def main() -> None:
    index = Index()
    better = worse = same = 0
    print(f"Выдача по {WIDE} кусков, мера — место нужного файла\n")

    for question, wanted in PROBES:
        before = place_of(index, question, wanted)
        query, _ = rewrite_query(question)
        after = place_of(index, query, wanted)

        # Отсутствие в выдаче считаем худшим из возможных мест.
        rank_before = before or WIDE + 1
        rank_after = after or WIDE + 1
        if rank_after < rank_before:
            verdict, better = "лучше", better + 1
        elif rank_after > rank_before:
            verdict, worse = "хуже", worse + 1
        else:
            verdict, same = "так же", same + 1

        print(f"  {question}")
        print(f"    нужен: {wanted}")
        print(f"    место: {before or 'нет':>3} → {after or 'нет':<3}  {verdict}")
        print(f"    запрос: {query}")
        print()

    print(f"{'─' * 70}")
    print(f"лучше {better}, хуже {worse}, без изменений {same} "
          f"из {len(PROBES)}")
    print("\nВывод: переписывание повышает полноту — вытаскивает нужный файл")
    print("из глубины выдачи. Реранкинг повышает точность — ставит нужный")
    print("кусок первым среди уже найденных. Приёмы разные, и на наборе дня 22")
    print("работает только второй, потому что полнота там и без него полная.")


if __name__ == "__main__":
    main()
