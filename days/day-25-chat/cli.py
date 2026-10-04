#!/usr/bin/env python3
"""День 25: мини-чат с RAG в терминале.

    python3 cli.py                      # диалог в сессии default
    python3 cli.py разбор-токенов       # своя сессия
    python3 cli.py --no-task            # без памяти задачи

Команды внутри: /задача — показать память задачи, /история — реплики,
/забыть — очистить память задачи, /чаты — список, /выход.

Источники печатаются под каждым ответом. Если близость ниже порога,
ассистент говорит «не знаю» и просит уточнить — до обращения к модели,
то есть бесплатно.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from chat import ChatSession  # noqa: E402
from index import Index  # noqa: E402
from llm import LLMError  # noqa: E402

HELP = ("команды: /задача  /история  /забыть  /чаты  /выход")


def show_task(session: ChatSession) -> None:
    task = session.task
    if task.is_empty:
        print("  память задачи пуста")
        return
    print(f"  цель: {task.goal or '(не определена)'}")
    if task.clarified:
        print("  уточнено:")
        for item in task.clarified:
            print(f"    — {item}")
    if task.constraints:
        print("  зафиксировано:")
        for item in task.constraints:
            print(f"    — {item}")
    print(f"  реплик учтено: {task.turns}")


def main() -> None:
    argv = sys.argv[1:]
    track = "--no-task" not in argv
    names = [a for a in argv if not a.startswith("--")]
    session_name = names[0] if names else "default"

    index = Index()
    stats = index.stats("structural")
    if not stats["chunks"]:
        print("Индекс пуст. Соберите: cd ../day-21-indexing && python3 build.py")
        sys.exit(1)

    session = ChatSession(session_name, index=index, track_task=track)
    print(f"Чат «{session.session}» · индекс {stats['chunks']} кусков"
          f"{'' if track else ' · память задачи выключена'}")
    if session.task.goal:
        print(f"Цель из прошлого раза: {session.task.goal}")
    print(HELP)

    while True:
        try:
            message = input("\nвы › ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nпока")
            return
        if not message:
            continue
        if message in ("/выход", "/exit", "/quit"):
            print("пока")
            return
        if message == "/задача":
            show_task(session)
            continue
        if message == "/забыть":
            session.forget_task()
            print("  память задачи очищена")
            continue
        if message == "/история":
            for turn in session.transcript():
                who = "вы" if turn.role == "user" else "ассистент"
                print(f"  {who:9} {' '.join(turn.content.split())[:110]}")
            continue
        if message == "/чаты":
            for row in session.chats():
                mark = "→" if row["active"] else " "
                print(f"  {mark} {row['name']:22} {row['pairs']:3} пар  "
                      f"{row['goal'][:44]}")
            continue
        if message.startswith("/"):
            print(f"  {HELP}")
            continue

        print("  думаю…", end="\r", flush=True)
        try:
            reply = session.ask(message)
        except LLMError as failure:
            print(f"  сбой: {failure}        ")
            continue
        print(" " * 20, end="\r")

        if reply.rewritten:
            print(f"  (искал: {reply.query})")
        print(f"\nассистент › {reply.answer.strip()}")

        if reply.sources:
            print("\n  источники:")
            for source in reply.sources:
                print(f"    [{source['n']}] {source['chunk_id']}")
        elif reply.abstained:
            print("\n  (отказ по порогу — модель не вызывалась)")
        else:
            print("\n  (источников нет)")

        if reply.quotes:
            good = sum(1 for q in reply.quotes if q["verified"])
            bad = len(reply.quotes) - good
            print(f"  цитат: {good} подтверждено"
                  + (f", {bad} ВЫДУМАНО" if bad else ""))

        if reply.task_changed and reply.task.goal:
            print(f"  цель: {reply.task.goal}")
        print(f"  {reply.total_tokens} токенов")


if __name__ == "__main__":
    main()
