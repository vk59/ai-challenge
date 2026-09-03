#!/usr/bin/env python3
"""День 3: одна задача, решённая четырьмя способами рассуждения.

    python3 solve.py                # 3 прогона каждым способом
    python3 solve.py --runs 1       # по одному прогону (быстро)
    python3 solve.py --runs 5       # надёжнее статистика, дольше и дороже

Способы:
    1. Прямой ответ            — вопрос как есть
    2. Пошагово                — «разбей на шаги»
    3. Мета-промпт             — модель пишет промпт себе, потом решает по нему
    4. Группа экспертов        — аналитик → инженер → критик → сведение

У задачи есть заранее известный правильный ответ, поэтому «точнее» здесь
не мнение, а измеряемая величина: доля верных ответов по нескольким прогонам.
Модель недетерминирована, по одному запуску судить нельзя.
"""

import re
import shutil
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from llm import LLMError, ask  # noqa: E402

# ── задача ──────────────────────────────────────────────────────────────
# Нужен настоящий перебор с двумя условиями сразу: наугад не угадать,
# «в уме» легко потерять часть вариантов. Первая версия задания (подсчёт
# цифр в нумерации страниц) оказалась слишком простой — все четыре способа
# давали верный ответ, и сравнивать было нечего.
TASK = (
    "Сколько существует трёхзначных чисел, у которых сумма цифр равна 13 "
    "и которые при этом делятся на 4 без остатка?"
)


def ground_truth() -> int:
    """Правильный ответ считается перебором, а не берётся на веру."""
    return sum(
        1 for n in range(100, 1000)
        if n % 4 == 0 and sum(int(digit) for digit in str(n)) == 13
    )


EXPECTED = ground_truth()

# Один и тот же хвост добавляется во все четыре способа, иначе ответы
# не сравнить и не распарсить. Это формат вывода, а не подсказка к решению.
ANSWER_FORMAT = "\n\nПоследней строкой напиши ровно: ОТВЕТ: <число>"

ANSWER_RE = re.compile(r"ОТВЕТ:\s*\**\s*([0-9][0-9\s]*)")


@dataclass
class Solution:
    method: str
    text: str
    answer: int | None
    tokens: int
    seconds: float
    calls: int = 1
    notes: list[tuple[str, str]] = field(default_factory=list)  # что показать по дороге

    @property
    def correct(self) -> bool:
        return self.answer == EXPECTED


def extract(text: str) -> int | None:
    """Вытаскивает число из строки «ОТВЕТ: …», игнорируя пробелы внутри."""
    matches = ANSWER_RE.findall(text)
    if not matches:
        return None
    try:
        return int(matches[-1].replace(" ", ""))
    except ValueError:
        return None


# ── способ 1: прямой ответ ──────────────────────────────────────────────
def direct() -> Solution:
    answer = ask(TASK + ANSWER_FORMAT)
    return Solution("1. Прямой ответ", answer.text, extract(answer.text),
                    answer.completion_tokens, answer.seconds)


# ── способ 2: пошагово ──────────────────────────────────────────────────
STEP_SYSTEM = (
    "Решай задачу пошагово. Сначала выпиши, что дано и что требуется найти. "
    "Затем разбей решение на пронумерованные шаги и выполни каждый, "
    "показывая промежуточные вычисления. Только после этого дай итог."
)


def step_by_step() -> Solution:
    answer = ask(TASK + ANSWER_FORMAT, system=STEP_SYSTEM)
    return Solution("2. Пошагово", answer.text, extract(answer.text),
                    answer.completion_tokens, answer.seconds)


# ── способ 3: мета-промпт ───────────────────────────────────────────────
META_SYSTEM = (
    "Ты составляешь промпты для языковых моделей. "
    "Тебе дают задачу — ты пишешь промпт, который поможет решить её надёжно "
    "и без типичных ошибок. Саму задачу НЕ решай и ответ не называй. "
    "Верни только текст промпта, без пояснений и без кавычек."
)


def meta_prompt() -> Solution:
    written = ask(f"Задача:\n{TASK}", system=META_SYSTEM)

    solved = ask(
        f"{written.text}\n\nЗадача:\n{TASK}{ANSWER_FORMAT}"
    )

    return Solution(
        "3. Мета-промпт", solved.text, extract(solved.text),
        written.completion_tokens + solved.completion_tokens,
        round(written.seconds + solved.seconds, 1),
        calls=2,
        notes=[("Промпт, который модель написала себе", written.text)],
    )


# ── способ 4: группа экспертов ──────────────────────────────────────────
ANALYST = (
    "Ты аналитик. Разбери условие задачи: что дано, что требуется найти, "
    "на какие случаи распадается подсчёт. Составь план решения. "
    "Сам НЕ считай и числового ответа не давай."
)
ENGINEER = (
    "Ты инженер-вычислитель. По разбору аналитика выполни расчёт, "
    "показывая каждое действие. Будь особенно внимателен к границам диапазонов."
)
CRITIC = (
    "Ты критик. Перед тобой разбор аналитика и расчёт инженера. "
    "Найди ошибки: перепроверь границы диапазонов, арифметику, логику. "
    "Если ошибок нет — так и скажи. Затем дай свой ответ."
)
LEAD = (
    "Ты ведущий. Перед тобой мнения трёх экспертов. "
    "Где они расходятся — реши, кто прав, и коротко объясни почему. "
    "Затем дай окончательный ответ."
)


