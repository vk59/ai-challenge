#!/usr/bin/env python3
"""День 24: замер режима «не знаю».

    python3 abstain_probe.py            # три группы вопросов
    python3 abstain_probe.py --no-llm   # только близость, без обращений к модели

Порог отказа выбран не на глаз. Здесь три группы вопросов, и видно, где
порог работает, а где нет:

    свои           ответ в базе есть — отказывать нельзя
    чужие          ответа нет и тема другая — порог обязан отказать
    околотемные    тема наша, но ответа нет. Близость высокая, порогом
                   не поймать — отказывать должна сама модель

Третья группа и объясняет, почему слоёв два. Один порог либо пропустит
околотемные, либо зарежет свои: по близости они неотличимы.
"""

import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from cite import ABSTAIN_BELOW, answer_with_citations  # noqa: E402
from index import STRUCTURAL, Index  # noqa: E402
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
    "Сколько заплатили за все эмбеддинги с начала проекта?",
]


def top_score(index: Index, question: str) -> float:
    found = index.search(question, STRUCTURAL, k=5)
    return found[0][1] if found else 0.0


def main() -> None:
    no_llm = "--no-llm" in sys.argv
    index = Index()

    groups = [
        ("свои (ответ есть)", [q["q"] for q in QUESTIONS], False),
        ("чужие (тема другая)", ALIEN, True),
        ("околотемные (тема наша, ответа нет)", NEARBY, True),
    ]
    collected: dict[str, list[float]] = {}
    tokens_saved = 0

    for title, questions, must_refuse in groups:
        print(f"\n── {title}")
        scores = []
        for question in questions:
            score = top_score(index, question)
            scores.append(score)
            gate = "отказ по порогу" if score < ABSTAIN_BELOW else "порог пройден"
            line = f"  {score:.3f}  {gate:16}"
            if not no_llm:
                result = answer_with_citations(question, index=index)
                if result.abstained:
                    tokens_saved += 1
                    verdict = "отказ до модели"
                elif not result.confident:
                    verdict = "модель отказалась"
                else:
                    verdict = f"ответил, цитат {len(result.quotes)}"
                correct = (must_refuse == (result.abstained
                                           or not result.confident))
                line += f" → {verdict:18} {'✓' if correct else '✗'}"
            print(line + f"  {question[:44]}")
        collected[title] = scores

    print(f"\n{'─' * 72}")
    for title, scores in collected.items():
        print(f"  {title[:38]:40} min {min(scores):.3f}  "
              f"медиана {statistics.median(scores):.3f}  max {max(scores):.3f}")

    own = collected["свои (ответ есть)"]
    alien = collected["чужие (тема другая)"]
    print(f"\nЗазор между своими и чужими: {min(own):.3f} против "
          f"{max(alien):.3f} — порог {ABSTAIN_BELOW:.2f} ложится посередине.")
    nearby = collected["околотемные (тема наша, ответа нет)"]
    print(f"Околотемные доходят до {max(nearby):.3f}, то есть выше порога "
          f"и выше\nминимума своих. Порогом их не отличить — поэтому второй "
          f"слой\nобязателен: модель сама говорит, что ответа нет.")
    if not no_llm:
        print(f"\nОтказов до обращения к модели: {tokens_saved} "
              f"(это нулевой расход токенов)")


if __name__ == "__main__":
    main()
