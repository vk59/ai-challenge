#!/usr/bin/env python3
"""День 23: сравнение четырёх режимов на контрольных вопросах.

    python3 evaluate.py                  # все режимы
    python3 evaluate.py base full        # только эти два
    python3 evaluate.py -v               # плюс тексты ответов
    python3 evaluate.py 3 8 --only       # только вопросы 3 и 8

Мерится двумя мерами, и обе нужны:

    контекст  попал ли золотой кусок в промпт. Это качество ПОИСКА, и оно
              не зависит от того, как модель распорядилась находкой.
    ответ     попал / признался / соврал. Это качество ОТВЕТА.

Разделять обязательно. В дне 22 единственный промах выглядел как промах
модели, а оказался промахом поиска: нужного куска в промпте просто не было,
и отказ был правильным поведением. Без первой меры этого не увидеть.

Набор вопросов взят из дня 22 без изменений — он там сверен с корпусом.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from answer import answer_question  # noqa: E402
from evaluate_shim import verdict_of  # noqa: E402
from gold import is_gold  # noqa: E402
from index import Index  # noqa: E402
from llm import LLMError  # noqa: E402
from modes import MODES, ORDER  # noqa: E402
from questions import ВОПРОСЫ as QUESTIONS  # noqa: E402

MARKS = {"попал": "✓", "признался": "?", "соврал": "✗"}


def main() -> None:
    argv = sys.argv[1:]
    verbose = "-v" in argv or "--verbose" in argv
    chosen = [a for a in argv if a in MODES] or list(ORDER)
    numbers = [int(a) for a in argv if a.isdigit()]
    items = [(n, q) for n, q in enumerate(QUESTIONS, 1)
             if not numbers or n in numbers]

    index = Index()
    tally = {m: {"попал": 0, "признался": 0, "соврал": 0, "context": 0,
                 "sources": 0, "invented": 0, "tokens": 0, "seconds": 0.0,
                 "stage_tokens": 0}
             for m in chosen}

    for number, item in items:
        print(f"\n{'─' * 76}")
        print(f"{number}. {item['q'][:70]}")
        print(f"   ждём: {'; '.join(' / '.join(v) for v in item['expect'])}")

        for mode in chosen:
            try:
                result = answer_question(item["q"], mode=mode, index=index)
            except LLMError as failure:
                print(f"   {mode:10} сбой: {failure}")
                continue

            place = next((n for n, hit in enumerate(result.retrieval.hits, 1)
                          if is_gold(hit.chunk, item)), 0)
            mark = verdict_of(result.text, item["expect"])
            sources_ok = bool(set(result.sources) & item["sources"])

            box = tally[mode]
            box[mark] += 1
            box["context"] += bool(place)
            box["sources"] += sources_ok
            box["invented"] += len(result.invented)
            box["tokens"] += result.total_tokens
            box["stage_tokens"] += result.stage_tokens
            box["seconds"] += result.seconds + result.retrieval.seconds

            print(f"   {MARKS[mark]} {mode:10} золотой в промпте: "
                  f"{('№' + str(place)) if place else 'НЕТ':>5}   "
                  f"ответ: {mark:10} ист. {'✓' if sources_ok else '✗'}   "
                  f"{result.total_tokens:5} ток.")
            if verbose:
                print(f"       {' '.join((result.text or '').split())[:220]}")

    total = len(items)
    print(f"\n{'═' * 76}")
    print(f"{'режим':11} {'золотой в промпте':>18} {'попал':>8} "
          f"{'признался':>11} {'соврал':>8} {'ист.':>6} {'токенов':>9}")
    for mode in chosen:
        box = tally[mode]
        print(f"{mode:11} {box['context']:>12}/{total:<5} "
              f"{box['попал']:>5}/{total:<2} {box['признался']:>9}  "
              f"{box['соврал']:>7} {box['sources']:>3}/{total:<2} "
              f"{box['tokens']:>9,}".replace(",", " "))

    print(f"\n{'режим':11} {'второй этап, ток.':>18} {'секунд всего':>14}")
    for mode in chosen:
        box = tally[mode]
        print(f"{mode:11} {box['stage_tokens']:>18,} "
              f"{box['seconds']:>14.1f}".replace(",", " "))

    if "base" in tally and chosen[-1] != "base":
        best = chosen[-1]
        base, top = tally["base"], tally[best]
        print(f"\nОт base к {best}:")
        print(f"  золотой кусок в промпте: {base['context']}/{total} → "
              f"{top['context']}/{total}")
        print(f"  правильных ответов:      {base['попал']}/{total} → "
              f"{top['попал']}/{total}")
        ratio = top["tokens"] / max(1, base["tokens"])
        print(f"  токенов:                 {base['tokens']:,} → "
              f"{top['tokens']:,} (в {ratio:.1f} раза)".replace(",", " "))
    invented = sum(t["invented"] for t in tally.values())
    print(f"\nВыдуманных ссылок за весь прогон: {invented}")


if __name__ == "__main__":
    main()
