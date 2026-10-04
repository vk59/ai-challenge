#!/usr/bin/env python3
"""День 22: один вопрос в двух режимах, бок о бок.

    python3 ask.py "какой MRR у структурной нарезки"
    python3 ask.py --mode rag "то же самое только с поиском"
    python3 ask.py -k 10 "больше выдержек в промпте"
    python3 ask.py --show "плюс что именно нашлось"

Главное, что видно: без RAG модель отвечает про проект, которого не знает,
и иногда делает это уверенно.
"""

import sys
from pathlib import Path

ЗДЕСЬ = Path(__file__).resolve().parent
sys.path.insert(0, str(ЗДЕСЬ.parents[1] / "shared"))
sys.path.insert(0, str(ЗДЕСЬ))

from index import Index  # noqa: E402
from rag import КУСКОВ, LLMError, спросить  # noqa: E402


def напечатать(о, показать_куски: bool) -> None:
    print(f"\n{'═' * 74}")
    заголовок = "БЕЗ RAG — модель отвечает из того, что помнит" if о.mode == "plain" \
        else "С RAG — модель отвечает по нашим документам"
    print(заголовок)
    print(f"{'─' * 74}")
    print((о.text or "(пусто)").strip())

    print(f"{'─' * 74}")
    хвост = [f"{о.total_tokens} токенов", f"{о.seconds:.1f}с"]
    if о.mode == "rag":
        хвост.insert(0, f"поиск {о.search_seconds:.2f}с")
    print("  " + " · ".join(хвост))

    if о.mode != "rag":
        return

    print(f"  ссылки: {о.cited or 'ни одной'}"
          + (f"  ⚠ выдуманные: {о.invented}" if о.invented else ""))
    if о.sources:
        print("  источники:")
        for и in о.sources:
            print(f"    {и}")
    if о.refused:
        print("  модель сказала, что в документах этого нет")

    if показать_куски:
        print(f"\n  что было выдано модели ({len(о.chunks)} выдержек):")
        for номер, к in enumerate(о.chunks, 1):
            отметка = "→" if номер in о.cited else " "
            print(f"  {отметка} [{номер}] {к['score']:.3f}  {к['source']}")
            print(f"        раздел: {к['section']}")


def main() -> None:
    аргументы = sys.argv[1:]
    режимы = ["plain", "rag"]
    кусков = КУСКОВ
    показать = False
    слова: list[str] = []

    пропустить = False
    for номер, аргумент in enumerate(аргументы):
        if пропустить:
            пропустить = False
            continue
        if аргумент == "--mode" and номер + 1 < len(аргументы):
            if аргументы[номер + 1] in режимы:
                режимы = [аргументы[номер + 1]]
            пропустить = True
        elif аргумент in ("-k", "--chunks") and номер + 1 < len(аргументы):
            кусков = max(1, int(аргументы[номер + 1]))
            пропустить = True
        elif аргумент == "--show":
            показать = True
        else:
            слова.append(аргумент)

    вопрос = " ".join(слова).strip()
    if not вопрос:
        print(__doc__)
        sys.exit(1)

    print(f"Вопрос: {вопрос}")
    индекс = Index()
    ответы = {}
    for режим in режимы:
        настройки = {"индекс": индекс, "кусков": кусков} if режим == "rag" else {}
        try:
            ответы[режим] = спросить(вопрос, режим=режим, **настройки)
        except LLMError as сбой:
            print(f"\n{режим}: сбой — {сбой}")
            continue
        напечатать(ответы[режим], показать)

    if len(ответы) == 2:
        б, р = ответы["plain"], ответы["rag"]
        print(f"\n{'═' * 74}")
        print(f"Токенов: без RAG {б.total_tokens}, с RAG {р.total_tokens} "
              f"(в {р.total_tokens / max(1, б.total_tokens):.1f} раза больше)")
        print("Проверяемость: без RAG ссылок нет вовсе, "
              f"с RAG — {len(р.sources)} источник(а)")


if __name__ == "__main__":
    main()
