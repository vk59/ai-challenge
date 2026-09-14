#!/usr/bin/env python3
"""День 7: агент, который помнит диалог между запусками.

    python3 cli.py                          продолжить диалог «default»
    python3 cli.py --session работа         другой диалог
    python3 cli.py --store json             хранить в JSON, а не в SQLite
    python3 cli.py --dir /tmp/память        другой каталог хранилища

Команды в чате: /стат, /сброс, /роль, /журнал, /история, /сессии, /выход

Проверка задания занимает тридцать секунд: скажите агенту своё имя, выйдите
по Ctrl+D, запустите файл заново и спросите, как вас зовут. Диалог начнётся
не с нуля — сверху терминала будет напечатан восстановленный хвост разговора.

Как и в дне 6, здесь нет ни urllib, ни списка messages, ни слова про API.
Появился ровно один новый импорт — хранилище, и то лишь затем, чтобы выбрать
между JSON и SQLite и отдать выбранное агенту.
"""

import shutil
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import MemoryError_, open_store  # noqa: E402

TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 90)
DIM, BOLD, CYAN, GREEN, RED, YELLOW = "2", "1", "36", "32", "31", "33"

# Сколько последних пар печатать при старте, чтобы разговор выглядел
# непрерывным. Весь архив вываливать незачем — на то есть /история.
REPLAY_PAIRS = 3


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


HELP = """  /стат      сколько потрачено за всё время диалога
  /история   весь архив с диска, включая забытое окном
  /сессии    список сохранённых диалогов
  /сброс     стереть этот диалог — и в памяти, и на диске
  /роль      показать роль агента
  /журнал    обращения к модели, которые агент сейчас помнит
  /выход     завершить (или Ctrl+D)"""


def wrap(text: str, indent: str = "    ") -> str:
    return textwrap.fill(text.replace("\n", " "), width=WIDTH - len(indent),
                         initial_indent=indent, subsequent_indent=indent)


def show_stats(agent: Agent) -> None:
    stats = agent.stats
    print(paint(BOLD, "\n  Расход по диалогу"))
    print(paint(DIM, f"    диалог:          {agent.session}"))
    print(paint(DIM, f"    хранилище:       {agent.store}"))
    print(paint(DIM, f"    реплик:          {stats.turns}"))
    print(paint(DIM, f"    токенов на вход: {stats.prompt_tokens}"))
    print(paint(DIM, f"    токенов на ответ:{stats.completion_tokens}"))
    print(paint(DIM, f"    всего токенов:   {stats.total_tokens}"))
    print(paint(DIM, f"    времени:         {stats.seconds} с"))
    print(paint(DIM, f"    в архиве:        {agent.archived} пар"))
    print(paint(DIM, f"    уедет в модель:  {agent.remembers} из {agent.memory_turns} пар"))


def show_journal(agent: Agent) -> None:
    if not agent.journal:
        print(paint(DIM, "\n  Журнал пуст."))
        return
    print(paint(BOLD, "\n  Обращения к модели (то, что сейчас в окне контекста)"))
    for index, call in enumerate(agent.journal, 1):
        print(f"    {index}. {call.short}")
        print(paint(DIM, f"       {call.prompt_tokens}→{call.completion_tokens} токенов, "
                         f"{call.seconds} с"))


def show_history(agent: Agent) -> None:
    """Весь архив — в том числе то, что окно контекста уже забыло."""
    turns = agent.transcript()
    if not turns:
        print(paint(DIM, "\n  Архив пуст — это первый запуск с этим диалогом."))
        return

    window = agent.remembers * 2
    print(paint(BOLD, f"\n  Архив диалога «{agent.session}» — {len(turns) // 2} пар"))
    for index, turn in enumerate(turns):
        who = "вы" if turn.role == "user" else agent.name.lower()
        # Метка у реплик, которые на диске есть, а в запрос к модели не попадут.
        forgotten = index < len(turns) - window
        mark = paint(YELLOW, " ⟨вне окна⟩") if forgotten else ""
        print(paint(DIM, f"    {turn.when}  ") + paint(BOLD, who) + mark)
        print(paint(DIM, wrap(turn.content[:220] + ("…" if len(turn.content) > 220 else ""))))
    print(paint(DIM, f"\n  В модель уедут последние {agent.remembers} пар из "
                     f"{len(turns) // 2}. Остальное лежит на диске, но не в контексте."))


