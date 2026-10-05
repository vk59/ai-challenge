#!/usr/bin/env python3
"""День 26: что меняется, когда локальной модели дать наши документы.

    python3 with_rag.py                      # три вопроса, два режима
    python3 with_rag.py --model qwen2.5:7b
    python3 with_rag.py --cloud              # для сравнения

Замер запросов показал единственное, чего не исправил размер модели:
и 3B, и 7B уверенно отвечают, что MRR — это Monthly Recurring Revenue.
Это не глупость, а отсутствие знаний: метрика узкая, в обучающих данных
маленькой модели её толкуют иначе.

Отсюда вопрос дня: лечится ли незнание поиском по своим документам.
Проверяется на вопросах, где локальная модель заведомо не знает ответа:
один общеизвестный термин, понятый неправильно, и два факта, которых нет
нигде, кроме нашего репозитория.

Цитаты проверяются ровно так же, как в дне 24 — дословно по куску.
Проверка от того, кто написал текст, не зависит.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from cite import answer_with_citations  # noqa: E402
from index import Index  # noqa: E402
from llm import DEFAULT_LOCAL_MODEL, LOCAL, LLMError, ask  # noqa: E402

PLAIN_SYSTEM = ("Отвечай кратко, два-три предложения. "
                "Если не знаешь — так и скажи.")

# (вопрос, что должно быть в правильном ответе)
QUESTIONS: list[tuple[str, list[str]]] = [
    ("Что такое MRR в оценке поисковых систем? Ответь в двух предложениях.",
     ["reciprocal", "обратн"]),
    ("Какая стратегия нарезки документов на куски победила в сравнении "
     "и с каким результатом?",
     ["structural", "0.774"]),
    ("Что делает build_app.sh, чтобы собранное приложение могло читать "
     "историю репозитория?",
     ["зеркал", "mirror"]),
]


def matches(text: str, wanted: list[str]) -> bool:
    low = (text or "").lower()
    return any(w.lower() in low for w in wanted)


def main() -> None:
    argv = sys.argv[1:]
    cloud = "--cloud" in argv
    model = None
    if "--model" in argv:
        position = argv.index("--model")
        if position + 1 < len(argv):
            model = argv[position + 1]

    provider = None if cloud else LOCAL
    where = "облако (DeepSeek)" if cloud else f"локально ({model or DEFAULT_LOCAL_MODEL})"
    print(f"Модель: {where}")

    index = Index()
    stats = index.stats("structural")
    if not stats["chunks"]:
        print("Индекс пуст. Соберите: cd ../day-21-indexing && python3 build.py")
        sys.exit(1)
    print(f"Индекс: {stats['chunks']} кусков\n")

    plain_right = rag_right = 0
    verified_total = fabricated_total = 0

    for number, (question, wanted) in enumerate(QUESTIONS, 1):
        print(f"{'─' * 76}")
        print(f"{number}. {question}")
        print(f"   ждём: {' / '.join(wanted)}")

        try:
            bare = ask(question, system=PLAIN_SYSTEM, provider=provider,
                       model=model, max_tokens=240, temperature=0.2)
        except LLMError as failure:
            print(f"   без RAG — сбой: {failure}")
            continue
        bare_ok = matches(bare.text, wanted)
        plain_right += bare_ok
        print(f"\n   без RAG {'✓' if bare_ok else '✗'}  "
              f"{' '.join((bare.text or '').split())[:180]}")

        try:
            cited = answer_with_citations(question, index=index, k=5,
                                          abstain_below=None,
                                          provider=provider, model=model,
                                          max_tokens=700)
        except LLMError as failure:
            print(f"   с RAG — сбой: {failure}")
            continue
        rag_ok = matches(cited.answer, wanted)
        rag_right += rag_ok
        verified_total += cited.verified_quotes
        fabricated_total += cited.fabricated_quotes
        print(f"\n   с RAG   {'✓' if rag_ok else '✗'}  "
              f"{' '.join((cited.answer or '').split())[:180]}")
        print(f"           источников {len(cited.sources)}, цитат "
              f"{len(cited.quotes)} (подтверждено {cited.verified_quotes}"
              + (f", ВЫДУМАНО {cited.fabricated_quotes}"
                 if cited.fabricated_quotes else "") + ")")
        for source in cited.sources[:2]:
            print(f"           {source.chunk_id}")

    total = len(QUESTIONS)
    print(f"\n{'═' * 76}")
    print(f"{'режим':10} {'верных ответов':>16}")
    print(f"{'без RAG':10} {plain_right:>10}/{total:<4}")
    print(f"{'с RAG':10} {rag_right:>10}/{total:<4}")
    print(f"\nцитат подтверждено {verified_total}"
          + (f", выдумано {fabricated_total}" if fabricated_total else
             ", выдуманных нет"))
    if rag_right > plain_right:
        print("\nВывод: маленькая модель не знает ответов, но умеет читать. "
              "\nПоиск по своим документам закрывает разрыв в знаниях — "
              "\nи это главный довод в пользу локальной модели: "
              "\nбесплатно, без сети и без утечки документов наружу.")


if __name__ == "__main__":
    main()
