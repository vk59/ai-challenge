#!/usr/bin/env python3
"""День 23: подбор порога отсечения по данным, а не на глаз.

    python3 tune.py            # распределения и таблица порогов
    python3 tune.py --wide 40  # шире выдача

Порог — самый дешёвый приём второго этапа, и самый обманчивый. Кажется,
что достаточно отрезать «непохожее». Этот скрипт показывает, почему не
достаточно: распределения близости у нужных и ненужных кусков
перекрываются, и любой порог либо пропускает мусор, либо режет ответы.

Золотым считается кусок, который и из нужного файла, и содержит ожидаемую
подстроку. Проверка по файлу слишком мягкая: в дне 22 нужный файл стоял
первым, а раздел с ответом — восьмым.
"""

import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from index import STRUCTURAL, Index  # noqa: E402
from gold import is_gold  # noqa: E402
from questions import ВОПРОСЫ as QUESTIONS  # noqa: E402

STEPS = [0.28, 0.30, 0.33, 0.35, 0.38, 0.40, 0.42, 0.45, 0.50]


def main() -> None:
    wide = 20
    if "--wide" in sys.argv:
        position = sys.argv.index("--wide")
        if position + 1 < len(sys.argv) and sys.argv[position + 1].isdigit():
            wide = int(sys.argv[position + 1])

    index = Index()
    gold_scores: list[float] = []
    other_scores: list[float] = []
    gold_ranks: list[int] = []
    per_question: list[tuple[int, float, int]] = []

    print(f"Выдача по {wide} кусков на вопрос, стратегия {STRUCTURAL}\n")
    for number, item in enumerate(QUESTIONS, 1):
        found = index.search(item["q"], STRUCTURAL, k=wide)
        rank, score_of_gold = 0, 0.0
        for position, (chunk, score) in enumerate(found, 1):
            if is_gold(chunk, item) and not rank:
                rank, score_of_gold = position, score
                gold_scores.append(score)
            else:
                other_scores.append(score)
        gold_ranks.append(rank)
        per_question.append((number, score_of_gold, rank))
        print(f"  {number:2}. золотой кусок: место {rank or '—':>3}, "
              f"близость {score_of_gold:.3f}" if rank else
              f"  {number:2}. золотого куска нет в выдаче")

    print(f"\n{'─' * 68}")
    print("распределение близости")
    print(f"  золотые  n={len(gold_scores):3}  min {min(gold_scores):.3f}  "
          f"медиана {statistics.median(gold_scores):.3f}  "
          f"max {max(gold_scores):.3f}")
    print(f"  прочие   n={len(other_scores):3}  min {min(other_scores):.3f}  "
          f"медиана {statistics.median(other_scores):.3f}  "
          f"max {max(other_scores):.3f}")

    # Вот главное число дня: насколько распределения налезают друг на друга.
    overlap = sum(1 for s in other_scores if s >= min(gold_scores))
    print(f"\n  ненужных кусков с близостью не ниже самого слабого "
          f"золотого: {overlap} из {len(other_scores)} "
          f"({100 * overlap / len(other_scores):.0f}%)")
    print("  → одним порогом нужное от ненужного не отделить")

    print(f"\n{'─' * 68}")
    print(f"{'порог':>6} {'золотых потеряно':>18} {'мусора отсечено':>17} "
          f"{'осталось на вопрос':>19}")
    for threshold in STEPS:
        lost = sum(1 for s in gold_scores if s < threshold)
        cut = sum(1 for s in other_scores if s < threshold)
        left = (len(gold_scores) + len(other_scores) - lost - cut) / len(QUESTIONS)
        chosen = "  ← выбран" if abs(threshold - 0.33) < 1e-9 else ""
        print(f"{threshold:>6.2f} {lost:>10} из {len(gold_scores):<4} "
              f"{cut:>8} из {len(other_scores):<5} {left:>14.1f}{chosen}")

    print(f"\n{'─' * 68}")
    weakest = min(gold_scores)
    print(f"Самый слабый золотой кусок: {weakest:.3f}")
    print("Без потерь на этой выборке работают пороги до 0.38 включительно,")
    print("и 0.38 отсекает мусора вдвое больше. Выбран всё же 0.33: до")
    print(f"{weakest:.3f} остаётся запас {weakest - 0.33:.3f}, а у 0.38 — всего")
    print(f"{weakest - 0.38:.3f}, и один новый вопрос со слабым совпадением")
    print("унёс бы свой ответ. Порог тут нужен не для точности, а чтобы")
    print("не платить за реранкинг явного шума.")
    print(f"\nЗолотой кусок в топ-5 без второго этапа: "
          f"{sum(1 for r in gold_ranks if 1 <= r <= 5)} из {len(QUESTIONS)}")
    print(f"Золотой кусок в топ-{wide}: "
          f"{sum(1 for r in gold_ranks if r)} из {len(QUESTIONS)}"
          "  ← столько доступно реранкингу")


if __name__ == "__main__":
    main()