def show_sessions(agent: Agent) -> None:
    found = agent.store.sessions()
    if not found:
        print(paint(DIM, "\n  Сохранённых диалогов пока нет."))
        return
    print(paint(BOLD, "\n  Сохранённые диалоги"))
    for info in found:
        here = " ← сейчас здесь" if info.name == agent.session else ""
        print(f"    {info.name}{paint(GREEN, here)}")
        print(paint(DIM, f"       {info.pairs} пар · {info.tokens} токенов · "
                         f"обновлён {info.when}"))
    print(paint(DIM, "\n  Открыть другой:  python3 cli.py --session <имя>"))


def replay(agent: Agent) -> None:
    """Печатает хвост прошлого разговора, чтобы он выглядел непрерывным."""
    turns = agent.transcript()
    if not turns:
        return

    tail = turns[-REPLAY_PAIRS * 2:]
    hidden = (len(turns) - len(tail)) // 2
    print(paint(BOLD, f"\n  ┌─ продолжаем диалог «{agent.session}»"))
    if hidden:
        print(paint(DIM, f"  │  …ещё {hidden} пар выше, целиком — /история"))
    for turn in tail:
        who = "вы › " if turn.role == "user" else f"{agent.name.lower()} › "
        colour = CYAN if turn.role == "user" else GREEN
        body = turn.content.replace("\n", " ")
        if len(body) > WIDTH - 12:
            body = body[:WIDTH - 13] + "…"
        print(paint(DIM, "  │  ") + paint(colour, who) + paint(DIM, body))
    print(paint(BOLD, "  └─"))


def parse_args(args: list[str]) -> dict:
    """Разбор флагов руками: ради четырёх опций argparse не нужен."""
    options = {"role": None, "session": "default", "store": "sqlite", "dir": None}
    for flag in ("--role", "--session", "--store", "--dir"):
        if flag in args:
            index = args.index(flag)
            if index + 1 >= len(args):
                raise SystemExit(f"У флага {flag} не хватает значения")
            options[flag[2:]] = args[index + 1]
    return options


def main() -> int:
    options = parse_args(sys.argv[1:])

    try:
        store = open_store(options["store"], options["dir"])
    except MemoryError_ as exc:
        print(paint(RED, f"  Ошибка хранилища: {exc}"))
        return 1

    agent = Agent(
        name="Помощник",
        store=store,
        session=options["session"],
        **({"role": options["role"]} if options["role"] else {}),
    )

    print(paint(BOLD, f"\n  {agent.name}"))
    print(paint(DIM, f"  {agent.describe()}"))
    print(paint(DIM, "  /помощь — список команд, Ctrl+D — выход"))
    replay(agent)
    print()

    while True:
        try:
            message = input(paint(CYAN, "вы › ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not message:
            continue

        command = message.lower()
        if command in ("/выход", "/quit", "/exit"):
            break
        if command in ("/помощь", "/help"):
            print(paint(DIM, HELP))
            continue
        if command in ("/стат", "/stats"):
            show_stats(agent)
            continue
        if command in ("/журнал", "/log"):
            show_journal(agent)
            continue
        if command in ("/история", "/history"):
            show_history(agent)
            continue
        if command in ("/сессии", "/sessions"):
            show_sessions(agent)
            continue
        if command in ("/сброс", "/reset"):
            agent.reset()
            print(paint(GREEN, f"  Диалог «{agent.session}» стёрт — и в памяти, "
                               f"и на диске."))
            continue
        if command in ("/роль", "/role"):
            print(paint(DIM, wrap(agent.role, "  ")))
            continue

        print(paint(GREEN, f"{agent.name.lower()} › "), end="", flush=True)
        try:
            for piece in agent.stream(message):
                print(piece, end="", flush=True)
        except LLMError as exc:
            print(paint(RED, f"\n  Ошибка: {exc}"))
            continue
        except MemoryError_ as exc:
            print(paint(RED, f"\n  Ответ получен, но не сохранён: {exc}"))
            continue
        except KeyboardInterrupt:
            # Оборванный ответ агент всё равно записал — тем, что успело прийти.
            print(paint(DIM, "\n  прервано (сохранено то, что успело прийти)"))
            continue

        last = agent.journal[-1]
        print(paint(DIM, f"\n  {last.completion_tokens} токенов · {last.seconds} с · "
                         f"в окне {agent.remembers}, в архиве {agent.archived}\n"))

    print(paint(DIM, f"  Диалог «{agent.session}» сохранён: {agent.archived} пар, "
                     f"{agent.stats.total_tokens} токенов."))
    print(paint(DIM, "  Запустите файл снова — разговор продолжится с этого места."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
