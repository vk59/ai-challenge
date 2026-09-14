#!/usr/bin/env python3
"""День 8: агент, который считает токены и показывает счёт.

    python3 cli.py
    python3 cli.py --memory 3        узкое окно — видно, как счёт перестаёт расти
    python3 cli.py --no-window       окно снято: счёт растёт квадратично

Команды: /вес, /стат, /цена, /история, /сброс, /выход

Что здесь нового против дня 7. Перед каждым запросом печатается, СКОЛЬКО
он весит и из чего этот вес состоит, — до отправки, пока ещё можно передумать.
После ответа рядом встаёт то, что насчитал API, и видно, насколько оценка
промахнулась.

Смысл в том, что удивляет обычно не размер вопроса. Вопрос — это десяток
токенов. Платите вы за роль и за всю историю, которые уезжают заново
в каждом запросе.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import MemoryError_, open_store  # noqa: E402
from tokens import money  # noqa: E402

TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 90)
DIM, BOLD, CYAN, GREEN, RED, YELLOW = "2", "1", "36", "32", "31", "33"


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


HELP = """  /вес       из чего складывается следующий запрос
  /стат      токены и деньги за весь диалог
  /цена      прогноз: во что обойдётся диалог дальше
  /история   архив с диска
  /сброс     стереть диалог
  /выход     завершить (или Ctrl+D)"""


def bar(share: float, width: int = 24) -> str:
    """Полоска заполнения окна модели."""
    filled = min(width, int(share * width))
    return "█" * filled + "·" * (width - filled)


def show_weight(agent: Agent, message: str = "") -> None:
    """Разбор веса запроса по частям — это главный экран дня."""
    w = agent.weigh(message)
    хвост = "" if message else " (ещё до того, как вы что-то спросили)"
    print(paint(BOLD, "\n  Следующий запрос весит ~%d токенов%s" % (w["total"], хвост)))
    print(paint(DIM, f"    обвязка запроса   {w['overhead']:>7}  служебная разметка"))
    print(paint(DIM, f"    роль              {w['role']:>7}  уезжает в КАЖДОМ запросе"))
    print(paint(DIM, f"    история диалога   {w['history']:>7}  оплачивается заново"))
    print(paint(DIM, f"    сам вопрос        {w['question']:>7}"))
    if w["limit"]:
        процент = w["share"] * 100
        краска = RED if процент > 80 else (YELLOW if процент > 50 else DIM)
        print(paint(краска, f"    окно модели       {bar(w['share'])} "
                            f"{процент:.2f}% от {w['limit']:,}"))
    print(paint(DIM, f"    цена входа        {money(w['cost'])}"))


def show_stats(agent: Agent) -> None:
    s = agent.stats
    print(paint(BOLD, "\n  Счёт за диалог"))
    print(paint(DIM, f"    реплик:            {s.turns}"))
    print(paint(DIM, f"    токенов на вход:   {s.prompt_tokens:>8}  "
                     f"(в среднем {s.average_prompt:.0f} на запрос)"))
    print(paint(DIM, f"    токенов на ответ:  {s.completion_tokens:>8}"))
    print(paint(DIM, f"    всего:             {s.total_tokens:>8}"))
    print(paint(DIM, f"    потрачено:         {agent.spent_pretty}"))
    print(paint(DIM, f"    окно памяти:       {agent.remembers} из {agent.memory_turns} пар"))


def show_forecast(agent: Agent) -> None:
    """Во что обойдутся следующие десять реплик, если ничего не менять."""
    w = agent.weigh("типичный вопрос средней длины про программирование")
    print(paint(BOLD, "\n  Прогноз на 10 реплик вперёд"))
    print(paint(DIM, "    (считаем на типовом вопросе, поэтому чуть больше, чем /вес)"))

    if agent.remembers >= agent.memory_turns:
        print(paint(GREEN, "    Окно памяти заполнено — вес запроса дальше почти не растёт."))
        итого = w["cost"] * 10
        print(paint(DIM, f"    примерно {w['total'] * 10:,} токенов входа, {money(итого)}"))
        print(paint(DIM, "    Это и есть плата за то, что агент перестал помнить начало."))
        return

    # Пока окно не заполнено, каждая реплика утяжеляет следующую
    вес, сумма = w["total"], 0
    прибавка = w["question"] * 2 + 8        # вопрос + ответ + разметка
    for _ in range(10):
        сумма += вес
        вес += прибавка
    print(paint(DIM, f"    сейчас запрос весит {w['total']:,}, через 10 реплик ~{вес:,}"))
    print(paint(YELLOW, f"    суммарно ~{сумма:,} токенов входа — растёт квадратично"))
    print(paint(DIM, f"    Окно памяти ({agent.memory_turns} пар) остановит этот рост."))


def main() -> int:
    args = sys.argv[1:]
    окно = 10
    if "--memory" in args:
        окно = int(args[args.index("--memory") + 1])
    if "--no-window" in args:
        окно = 10_000                      # практически «без подрезки»

    try:
        store = open_store("sqlite")
    except MemoryError_ as exc:
        print(paint(RED, f"  Ошибка хранилища: {exc}"))
        return 1

    agent = Agent(name="Счетовод", store=store,
                  session="токены" if окно <= 100 else "токены-без-окна",
                  memory_turns=окно)

    print(paint(BOLD, f"\n  {agent.name}"))
    print(paint(DIM, f"  {agent.describe()}"))
    print(paint(DIM, f"  потрачено за диалог: {agent.spent_pretty}"))
    print(paint(DIM, "  /помощь — команды, Ctrl+D — выход\n"))

    while True:
        try:
            message = input(paint(CYAN, "вы › ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not message:
            continue

        cmd = message.lower()
        if cmd in ("/выход", "/quit"):
            break
        if cmd in ("/помощь", "/help"):
            print(paint(DIM, HELP)); continue
        if cmd in ("/вес", "/weigh"):
            show_weight(agent); continue
        if cmd in ("/стат", "/stats"):
            show_stats(agent); continue
        if cmd in ("/цена", "/forecast"):
            show_forecast(agent); continue
        if cmd in ("/история", "/history"):
            for t in agent.transcript():
                who = "вы" if t.role == "user" else agent.name.lower()
                print(paint(DIM, f"    {t.when} {who}: {t.content[:60]}"))
            continue
        if cmd in ("/сброс", "/reset"):
            agent.reset()
            print(paint(GREEN, "  Диалог стёрт.")); continue

        # ── вот он, подсчёт ДО отправки ──
        оценка = agent.weigh(message)
        print(paint(DIM, f"  → уедет ~{оценка['total']:,} токенов "
                         f"(роль {оценка['role']}, история {оценка['history']}, "
                         f"вопрос {оценка['question']}) · {money(оценка['cost'])}"))

        print(paint(GREEN, f"{agent.name.lower()} › "), end="", flush=True)
        hint = "думает…"
        erase = "\b" * len(hint) + " " * len(hint) + "\b" * len(hint)
        if TTY:
            print(paint(DIM, hint), end="", flush=True)
        waiting = TTY

        def stop_waiting() -> None:
            nonlocal waiting
            if waiting:
                print(erase, end="", flush=True)
                waiting = False

        try:
            for piece in agent.stream(message):
                stop_waiting()
                print(piece, end="", flush=True)
        except LLMError as exc:
            stop_waiting()
            print(paint(RED, f"\n  Ошибка: {exc}")); continue
        except KeyboardInterrupt:
            stop_waiting()
            print(paint(DIM, "\n  прервано")); continue
        stop_waiting()

        if not agent.journal:
            print(paint(DIM, "\n  Пустой ответ.\n")); continue

        # ── а вот точные числа ПОСЛЕ ответа ──
        last = agent.journal[-1]
        промах = last.prompt_tokens - оценка["total"]
        доля = (abs(промах) / last.prompt_tokens * 100) if last.prompt_tokens else 0
        знак = "+" if промах > 0 else ""
        print(paint(DIM, f"\n  вход {last.prompt_tokens:,} (оценка промахнулась на "
                         f"{знак}{промах}, {доля:.1f}%) · "
                         f"ответ {last.completion_tokens} · "
                         f"всего за диалог {agent.stats.total_tokens:,} · "
                         f"{agent.spent_pretty}\n"))

    print(paint(DIM, f"  Итого: {agent.stats.total_tokens:,} токенов, "
                     f"{agent.spent_pretty}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
