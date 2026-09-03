#!/usr/bin/env python3
"""День 4: один и тот же запрос при temperature 0, 0.7 и 1.2.

    python3 compare.py              # 5 прогонов на температуру
    python3 compare.py --runs 3     # быстрее и дешевле

Три грани из задания меряются на двух разных задачах — иначе никак:
у задачи с правильным ответом нельзя измерить креативность, а у творческой
нет правильного ответа, чтобы измерить точность.

    точность      → задача с проверяемым ответом (что выведет код)
    креативность  → метафора, оценивают вслепую моделью-судьёй
    разнообразие  → обе задачи: насколько ответы отличаются друг от друга
"""

import contextlib
import io
import json
import re
import shutil
import statistics
import sys
import textwrap
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from llm import LLMError, ask  # noqa: E402

TEMPERATURES = [0.0, 0.7, 1.2]

# ── задача на точность ──────────────────────────────────────────────────
# Два эффекта Python сразу: x=i захватывает значение в момент объявления
# (0,1,2,3), а само i в теле лямбды берётся при вызове, когда оно уже 10.
# По отдельности модель знает оба, вместе они встречаются редко.
CODE = """vals = []
for i in range(4):
    vals.append(lambda x=i: x * i)
i = 10
print(sum(f() for f in vals))
"""