def experts() -> Solution:
    analysis = ask(TASK, system=ANALYST)

    calculation = ask(
        f"Задача:\n{TASK}\n\nРазбор аналитика:\n{analysis.text}{ANSWER_FORMAT}",
        system=ENGINEER,
    )

    review = ask(
        f"Задача:\n{TASK}\n\nРазбор аналитика:\n{analysis.text}\n\n"
        f"Расчёт инженера:\n{calculation.text}{ANSWER_FORMAT}",
        system=CRITIC,
    )

    final = ask(
        f"Задача:\n{TASK}\n\nАналитик:\n{analysis.text}\n\n"
        f"Инженер:\n{calculation.text}\n\nКритик:\n{review.text}{ANSWER_FORMAT}",
        system=LEAD,
    )

    tokens = sum(a.completion_tokens for a in (analysis, calculation, review, final))
    seconds = sum(a.seconds for a in (analysis, calculation, review, final))

    return Solution(
        "4. Группа экспертов", final.text, extract(final.text),
        tokens, round(seconds, 1), calls=4,
        notes=[
            ("Аналитик — разбор и план", analysis.text),
            (f"Инженер — расчёт (ответ: {extract(calculation.text)})", calculation.text),
            (f"Критик — проверка (ответ: {extract(review.text)})", review.text),
        ],
    )


METHODS = [direct, step_by_step, meta_prompt, experts]

# ── вывод ───────────────────────────────────────────────────────────────
TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 92)
DIM, BOLD, CYAN, GREEN, RED, MAGENTA = "2", "1", "36", "32", "31", "35"


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def wrap(text: str, indent: str = "") -> str:
    out = []
    for line in text.splitlines():
        out.append(textwrap.fill(line, width=WIDTH, initial_indent=indent,
                                 subsequent_indent=indent + "  ") if line.strip() else "")
    return "\n".join(out)


def show(solution: Solution) -> None:
    line = "─" * (WIDTH - 2)
    print()
    print(paint(CYAN, f"╭{line}╮"))
    print(paint(CYAN, "│ ") + paint(BOLD, solution.method))
    print(paint(CYAN, f"╰{line}╯"))

    for title, body in solution.notes:
        print(paint(MAGENTA, f"  ┌ {title}"))
        print(paint(DIM, wrap(body, "  │ ")))
        print(paint(MAGENTA, "  └"))
        print()

    print(wrap(solution.text))
    verdict = (paint(GREEN, f"верно ({EXPECTED})") if solution.correct
               else paint(RED, f"неверно: {solution.answer} вместо {EXPECTED}"))
    print()
    print(paint(DIM, f"  {solution.tokens} токенов · {solution.calls} запрос(ов) · "
                     f"{solution.seconds} с · ") + verdict)


def summary(results: dict[str, list[Solution]], runs: int) -> None:
    print()
    print(paint(BOLD, "Сравнение"))
    print(paint(DIM, f"  задача: {TASK}"))
    print(paint(DIM, f"  правильный ответ: {EXPECTED}, прогонов на способ: {runs}"))
    print()
    head = (f"  {'Способ':<22} {'Верно':>7} {'Ответы':<18} "
            f"{'Токенов':>8} {'Запросов':>9} {'Время':>7}")
    print(paint(BOLD, head))
    print(paint(DIM, "  " + "─" * (len(head) - 2)))

    for method, solutions in results.items():
        hits = sum(s.correct for s in solutions)
        answers = ", ".join(str(s.answer) for s in solutions)
        tokens = sum(s.tokens for s in solutions) // len(solutions)
        seconds = sum(s.seconds for s in solutions) / len(solutions)
        colour = GREEN if hits == len(solutions) else (RED if hits == 0 else "33")
        print(f"  {method:<22} " + paint(colour, f"{hits}/{len(solutions):<5}") +
              f" {answers:<18} {tokens:>8} {solutions[0].calls:>9} {seconds:>6.1f} с")


def main() -> int:
    runs = 3
    args = sys.argv[1:]
    if "--runs" in args:
        runs = int(args[args.index("--runs") + 1])

    print(paint(BOLD, f"\nЗадача: {TASK}"))
    print(paint(DIM, f"Правильный ответ: {EXPECTED} (посчитан перебором в ground_truth())"))
    print(paint(DIM, f"Прогонов на способ: {runs}"))

    results: dict[str, list[Solution]] = {}
    for run in range(runs):
        if runs > 1:
            print(paint(BOLD, f"\n\n═══ Прогон {run + 1} из {runs} ═══"))
        for method in METHODS:
            try:
                solution = method()
            except LLMError as exc:
                print(paint(RED, f"\n  Ошибка: {exc}"))
                return 1
            results.setdefault(solution.method, []).append(solution)
            # Полные тексты показываем только в первом прогоне, дальше — сводка
            if run == 0:
                show(solution)
            else:
                mark = "✓" if solution.correct else "✗"
                colour = GREEN if solution.correct else RED
                print(paint(colour, f"  {mark} ") + f"{solution.method:<22} "
                      + paint(DIM, f"ответ: {solution.answer}"))

    summary(results, runs)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
