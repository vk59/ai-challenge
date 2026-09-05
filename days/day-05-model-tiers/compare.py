#!/usr/bin/env python3
"""День 5: один и тот же запрос на моделях разного класса.

    python3 compare.py                  # 3 прогона на модель
    python3 compare.py --runs 1         # быстро и дёшево
    python3 compare.py --with-flagship  # добавить Claude Opus, нужен баланс

Пять ступеней от 1B до флагмана. Замеряется время до первого токена,
полное время, токены и стоимость — цены берутся живьём из каталога
OpenRouter, а не зашиты в код: они меняются, зашитые протухнут.

Методика: все модели опрашиваются через OpenRouter, включая DeepSeek.
Прямой вызов к DeepSeek на один сетевой хоп короче, и сравнивать с ним
скорость остальных было бы нечестно. Накладные расходы прокси меряются
отдельно, в конце.
"""

import contextlib
import io
import json
import re
import shutil
import statistics
import sys
import textwrap
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from llm import DEEPSEEK, OPENROUTER, LLMError, ask, ask_stream  # noqa: E402

CATALOGUE = "https://openrouter.ai/api/v1/models"

# Всем моделям выдаётся одинаковый потолок ответа. Две причины: сравнение
# честнее, когда бюджет один на всех, и OpenRouter резервирует средства
# по max_tokens — без него он закладывает весь контекст модели (до 131k)
# и отказывает с 402, даже если ответ будет коротким.
MAX_TOKENS = 900


@dataclass(frozen=True)
class Tier:
    label: str
    model: str
    note: str
    needs_credits: bool = False   # не влезает в бесплатный тир OpenRouter

    @property
    def link(self) -> str:
        return f"https://openrouter.ai/{self.model}"


LADDER = [
    Tier("Крошечная", "meta-llama/llama-3.2-1b-instruct", "1B параметров, самый низ каталога"),
    Tier("Средняя", "google/gemma-2-27b-it", "27B, крепкий середняк"),
    Tier("Рабочая", "deepseek/deepseek-v4-flash", "на ней сделаны дни 1–4"),
    Tier("Старшая", "deepseek/deepseek-v4-pro", "тяжёлая из той же линейки"),
    Tier("Флагман", "anthropic/claude-opus-4", "верх рынка", needs_credits=True),
]

# ── задача с проверяемым ответом ────────────────────────────────────────
# Та же, что в дне 4: два эффекта Python сразу. Слабая модель на ней
# спотыкается, сильная проходит — ровно то, что нужно для сравнения классов.
CODE = """vals = []
for i in range(4):
    vals.append(lambda x=i: x * i)
i = 10
print(sum(f() for f in vals))
"""


def ground_truth() -> int:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exec(CODE, {})  # noqa: S102 — исполняем собственную константу выше
    return int(buffer.getvalue().strip())


EXPECTED = ground_truth()

ANSWER_FORMAT = "\n\nПоследней строкой напиши ровно: ОТВЕТ: <число>"
ACCURACY_TASK = f"Что выведет этот код?\n\n```python\n{CODE}```{ANSWER_FORMAT}"
ANSWER_RE = re.compile(r"ОТВЕТ:\s*\**\s*(-?\d+)")

# ── открытый вопрос для оценки качества ─────────────────────────────────
OPEN_TASK = (
    "Объясни за 3–4 предложения, почему в многопоточной программе "
    "нельзя просто так читать и писать одну переменную из разных потоков. "
    "Пиши для джуна, который знает синтаксис, но не сталкивался с гонками."
)

JUDGE_SYSTEM = (
    "Ты оцениваешь объяснения для начинающего программиста. "
    "Для каждого варианта поставь оценки от 1 до 10:\n"
    "«точность» — нет ли фактических ошибок;\n"
    "«ясность» — поймёт ли джун;\n"
    "«полнота» — раскрыта ли суть проблемы.\n"
    'Верни JSON: {"оценки": [{"id": 1, "точность": 8, "ясность": 7, "полнота": 6}]}. '
    "Оцени все варианты, ничего не пропусти."
)


@dataclass
class Shot:
    """Один замер: что ответила модель и чего это стоило."""

    text: str
    first_token: float      # секунд до первого токена — это чувствует пользователь
    total: float
    prompt_tokens: int
    completion_tokens: int
    cost: float = 0.0

    @property
    def answer(self) -> int | None:
        found = ANSWER_RE.findall(self.text)
        return int(found[-1]) if found else None

    @property
    def correct(self) -> bool:
        return self.answer == EXPECTED


