#!/usr/bin/env python3
"""День 2: один и тот же вопрос с разным уровнем контроля над ответом.

    python3 compare.py                      # вопрос по умолчанию
    python3 compare.py "свой вопрос"
    python3 compare.py --only 1,6           # прогнать только выбранные варианты

Шесть прогонов: без ограничений, потом по одному рычагу (формат, длина
инструкцией, длина параметром, условие завершения) и всё вместе.
В конце — таблица, по которой видно, что изменилось.
"""

import shutil
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

# Клиент общий для всех дней начиная со второго, лежит в shared/
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from llm import Answer, LLMError, ask  # noqa: E402

QUESTION = "Что такое REST API?"

# Разделитель, по которому модель сама обрывает ответ. Выбран так, чтобы
# в обычном тексте не встречался: иначе оборвётся раньше времени.
STOP_MARKER = "<<<КОНЕЦ>>>"

FORMAT_SYSTEM = (
    "Отвечай строго в формате JSON по схеме:\n"
    '{"определение": строка, "принципы": [строка, строка, строка], "пример": строка}\n'
    "Никакого текста вне JSON. Значение «определение» — одно предложение."
)

LENGTH_SYSTEM = "Отвечай не длиннее 30 слов. Одно плотное предложение, без вступлений."

STOP_SYSTEM = (
    "Перечисли ровно три ключевых принципа, по одному на строку, без вступления.\n"
    f"Закончив третий, напиши на новой строке {STOP_MARKER} и замолчи."
)


@dataclass
class Variant:
    number: int
    title: str
    knobs: str                 # что именно добавили — это и показываем на видео
    kwargs: dict


VARIANTS = [
    Variant(
        1, "Без ограничений",
        "ничего не добавлено — как в дне 1",
        {},
    ),
    Variant(
        2, "Формат: строгий JSON",
        'system со схемой + response_format={"type": "json_object"}',
        {"system": FORMAT_SYSTEM, "json_mode": True},
    ),
    Variant(
        3, "Длина: инструкцией",
        "system: «не длиннее 30 слов»",
        {"system": LENGTH_SYSTEM},
    ),
    Variant(
        4, "Длина: параметром",
        "max_tokens=60 — жёсткий потолок на стороне API",
        {"max_tokens": 60},
    ),
    Variant(
        5, "Условие завершения",
        f'инструкция дописать {STOP_MARKER} + stop=["{STOP_MARKER}"]',
        {"system": STOP_SYSTEM, "stop": [STOP_MARKER]},
    ),
    Variant(
        6, "Всё вместе",
        "формат + длина + стоп-последовательность",
        {
            "system": FORMAT_SYSTEM + "\n" + LENGTH_SYSTEM,
            "json_mode": True,
            "max_tokens": 200,
            "stop": [STOP_MARKER],
        },
    ),
]

# ── оформление вывода ───────────────────────────────────────────────────
TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 92)


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


DIM, BOLD, CYAN, GREEN, YELLOW = "2", "1", "36", "32", "33"


def header(variant: Variant) -> None:
    line = "─" * (WIDTH - 2)
    print()
    print(paint(CYAN, f"╭{line}╮"))
    print(paint(CYAN, "│ ") + paint(BOLD, f"{variant.number}. {variant.title}"))
    print(paint(CYAN, "│ ") + paint(DIM, variant.knobs))
    print(paint(CYAN, f"╰{line}╯"))


def show(answer: Answer) -> None:
    for paragraph in answer.text.splitlines():
        if paragraph.strip():
            print(textwrap.fill(paragraph, width=WIDTH, subsequent_indent="  "))
        else:
            print()

    reason = answer.finish_reason
    colour = YELLOW if reason == "length" else GREEN
    print()
    print(
        paint(DIM, f"  {answer.words} слов · {answer.completion_tokens} токенов ответа · ")
        + paint(DIM, "finish_reason=")
        + paint(colour, reason)
        + paint(DIM, f" · {answer.seconds} с")
    )


def summary(rows: list[tuple[Variant, Answer]]) -> None:
    print()
    print(paint(BOLD, "Сравнение"))
    print(paint(DIM, f"  вопрос: {QUESTION}"))
    print()
    head = f"  {'№':<2} {'Вариант':<26} {'Слов':>5} {'Токенов':>8} {'Финиш':<9} {'Время':>7}"
    print(paint(BOLD, head))
    print(paint(DIM, "  " + "─" * (len(head) - 2)))

    for variant, answer in rows:
        reason = answer.finish_reason
        colour = YELLOW if reason == "length" else GREEN
        print(
            f"  {variant.number:<2} {variant.title:<26} "
            f"{answer.words:>5} {answer.completion_tokens:>8} "
            + paint(colour, f"{reason:<9}")
            + f" {answer.seconds:>6} с"
        )

    if len(rows) > 1:
        first, last = rows[0][1], rows[-1][1]
        if first.completion_tokens:
            ratio = first.completion_tokens / max(last.completion_tokens, 1)
            print()
            print(paint(DIM, f"  Ответ без ограничений длиннее последнего в {ratio:.1f} раза."))


def main() -> int:
    global QUESTION

    args = [a for a in sys.argv[1:]]
    chosen: set[int] | None = None
    if "--only" in args:
        index = args.index("--only")
        chosen = {int(n) for n in args[index + 1].split(",")}
        del args[index:index + 2]
    if args:
        QUESTION = " ".join(args).strip()

    variants = [v for v in VARIANTS if chosen is None or v.number in chosen]

    print(paint(BOLD, f"\nВопрос: {QUESTION}"))
    print(paint(DIM, f"Прогонов: {len(variants)}"))

    rows: list[tuple[Variant, Answer]] = []
    for variant in variants:
        header(variant)
        try:
            answer = ask(QUESTION, **variant.kwargs)
        except LLMError as exc:
            print(paint("31", f"  Ошибка: {exc}"))
            return 1
        show(answer)
        rows.append((variant, answer))

    summary(rows)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
