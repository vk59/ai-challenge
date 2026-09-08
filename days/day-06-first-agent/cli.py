#!/usr/bin/env python3
"""День 6: чат с агентом в терминале.

    python3 cli.py
    python3 cli.py --role "Ты ворчливый сисадмин, отвечаешь односложно"

Команды в чате: /стат, /сброс, /роль, /журнал, /выход

Обрати внимание, чего здесь НЕТ: ни urllib, ни json, ни списка messages,
ни слова про API. Этот файл знает только четыре метода агента. Вся работа
с моделью заперта в shared/agent.py — в этом и есть задание дня.
"""

import shutil
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402

TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 90)
DIM, BOLD, CYAN, GREEN, RED = "2", "1", "36", "32", "31"


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


HELP = """  /стат     сколько потрачено за сессию
  /сброс    забыть диалог, начать заново
  /роль     показать роль агента
  /журнал   все обращения к модели
  /выход    завершить (или Ctrl+D)"""


def show_stats(agent: Agent) -> None:
    stats = agent.stats
    print(paint(BOLD, "\n  Расход за сессию"))
    print(paint(DIM, f"    реплик:          {stats.turns}"))
    print(paint(DIM, f"    токенов на вход: {stats.prompt_tokens}"))
    print(paint(DIM, f"    токенов на ответ:{stats.completion_tokens}"))
    print(paint(DIM, f"    всего токенов:   {stats.total_tokens}"))
    print(paint(DIM, f"    времени:         {stats.seconds} с"))
    print(paint(DIM, f"    в памяти:        {agent.remembers} из {agent.memory_turns} пар"))


def show_journal(agent: Agent) -> None:
    if not agent.journal:
        print(paint(DIM, "\n  Журнал пуст."))
        return
    print(paint(BOLD, "\n  Журнал обращений"))
    for index, call in enumerate(agent.journal, 1):
        print(f"    {index}. {call.short}")
        print(paint(DIM, f"       {call.prompt_tokens}→{call.completion_tokens} токенов, "
                         f"{call.seconds} с"))


def main() -> int:
    args = sys.argv[1:]
    role = None
    if "--role" in args:
        role = args[args.index("--role") + 1]

    agent = Agent(name="Помощник", **({"role": role} if role else {}))

    print(paint(BOLD, f"\n  {agent.name}"))
    print(paint(DIM, f"  {agent.describe()}"))
    print(paint(DIM, "  /помощь — список команд, Ctrl+D — выход\n"))

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
        if command in ("/сброс", "/reset"):
            agent.reset()
            print(paint(GREEN, "  Диалог забыт, начинаем заново."))
            continue
        if command in ("/роль", "/role"):
            print(paint(DIM, textwrap.fill(agent.role, width=WIDTH - 4,
                                           initial_indent="  ", subsequent_indent="  ")))
            continue

        print(paint(GREEN, f"{agent.name.lower()} › "), end="", flush=True)
        try:
            for piece in agent.stream(message):
                print(piece, end="", flush=True)
        except LLMError as exc:
            print(paint(RED, f"\n  Ошибка: {exc}"))
            continue
        except KeyboardInterrupt:
            print(paint(DIM, "\n  прервано"))
            continue

        last = agent.journal[-1]
        print(paint(DIM, f"\n  {last.completion_tokens} токенов · {last.seconds} с · "
                         f"в памяти {agent.remembers}\n"))

    print(paint(DIM, f"  Итого: {agent.stats.turns} реплик, "
                     f"{agent.stats.total_tokens} токенов, {agent.stats.seconds} с"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
