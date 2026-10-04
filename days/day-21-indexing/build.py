#!/usr/bin/env python3
"""День 21: сборка индекса обеими стратегиями.

    python3 build.py              # собрать fixed и structural
    python3 build.py fixed        # только одну
    python3 build.py --stats      # не собирать, показать что уже есть

Повторный запуск почти бесплатный: векторы лежат в кэше
(`memory/embeddings.db`), и в сеть уходят только новые куски.
"""

import sys
import time
from pathlib import Path

ЗДЕСЬ = Path(__file__).resolve().parent
sys.path.insert(0, str(ЗДЕСЬ.parents[1] / "shared"))
sys.path.insert(0, str(ЗДЕСЬ))

from corpus import НА_СТРАНИЦЕ, собрать, сводка  # noqa: E402
from embeddings import РАСХОД, МОДЕЛЬ, сколько_в_кэше  # noqa: E402
from index import СТРАТЕГИИ, Index  # noqa: E402

ПОДПИСИ = {
    "fixed": "окно 1200 знаков с перехлёстом 200",
    "structural": "по заголовкам и определениям, потолок 1800",
}


def печать_статистики(статистика: dict) -> None:
    с = статистика
    print(f"  кусков      {с['chunks']}")
    print(f"  документов  {с['documents']}")
    print(f"  знаков      {с['chars']:,}".replace(",", " "))
    print(f"  длина       средняя {с['avg']}, медиана {с['median']}, "
          f"от {с['min']} до {с['max']}")
    print(f"  разброс     ±{с['spread']} знаков")


def main() -> None:
    аргументы = [а for а in sys.argv[1:] if not а.startswith("-")]
    только_показать = "--stats" in sys.argv
    стратегии = [а for а in аргументы if а in СТРАТЕГИИ] or list(СТРАТЕГИИ)

    индекс = Index()

    if только_показать:
        for стратегия in стратегии:
            print(f"\n{стратегия} — {ПОДПИСИ[стратегия]}")
            печать_статистики(индекс.stats(стратегия))
        print(f"\nвекторов в кэше: {сколько_в_кэше()}")
        return

    документы = собрать()
    с = сводка(документы)
    print(f"Корпус: {с['documents']} документов, {с['chars']:,} знаков, "
          f"≈{с['pages']} страниц по {НА_СТРАНИЦЕ} знаков".replace(",", " "))
    print(f"Модель эмбеддингов: {МОДЕЛЬ}")
    print(f"В кэше уже: {сколько_в_кэше()} векторов\n")

    итоги = {}
    for стратегия in стратегии:
        print(f"── {стратегия}: {ПОДПИСИ[стратегия]}")
        начало = time.monotonic()

        последняя = [0]

        def шаг(сделано: int, всего: int) -> None:
            if сделано - последняя[0] >= 128 or сделано == всего:
                последняя[0] = сделано
                print(f"   векторы: {сделано}/{всего}", end="\r", flush=True)

        статистика = индекс.rebuild(документы, стратегия, на_шаг=шаг)
        print(" " * 40, end="\r")
        итоги[стратегия] = статистика
        печать_статистики(статистика)
        print(f"  собрано за  {time.monotonic() - начало:.1f}с\n")

    if len(итоги) == 2:
        б, с = итоги["fixed"], итоги["structural"]
        print("── в двух словах")
        print(f"  кусков:  fixed {б['chunks']}  ·  structural {с['chunks']}")
        print(f"  средняя: fixed {б['avg']}  ·  structural {с['avg']}")
        print(f"  разброс: fixed ±{б['spread']}  ·  structural ±{с['spread']}")
        print("  У fixed куски ровные, у structural — осмысленные, "
              "но разной длины.")

    print(f"\nПотрачено на эмбеддинги: {РАСХОД.деньги_строкой} "
          f"({РАСХОД.токенов:,} токенов, {РАСХОД.запросов} запросов, "
          f"из кэша {РАСХОД.из_кэша})".replace(",", " "))
    print(f"Индекс: {Index().path}")
    print("Поиск:  python3 search.py «ваш вопрос»")


if __name__ == "__main__":
    main()
