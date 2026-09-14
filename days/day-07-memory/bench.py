#!/usr/bin/env python3
"""День 7: чего стоит «переписать файл целиком».

    python3 bench.py
    python3 bench.py 8000        # другой размер архива

Ключей и сети не нужно: меряем только диск. Вопрос простой — сколько стоит
дозаписать ОДНУ реплику, если в архиве уже лежит N.

У SQLite это INSERT: его цена от размера таблицы практически не зависит.
У JSON — прочитать файл, добавить элемент, записать файл обратно, то есть
работа, пропорциональная всему архиву. А раз архив растёт с каждой репликой,
суммарное время записи диалога растёт квадратично.

Это ровно та же ловушка, из-за которой в дне 6 появилось ограничение окна
контекста, только там она была про токены, а здесь — про диск.
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from memory import JsonStore, SqliteStore, Turn, now_iso  # noqa: E402

# Реплика правдоподобной длины: мерить на строке «привет» бессмысленно,
# там всё утонет в накладных расходах.
TEXT = "реплика средней длины, примерно как обычный вопрос к ассистенту " * 3

TOTAL = 4000
MARKS = (10, 100, 500, 1000, 2000)


def fill(store, total: int, marks: set[int]) -> dict[int, float]:
    """Пишет total реплик, засекая стоимость записи на контрольных отметках."""
    timings: dict[int, float] = {}
    for index in range(total):
        turn = Turn(
            "user" if index % 2 == 0 else "assistant",
            f"{index}: {TEXT}", now_iso(), 12, 0.5,
        )
        started = time.perf_counter()
        store.append("bench", turn)
        if index in marks:
            timings[index] = (time.perf_counter() - started) * 1000
    return timings


def read_window(store, times: int = 50) -> float:
    """Средняя стоимость поднятия окна контекста из большого архива."""
    started = time.perf_counter()
    for _ in range(times):
        store.load("bench", limit=20)
    return (time.perf_counter() - started) / times * 1000


def main() -> int:
    total = int(sys.argv[1]) if len(sys.argv) > 1 else TOTAL
    marks = {mark for mark in MARKS if mark < total} | {total - 1}

    print(f"\n  Дозапись одной реплики в архив из N. Пишем {total} реплик,")
    print("  засекаем время на контрольных отметках.\n")

    with tempfile.TemporaryDirectory() as tmp:
        json_store = JsonStore(Path(tmp) / "json")
        sqlite_store = SqliteStore(Path(tmp) / "sqlite" / "agent.db")

        json_times = fill(json_store, total, marks)
        sqlite_times = fill(sqlite_store, total, marks)

        print(f"  {'в архиве':>10} | {'JSON, мс':>9} | {'SQLite, мс':>11} | разница")
        print("  " + "-" * 52)
        for mark in sorted(marks):
            slower = json_times[mark] / sqlite_times[mark]
            note = f"{slower:.0f}×" if slower >= 1.5 else "—"
            print(f"  {mark:>10} | {json_times[mark]:>9.2f} | "
                  f"{sqlite_times[mark]:>11.2f} | {note:>7}")

        print(f"\n  Поднять окно контекста (20 реплик) из архива в {total}:")
        print(f"    JSON   {read_window(json_store):.2f} мс   "
              f"— читает весь файл, чтобы отрезать хвост")
        print(f"    SQLite {read_window(sqlite_store):.2f} мс   "
              f"— ORDER BY id DESC LIMIT 20")

        json_size = sum(p.stat().st_size for p in (Path(tmp) / "json").glob("*.json"))
        sqlite_size = (Path(tmp) / "sqlite" / "agent.db").stat().st_size
        print(f"\n  На диске: JSON {json_size / 1024:.0f} КБ, "
              f"SQLite {sqlite_size / 1024:.0f} КБ — по объёму разницы почти нет.")

    print("\n  Вывод: важны не абсолютные числа (у вас будут свои), а форма.")
    print("  Колонка SQLite плоская, колонка JSON растёт вместе с архивом.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
