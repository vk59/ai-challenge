#!/usr/bin/env python3
"""День 21: поиск по индексу из терминала.

    python3 search.py "почему сжатие бывает дороже"
    python3 search.py -s fixed "то же самое другой стратегией"
    python3 search.py -k 8 "больше результатов"
    python3 search.py --both "сравнить обе выдачи рядом"

Показывает метаданные каждой находки: источник, раздел, chunk_id. Без них
результат поиска — безымянный кусок текста, по которому не понять, откуда
он взялся и можно ли ему верить.
"""

import sys
from pathlib import Path

ЗДЕСЬ = Path(__file__).resolve().parent
sys.path.insert(0, str(ЗДЕСЬ.parents[1] / "shared"))
sys.path.insert(0, str(ЗДЕСЬ))

from embeddings import РАСХОД  # noqa: E402
from index import СТРАТЕГИИ, STRUCTURAL, Index, IndexError_  # noqa: E402


def показать(индекс: Index, запрос: str, стратегия: str, сколько: int) -> None:
    найденное = индекс.search(запрос, стратегия, k=сколько)
    print(f"\n── {стратегия}")
    for позиция, (кусок, оценка) in enumerate(найденное, 1):
        print(f"\n{позиция}. {оценка:.3f}  {кусок.source}")
        print(f"   раздел   {кусок.section}")
        print(f"   chunk_id {кусок.chunk_id}  ({кусок.length} знаков, "
              f"со смещения {кусок.start})")
        текст = " ".join(кусок.text.split())
        print(f"   {текст[:240]}{'…' if len(текст) > 240 else ''}")


def main() -> None:
    аргументы = sys.argv[1:]
    стратегия, сколько, обе = STRUCTURAL, 5, False
    слова: list[str] = []

    пропустить = False
    for индекс_а, аргумент in enumerate(аргументы):
        if пропустить:
            пропустить = False
            continue
        if аргумент in ("-s", "--strategy") and индекс_а + 1 < len(аргументы):
            стратегия = аргументы[индекс_а + 1]
            пропустить = True
        elif аргумент in ("-k", "--top") and индекс_а + 1 < len(аргументы):
            сколько = max(1, int(аргументы[индекс_а + 1]))
            пропустить = True
        elif аргумент == "--both":
            обе = True
        else:
            слова.append(аргумент)

    запрос = " ".join(слова).strip()
    if not запрос:
        print(__doc__)
        sys.exit(1)
    if стратегия not in СТРАТЕГИИ:
        print(f"Стратегии «{стратегия}» нет. Есть: {', '.join(СТРАТЕГИИ)}")
        sys.exit(1)

    хранилище = Index()
    print(f"Запрос: {запрос}")
    try:
        for какая in (СТРАТЕГИИ if обе else (стратегия,)):
            показать(хранилище, запрос, какая, сколько)
    except IndexError_ as сбой:
        print(f"\n{сбой}")
        sys.exit(1)

    цена = ("бесплатно, вектор запроса был в кэше" if РАСХОД.запросов == 0
            else РАСХОД.деньги_строкой)
    print(f"\nвектор запроса: {цена}")


if __name__ == "__main__":
    main()
