#!/usr/bin/env python3
"""День 17: агент с инструментами MCP — в терминале.

    python3 ask.py "Что я делал в девятый день?"
    python3 ask.py                      интерактивный режим
    python3 ask.py --tools              только показать инструменты

Агент подключается к MCP-серверу из server.py, получает список инструментов,
сам решает, какой вызвать, и отвечает по результату. Каждый вызов виден
строкой 🔧 — это не подставлено вручную, а взято из журнала.
"""

import shutil
import sys
from pathlib import Path

ЗДЕСЬ = Path(__file__).resolve().parent
sys.path.insert(0, str(ЗДЕСЬ.parents[1] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import MemoryError_, open_store  # noqa: E402
from tools import MCPToolset  # noqa: E402

TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 90)
DIM, BOLD, CYAN, GREEN, RED, YELLOW, MAG = "2", "1", "36", "32", "31", "33", "35"

СЕРВЕРЫ = {"git": [sys.executable, str(ЗДЕСЬ / "server.py")]}

РОЛЬ = ("Ты — летописец проекта AI Advent. У тебя есть инструменты, которые "
        "читают историю репозитория. Опирайся на них, а не на догадки: если "
        "спрашивают про конкретный день или файл — сначала посмотри. "
        "Отвечай кратко, по делу, без воды.")


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def показать_вызовы(agent: Agent) -> None:
    for з in agent.tool_log:
        краска = RED if з.failed else MAG
        метка = " [ошибка]" if з.failed else ""
        print(paint(краска, f"  🔧 {з.short}{метка}"))
        первая = (з.result or "").strip().splitlines()
        if первая:
            print(paint(DIM, f"     → {первая[0][:76]}"))
            if len(первая) > 1:
                print(paint(DIM, f"       ещё {len(первая) - 1} строк, "
                                 f"{len(з.result)} знаков, {з.seconds} с"))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    только_список = "--tools" in sys.argv

    try:
        хранилище = open_store("sqlite")
    except MemoryError_ as exc:
        print(paint(RED, f"  Ошибка хранилища: {exc}"))
        return 1

    with MCPToolset(СЕРВЕРЫ) as набор:
        print(paint(BOLD, "\n  Летописец проекта"))
        print(paint(DIM, f"  MCP: {набор.describe()}"))

        if только_список:
            print()
            for схема in набор.schemas():
                ф = схема["function"]
                print(f"    {paint(BOLD, ф['name'])}")
                print(paint(DIM, f"      {' '.join(ф['description'].split())[:76]}"))
                свойства = (ф.get("parameters") or {}).get("properties") or {}
                обяз = set((ф.get("parameters") or {}).get("required") or [])
                for имя, о in свойства.items():
                    метка = "обязательный" if имя in обяз else "необязательный"
                    print(paint(DIM, f"      · {имя}: {о.get('type','?')} ({метка})"))
                if not свойства:
                    print(paint(DIM, "      · без параметров"))
                print()
            return 0

        agent = Agent(name="Летописец", role=РОЛЬ, store=хранилище,
                      session="день17", temperature=0.3, max_tokens=1500)
        agent.tools = набор
        print(paint(DIM, "  Спрашивайте про дни, файлы и историю. "
                         "Ctrl+D — выход\n"))

        вопросы = [" ".join(args)] if args else None
        while True:
            if вопросы is not None:
                if not вопросы:
                    break
                вопрос = вопросы.pop(0)
                print(paint(CYAN, f"вы › {вопрос}"))
            else:
                try:
                    вопрос = input(paint(CYAN, "вы › ")).strip()
                except (EOFError, KeyboardInterrupt):
                    print(); break
                if not вопрос:
                    continue
                if вопрос.lower() in ("/выход", "/quit"):
                    break

            try:
                ответ = agent.ask(вопрос)
            except LLMError as exc:
                print(paint(RED, f"  Ошибка: {exc}\n")); continue

            # Сначала показываем, что дёргали, потом ответ — так видно,
            # что ответ опирается на данные, а не сочинён.
            показать_вызовы(agent)
            print(paint(GREEN, f"\n{agent.name.lower()} › ") + ответ.strip())
            последний = agent.journal[-1] if agent.journal else None
            if последний:
                print(paint(DIM, f"\n  вызовов: {len(agent.tool_log)} · "
                                 f"{последний.prompt_tokens}→"
                                 f"{последний.completion_tokens} токенов · "
                                 f"{agent.spent_pretty}\n"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