@dataclass
class Result:
    tier: Tier
    shots: list[Shot] = field(default_factory=list)
    open_answer: Shot | None = None


def fetch_prices() -> dict[str, tuple[float, float]]:
    """Цены за 1M токенов (вход, выход) прямо из каталога OpenRouter."""
    with urllib.request.urlopen(CATALOGUE, timeout=60) as response:
        catalogue = json.loads(response.read())["data"]
    return {
        item["id"]: (
            float(item["pricing"]["prompt"]) * 1_000_000,
            float(item["pricing"]["completion"]) * 1_000_000,
        )
        for item in catalogue
    }


def measure(prompt: str, model: str, prices: dict) -> Shot:
    """Запрос со стримингом: только так видно время до первого токена."""
    stats: dict = {}
    chunks: list[str] = []
    started = time.monotonic()
    first: float | None = None

    for delta in ask_stream(prompt, model=model, provider=OPENROUTER,
                            temperature=0.0, max_tokens=MAX_TOKENS, stats=stats):
        if first is None:
            first = time.monotonic() - started
        chunks.append(delta)

    total = time.monotonic() - started
    usage = stats.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    price_in, price_out = prices.get(model, (0.0, 0.0))
    cost = (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000

    return Shot("".join(chunks), round(first or total, 2), round(total, 2),
                prompt_tokens, completion_tokens, cost)


def judge(samples: list[tuple[Tier, str]]) -> dict[int, dict]:
    """Оценка вслепую: судья не знает, какая модель что написала."""
    listing = "\n\n".join(
        f"Вариант {index}:\n{text}" for index, (_, text) in enumerate(samples, 1)
    )
    verdict = ask(listing, system=JUDGE_SYSTEM, json_mode=True, temperature=0.0,
                  model="deepseek-v4-pro")
    try:
        return {int(item["id"]): item for item in json.loads(verdict.text)["оценки"]}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


# ── оформление ──────────────────────────────────────────────────────────
TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((100, 24)).columns, 100)
DIM, BOLD, CYAN, GREEN, RED, YELLOW = "2", "1", "36", "32", "31", "33"


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def section(title: str) -> None:
    print()
    print(paint(CYAN, "━" * WIDTH))
    print(paint(BOLD, f" {title}"))
    print(paint(CYAN, "━" * WIDTH))


def money(value: float) -> str:
    """Микроцены читаются плохо, поэтому очень мелкие показываем иначе."""
    if value == 0:
        return "бесплатно"
    if value < 0.001:
        return f"{value * 100_000:.2f}¢/100"
    return f"${value:.4f}"