def ground_truth() -> int:
    """Правильный ответ добывается запуском кода, а не рассуждением о нём."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exec(CODE, {})  # noqa: S102 — исполняем собственную константу выше
    return int(buffer.getvalue().strip())


EXPECTED = ground_truth()

ANSWER_FORMAT = "\n\nПоследней строкой напиши ровно: ОТВЕТ: <число>"
ACCURACY_TASK = f"Что выведет этот код?\n\n```python\n{CODE}```{ANSWER_FORMAT}"
ANSWER_RE = re.compile(r"ОТВЕТ:\s*\**\s*(-?\d+)")

# ── задача на креативность ──────────────────────────────────────────────
CREATIVE_TASK = (
    "Объясни через метафору, что такое рекурсия. "
    "Одно-два предложения, без технических терминов и без слова «рекурсия»."
)

JUDGE_SYSTEM = (
    "Ты оцениваешь метафоры. Для каждого варианта поставь две оценки от 1 до 10:\n"
    "«оригинальность» — насколько образ неожиданный, а не заезженный;\n"
    "«понятность» — объясняет ли метафора суть тому, кто не знает предмета.\n"
    "Верни JSON вида {\"оценки\": [{\"id\": 1, \"оригинальность\": 7, \"понятность\": 9}]}. "
    "Оцени все варианты, ничего не пропусти."
)


@dataclass
class Run:
    temperature: float
    text: str
    tokens: int
    seconds: float

    @property
    def answer(self) -> int | None:
        found = ANSWER_RE.findall(self.text)
        return int(found[-1]) if found else None

    @property
    def correct(self) -> bool:
        return self.answer == EXPECTED


# ── метрики ─────────────────────────────────────────────────────────────
def word_set(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def diversity(texts: list[str]) -> float:
    """Среднее попарное различие текстов: 0 — все одинаковые, 1 — ни одного общего слова.

    Мера Жаккара по множествам слов. Простая и без зависимостей, а для вопроса
    «повторяется ли модель» её более чем достаточно.
    """
    pairs = list(combinations(texts, 2))
    if not pairs:
        return 0.0
    scores = []
    for first, second in pairs:
        a, b = word_set(first), word_set(second)
        scores.append(1 - len(a & b) / len(a | b) if (a | b) else 0.0)
    return statistics.mean(scores)


def judge(samples: list[tuple[float, str]]) -> dict[int, dict]:
    """Оценивает метафоры вслепую: судья не знает, какая температура их породила.

    Порядок перемешан, температуры не подписаны — иначе оценка поехала бы
    вслед за ожиданиями. Сам судья работает при temperature=0, чтобы оценки
    не гуляли от запуска к запуску.
    """
    listing = "\n\n".join(
        f"Вариант {index}:\n{text}" for index, (_, text) in enumerate(samples, 1)
    )
    verdict = ask(listing, system=JUDGE_SYSTEM, json_mode=True, temperature=0.0)

    try:
        parsed = json.loads(verdict.text)
    except json.JSONDecodeError:
        return {}
    return {int(item["id"]): item for item in parsed.get("оценки", [])}


# ── оформление ──────────────────────────────────────────────────────────
TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 92)
DIM, BOLD, CYAN, GREEN, RED, YELLOW = "2", "1", "36", "32", "31", "33"


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def section(title: str) -> None:
    print()
    print(paint(CYAN, "━" * WIDTH))
    print(paint(BOLD, f" {title}"))
    print(paint(CYAN, "━" * WIDTH))


def main() -> int:
    runs = 5
    args = sys.argv[1:]
    if "--runs" in args:
        runs = int(args[args.index("--runs") + 1])

    print(paint(BOLD, "\nДень 4 — температура"))
    print(paint(DIM, f"  температуры: {TEMPERATURES}, прогонов на каждую: {runs}"))

    # ── точность ────────────────────────────────────────────────────────
    section("Точность — «что выведет код», правильный ответ " + str(EXPECTED))
    print(paint(DIM, textwrap.indent(CODE.rstrip(), "  ")))

    accuracy: dict[float, list[Run]] = {}
    for temperature in TEMPERATURES:
        results = []
        for _ in range(runs):
            try:
                answer = ask(ACCURACY_TASK, temperature=temperature)
            except LLMError as exc:
                print(paint(RED, f"  Ошибка: {exc}"))
                return 1
            results.append(Run(temperature, answer.text,
                               answer.completion_tokens, answer.seconds))
        accuracy[temperature] = results

        hits = sum(r.correct for r in results)
        colour = GREEN if hits == runs else (RED if hits == 0 else YELLOW)
        answers = ", ".join(str(r.answer) for r in results)
        print(f"\n  t={temperature:<4} " + paint(colour, f"{hits}/{runs} верно")
              + paint(DIM, f"   ответы: {answers}"))

    # ── креативность и разнообразие ─────────────────────────────────────
    section("Креативность — метафора для рекурсии")

    creative: dict[float, list[Run]] = {}
    for temperature in TEMPERATURES:
        results = []
        for _ in range(runs):
            try:
                answer = ask(CREATIVE_TASK, temperature=temperature)
            except LLMError as exc:
                print(paint(RED, f"  Ошибка: {exc}"))
                return 1
            results.append(Run(temperature, answer.text.strip(),
                               answer.completion_tokens, answer.seconds))
        creative[temperature] = results

        print(paint(BOLD, f"\n  t={temperature}"))
        for index, run in enumerate(results, 1):
            print(textwrap.fill(run.text, width=WIDTH - 6,
                                initial_indent=f"    {index}. ",
                                subsequent_indent="       "))

    print(paint(DIM, "\n  Отдаю метафоры судье — вперемешку и без подписей…"))
    samples = [(t, r.text) for t in TEMPERATURES for r in creative[t]]
    scores = judge(samples)

    ratings: dict[float, list[tuple[int, int]]] = {t: [] for t in TEMPERATURES}
    for index, (temperature, _) in enumerate(samples, 1):
        item = scores.get(index)
        if item:
            ratings[temperature].append(
                (item.get("оригинальность", 0), item.get("понятность", 0))
            )

    # ── итог ────────────────────────────────────────────────────────────
    section("Сводка")
    head = (f"  {'t':<5} {'Точность':>9} {'Разных':>8} {'Разнообразие':>13} "
            f"{'Оригинальность':>15} {'Понятность':>11}")
    print(paint(BOLD, head))
    print(paint(DIM, "  " + "─" * (len(head) - 2)))

    for temperature in TEMPERATURES:
        acc = accuracy[temperature]
        cre = creative[temperature]
        hits = sum(r.correct for r in acc)
        unique = len({r.text for r in cre})
        div = diversity([r.text for r in cre])
        marks = ratings[temperature]
        originality = statistics.mean(m[0] for m in marks) if marks else 0
        clarity = statistics.mean(m[1] for m in marks) if marks else 0

        colour = GREEN if hits == runs else (RED if hits == 0 else YELLOW)
        print(f"  {temperature:<5} " + paint(colour, f"{hits}/{runs:<7}")
              + f" {unique:>7}/{runs} {div:>12.2f} "
                f"{originality:>15.1f} {clarity:>11.1f}")

    print(paint(DIM, "\n  Разнообразие: 0 — ответы совпадают дословно, 1 — ни одного общего слова."))
    print(paint(DIM, "  Оценки от 1 до 10 ставила модель-судья вслепую при temperature=0."))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
