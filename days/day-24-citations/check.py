#!/usr/bin/env python3
"""День 24: проверка трёх требований на десяти вопросах.

    python3 check.py              # все вопросы
    python3 check.py 3 7          # только выбранные
    python3 check.py -v           # плюс тексты ответов и цитат
    python3 check.py --rerank     # с реранкингом дня 23
    python3 check.py --judge      # плюс суждение модели о связи ответа и цитат

Задание просит проверить три вещи. Две проверяются машинно и однозначно:

    источники   есть ли source + section + chunk_id в каждом ответе
    цитаты      есть ли они и подтверждаются ли ДОСЛОВНО в выданном куске

Третья — «совпадает ли смысл ответа с цитатами» — так не проверяется.
Делаю два подхода, и оба несовершенны по-разному:

    опора       содержит ли хоть одна ПОДТВЕРЖДЁННАЯ цитата тот самый факт,
                который мы ждём в ответе. Объективно и механически, но
                узко: проверяет один факт, а не весь ответ.
    судья       отдельный запрос к модели: следует ответ из цитат или нет.
                Шире по охвату, но это суждение модели о работе модели,
                и ошибаться может само. Поэтому считается отдельной
                колонкой и включается флагом, а не по умолчанию.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from cite import ABSTAIN_BELOW, answer_with_citations, normalize  # noqa: E402
from index import Index  # noqa: E402
from llm import LLMError, ask  # noqa: E402
from questions import ВОПРОСЫ as QUESTIONS  # noqa: E402

JUDGE_PROMPT = """Вот вопрос, ответ на него и цитаты, которыми ответ
подкреплён.

Вопрос: {question}

Ответ: {answer}

Цитаты:
{quotes}

Следует ли ответ из цитат? Ответь одним словом:
  да        — всё в ответе подкреплено цитатами
  частично  — часть утверждений не подкреплена
  нет       — ответ не следует из цитат

Только одно слово, без пояснений."""


def quotes_support_fact(result, expect: list[list[str]]) -> bool:
    """Есть ли ожидаемый факт в подтверждённых цитатах.

    Берём только подтверждённые: цитата, которой нет в документе, ничего
    не подкрепляет, даже если нужное слово в ней встречается.
    """
    pile = " ".join(normalize(q.text) for q in result.quotes if q.verified)
    if not pile:
        return False
    return all(any(variant.lower() in pile for variant in variants)
               for variants in expect)


def answer_has_fact(text: str, expect: list[list[str]]) -> bool:
    low = normalize(text)
    return all(any(variant.lower() in low for variant in variants)
               for variants in expect)


def ask_judge(result) -> str:
    quotes = "\n".join(f"[{q.n}] {q.text}" for q in result.quotes) or "(нет)"
    try:
        reply = ask(JUDGE_PROMPT.format(question=result.question,
                                        answer=result.answer, quotes=quotes),
                    max_tokens=16, temperature=0.0)
    except LLMError:
        return "сбой"
    word = normalize(reply.text).strip(" .!")
    for known in ("частично", "да", "нет"):
        if known in word:
            return known
    return "неясно"


def main() -> None:
    argv = sys.argv[1:]
    verbose = "-v" in argv or "--verbose" in argv
    with_judge = "--judge" in argv
    with_rerank = "--rerank" in argv
    numbers = [int(a) for a in argv if a.isdigit()]
    items = [(n, q) for n, q in enumerate(QUESTIONS, 1)
             if not numbers or n in numbers]

    index = Index()
    retrieve = None
    if with_rerank:
        from modes import options_for
        from rerank import retrieve as _retrieve

        def retrieve(question):            # noqa: F811
            return _retrieve(question, index=index, **options_for("rerank"))

    tally = {"sources": 0, "quotes": 0, "all_verified": 0, "fabricated": 0,
             "support": 0, "answer_ok": 0, "abstained": 0, "parse_failed": 0,
             "tokens": 0}
    judged = {"да": 0, "частично": 0, "нет": 0, "неясно": 0, "сбой": 0}

    print(f"Порог отказа {ABSTAIN_BELOW:.2f}"
          + (" · с реранкингом дня 23" if with_rerank else "")
          + (" · с судьёй" if with_judge else ""))

    for number, item in items:
        print(f"\n{'─' * 76}")
        print(f"{number}. {item['q'][:70]}")
        print(f"   ждём: {'; '.join(' / '.join(v) for v in item['expect'])}")

        extra = {"retrieval": retrieve(item["q"])} if retrieve else {}
        try:
            result = answer_with_citations(item["q"], index=index, **extra)
        except LLMError as failure:
            print(f"   сбой: {failure}")
            continue

        verified = result.verified_quotes
        fabricated = result.fabricated_quotes
        support = quotes_support_fact(result, item["expect"])
        answer_ok = answer_has_fact(result.answer, item["expect"])

        tally["sources"] += result.has_sources
        tally["quotes"] += result.has_quotes
        tally["all_verified"] += bool(result.quotes) and fabricated == 0
        tally["fabricated"] += fabricated
        tally["support"] += support
        tally["answer_ok"] += answer_ok
        tally["abstained"] += result.abstained
        tally["parse_failed"] += result.parse_failed
        tally["tokens"] += result.total_tokens

        print(f"   источники {'✓' if result.has_sources else '✗'} "
              f"({len(result.sources)})   "
              f"цитаты {'✓' if result.has_quotes else '✗'} "
              f"({verified} подтв."
              + (f", {fabricated} ВЫДУМАНО" if fabricated else "") + ")   "
              f"опора {'✓' if support else '✗'}   "
              f"ответ {'✓' if answer_ok else '✗'}   "
              f"{result.total_tokens} ток.")

        if with_judge and result.quotes:
            word = ask_judge(result)
            judged[word] += 1
            print(f"   судья: {word}")

        for source in result.sources:
            print(f"     [{source.n}] {source.chunk_id}")
        if verbose:
            print(f"   ответ: {' '.join(result.answer.split())[:200]}")
            for quote in result.quotes:
                mark = "✓" if quote.verified else f"✗ {quote.matched_ratio:.2f}"
                print(f"   {mark} [{quote.n}] "
                      f"«{' '.join(quote.text.split())[:120]}»")

    total = len(items)
    print(f"\n{'═' * 76}")
    print(f"вопросов: {total}")
    print(f"  источники есть                 {tally['sources']}/{total}")
    print(f"  цитаты есть                    {tally['quotes']}/{total}")
    print(f"  все цитаты подтверждены        {tally['all_verified']}/{total}")
    print(f"  выдуманных цитат всего         {tally['fabricated']}")
    print(f"  цитаты содержат нужный факт    {tally['support']}/{total}")
    print(f"  ответ содержит нужный факт     {tally['answer_ok']}/{total}")
    if tally["abstained"]:
        print(f"  отказов по порогу              {tally['abstained']}")
    if tally["parse_failed"]:
        print(f"  ⚠ JSON не разобрался           {tally['parse_failed']}")
    print(f"  токенов                        {tally['tokens']:,}"
          .replace(",", " "))
    if with_judge:
        print("\n  судья о связи ответа и цитат:")
        for word, count in judged.items():
            if count:
                print(f"    {word:9} {count}")


if __name__ == "__main__":
    main()