def main() -> int:
    runs = 3
    args = sys.argv[1:]
    if "--runs" in args:
        runs = int(args[args.index("--runs") + 1])

    # Флагман стоит $15/$75 за 1M, и OpenRouter резервирует деньги заранее —
    # на бесплатном тире он отвечает 402 ещё до генерации. Включается флагом,
    # когда на счету есть хотя бы пара долларов.
    ladder = [t for t in LADDER if not t.needs_credits or "--with-flagship" in args]
    skipped = [t for t in LADDER if t not in ladder]

    print(paint(BOLD, "\nДень 5 — модели разного класса"))
    print(paint(DIM, f"  ступеней: {len(ladder)}, прогонов на модель: {runs}"))
    print(paint(DIM, "  все запросы идут через OpenRouter — иначе сравнение скорости нечестное"))

    try:
        prices = fetch_prices()
    except Exception as exc:
        print(paint(RED, f"  Не удалось получить цены: {exc}"))
        return 1
    print(paint(DIM, f"  цены получены из каталога: {len(prices)} моделей"))
    for tier in skipped:
        print(paint(YELLOW, f"  пропущена «{tier.label}» ({tier.model}) — "
                            "нужен баланс OpenRouter, включается флагом --with-flagship"))

    results = [Result(tier) for tier in ladder]

    # ── точность ────────────────────────────────────────────────────────
    section(f"Задача с проверяемым ответом — правильный {EXPECTED}")
    print(paint(DIM, textwrap.indent(CODE.rstrip(), "  ")))

    for result in results:
        print(f"\n  {paint(BOLD, result.tier.label):<22} {paint(DIM, result.tier.model)}")
        for _ in range(runs):
            try:
                result.shots.append(measure(ACCURACY_TASK, result.tier.model, prices))
            except LLMError as exc:
                print(paint(RED, f"    ошибка: {str(exc)[:100]}"))
        if not result.shots:
            continue
        hits = sum(s.correct for s in result.shots)
        colour = GREEN if hits == len(result.shots) else (RED if hits == 0 else YELLOW)
        answers = ", ".join(str(s.answer) for s in result.shots)
        print("    " + paint(colour, f"{hits}/{len(result.shots)} верно")
              + paint(DIM, f"   ответы: {answers}"))

    # ── открытый вопрос ─────────────────────────────────────────────────
    section("Открытый вопрос — качество оценивает судья вслепую")
    print(paint(DIM, textwrap.fill(OPEN_TASK, width=WIDTH - 4, initial_indent="  ",
                                   subsequent_indent="  ")))

    for result in results:
        try:
            result.open_answer = measure(OPEN_TASK, result.tier.model, prices)
        except LLMError as exc:
            print(paint(RED, f"  {result.tier.label}: {str(exc)[:100]}"))
            continue
        print(paint(BOLD, f"\n  {result.tier.label}"))
        print(textwrap.fill(result.open_answer.text.strip(), width=WIDTH - 6,
                            initial_indent="    ", subsequent_indent="    "))

    print(paint(DIM, "\n  Отдаю ответы судье — вперемешку и без подписей…"))
    samples = [(r.tier, r.open_answer.text) for r in results if r.open_answer]
    scores = judge(samples)

    # ── сводка ──────────────────────────────────────────────────────────
    section("Сводка")
    head = (f"  {'Ступень':<11} {'Верно':>6} {'1-й токен':>10} {'Ответ':>7} "
            f"{'Токенов':>8} {'Цена':>12} {'Оценка':>7}")
    print(paint(BOLD, head))
    print(paint(DIM, "  " + "─" * (len(head) - 2)))

    for index, result in enumerate(results, 1):
        if not result.shots:
            continue
        hits = sum(s.correct for s in result.shots)
        first = statistics.mean(s.first_token for s in result.shots)
        total = statistics.mean(s.total for s in result.shots)
        tokens = round(statistics.mean(s.completion_tokens for s in result.shots))
        cost = statistics.mean(s.cost for s in result.shots)

        marks = scores.get(index, {})
        grades = [marks.get(k, 0) for k in ("точность", "ясность", "полнота")]
        grade = statistics.mean(grades) if any(grades) else 0

        colour = GREEN if hits == len(result.shots) else (RED if hits == 0 else YELLOW)
        print(f"  {result.tier.label:<11} " + paint(colour, f"{hits}/{len(result.shots):<4}")
              + f" {first:>9.2f}с {total:>6.2f}с {tokens:>8} {money(cost):>12} {grade:>7.1f}")

    print(paint(DIM, "\n  «1-й токен» — сколько ждёт пользователь до начала ответа."))
    print(paint(DIM, "  Цена — за один запрос, по живым тарифам каталога OpenRouter."))
    print(paint(DIM, "  Оценка — среднее по точности, ясности и полноте, судья вслепую."))

    # ── накладные расходы прокси ────────────────────────────────────────
    section("Сколько стоит ходить через прокси")
    probe = "Ответь одним словом: столица Италии?"
    try:
        direct = time.monotonic()
        ask(probe, model="deepseek-v4-flash", provider=DEEPSEEK,
            temperature=0.0, max_tokens=MAX_TOKENS)
        direct = time.monotonic() - direct

        through = time.monotonic()
        ask(probe, model="deepseek/deepseek-v4-flash", provider=OPENROUTER,
            temperature=0.0, max_tokens=MAX_TOKENS)
        through = time.monotonic() - through

        print(f"  напрямую в DeepSeek:      {direct:.2f} с")
        print(f"  через OpenRouter:         {through:.2f} с")
        print(paint(DIM, f"  разница: {through - direct:+.2f} с"))
    except LLMError as exc:
        print(paint(RED, f"  не удалось замерить: {str(exc)[:100]}"))

    print()
    print(paint(BOLD, "Ссылки на модели"))
    for tier in ladder:
        print(f"  {tier.label:<11} {tier.link}")
        print(paint(DIM, f"              {tier.note}"))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
