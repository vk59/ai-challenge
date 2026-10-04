#!/usr/bin/env python3
"""День 24: вопрос → ответ с источниками и проверенными цитатами.

    python3 ask.py "какой MRR у структурной нарезки"
    python3 ask.py --rerank "задача не выполнилась, компьютер спал"
    python3 ask.py --show "плюс полный текст кусков и что отсеклось"
    python3 ask.py --no-gate "без порога отказа — посмотреть, что скажет модель"

Каждая цитата помечена: подтверждена дословно или выдумана. Проверка
механическая, поэтому ей можно верить больше, чем самому ответу.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from cite import ABSTAIN_BELOW, answer_with_citations  # noqa: E402
from index import Index  # noqa: E402
from llm import LLMError  # noqa: E402


def show(result, with_chunks: bool) -> None:
    print(f"\n{'═' * 74}")
    if result.abstained:
        print("ОТКАЗ ПО ПОРОГУ — до модели дело не дошло")
    elif not result.confident:
        print("МОДЕЛЬ ОТКАЗАЛАСЬ — вопрос по теме, но ответа в выдержках нет")
    else:
        print("ОТВЕТ")
    print(f"{'─' * 74}")
    print((result.answer or "(пусто)").strip())

    if result.parse_failed:
        print(f"\n⚠ JSON не разобрался, показан сырой ответ модели")

    print(f"{'─' * 74}")
    print(f"  топ-1 близость {result.top_score:.3f}"
          + (f"  (порог {ABSTAIN_BELOW:.2f})" if result.abstained else ""))
    if result.abstain_reason:
        print(f"  причина: {result.abstain_reason}")

    if result.sources:
        print(f"\n  ИСТОЧНИКИ ({len(result.sources)}):")
        for source in result.sources:
            print(f"    [{source.n}] {source.source}")
            print(f"         раздел   {source.section}")
            print(f"         chunk_id {source.chunk_id}")
            print(f"         близость {source.score:.3f}")
    elif not result.abstained:
        print("\n  ИСТОЧНИКОВ НЕТ — это нарушение требования дня")

    if result.quotes:
        print(f"\n  ЦИТАТЫ ({len(result.quotes)}: подтверждено "
              f"{result.verified_quotes}, выдумано "
              f"{result.fabricated_quotes}):")
        for quote in result.quotes:
            mark = ("✓ дословно" if quote.verified
                    else f"✗ ВЫДУМКА (совпало {quote.matched_ratio:.0%})")
            print(f"    {mark}  [{quote.n}] {quote.source}")
            print(f"      «{' '.join(quote.text.split())}»")
    elif result.confident:
        print("\n  ЦИТАТ НЕТ — это нарушение требования дня")

    if with_chunks:
        print(f"\n  ЧТО БЫЛО ВЫДАНО МОДЕЛИ ({len(result.chunks)}):")
        cited = {s.n for s in result.sources}
        for number, chunk in enumerate(result.chunks, 1):
            mark = "→" if number in cited else " "
            print(f"  {mark} [{number}] {chunk['score']:.3f}  {chunk['source']}")
            print(f"        «{chunk['section']}»")

    print(f"\n  {result.total_tokens} токенов · {result.seconds:.1f}с")


def main() -> None:
    argv = sys.argv[1:]
    with_chunks = "--show" in argv
    with_rerank = "--rerank" in argv
    no_gate = "--no-gate" in argv
    words = [a for a in argv if not a.startswith("--")]

    question = " ".join(words).strip()
    if not question:
        print(__doc__)
        sys.exit(1)

    index = Index()
    extra: dict = {}
    if with_rerank:
        from modes import options_for
        from rerank import retrieve

        extra["retrieval"] = retrieve(question, index=index,
                                      **options_for("rerank"))
    if no_gate:
        extra["abstain_below"] = None

    print(f"Вопрос: {question}"
          + ("  (с реранкингом)" if with_rerank else ""))
    try:
        show(answer_with_citations(question, index=index, **extra), with_chunks)
    except LLMError as failure:
        print(f"\nсбой: {failure}")
        sys.exit(1)


if __name__ == "__main__":
    main()
