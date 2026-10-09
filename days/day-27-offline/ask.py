#!/usr/bin/env python3
"""Офлайн-помощник по документации проекта.

    ./ask.py "почему сжатие бывает дороже"      разовый вопрос
    ./ask.py                                     диалог
    ./ask.py --check                             проверить, что всё готово
    ./ask.py --cloud "..."                       то же через облако, для сравнения

Ни один запрос не уходит из этой машины: и модель, и эмбеддинги работают
через Ollama. Под каждым ответом — источники; если в документах ответа
нет, утилита говорит «не знаю» и просит уточнить.

Команды в диалоге: /задача, /история, /забыть, /источники, /выход.
"""

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

HELP = "команды: /задача  /история  /забыть  /источники  /выход"


def ready(backend: str) -> tuple[bool, list[str]]:
    """Всё ли на месте: Ollama, модели, индекс."""
    import json
    import urllib.error
    import urllib.request

    import embeddings
    from index import база_для
    from llm import DEFAULT_LOCAL_MODEL

    troubles: list[str] = []
    models: set[str] = set()

    if backend == "local":
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:11434/api/tags", timeout=4) as answer:
                for item in json.load(answer).get("models") or []:
                    models.add(item.get("name", "").split(":")[0])
                    models.add(item.get("name", ""))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            troubles.append("Ollama не отвечает → ollama serve")

        if models:
            for needed in (DEFAULT_LOCAL_MODEL, embeddings.LOCAL_МОДЕЛЬ):
                if needed not in models and needed.split(":")[0] not in models:
                    troubles.append(f"нет модели {needed} → ollama pull {needed}")

    path = база_для(backend)
    if not path.exists():
        troubles.append(
            f"нет индекса {path.name} → AI_ADVENT_EMBEDDINGS={backend} "
            f"python3 ../day-21-indexing/build.py")
    return not troubles, troubles


def show_reply(reply, with_sources: bool = True) -> None:
    if reply.rewritten:
        print(f"  (искал: {reply.query})")
    print(f"\n{reply.answer.strip()}\n")

    if not with_sources:
        return
    if reply.sources:
        print("  источники:")
        for source in reply.sources:
            print(f"    {source['chunk_id']}")
    elif reply.abstained:
        print("  (ответа в документах нет — модель даже не вызывалась)")
    else:
        print("  (источников нет)")

    quotes = reply.quotes
    if quotes:
        good = sum(1 for q in quotes if q["verified"])
        bad = len(quotes) - good
        line = f"  цитат: {good} подтверждено дословно"
        if bad:
            line += f", {bad} ВЫДУМАНО"
        print(line)
    print(f"  {reply.total_tokens} токенов · {reply.seconds_total:.1f}с")


def main() -> None:
    argv = sys.argv[1:]
    cloud = "--cloud" in argv
    os.environ["AI_ADVENT_EMBEDDINGS"] = "cloud" if cloud else "local"
    backend = "cloud" if cloud else "local"

    import embeddings  # noqa: F401  (порядок важен: после переменной)
    from chat import ChatSession
    from cite import порог_отказа
    from index import Index, база_для
    from llm import DEFAULT_LOCAL_MODEL, LOCAL, LLMError

    if "--check" in argv:
        ok, troubles = ready(backend)
        print(f"Бэкенд: {backend}")
        if ok:
            index = Index()
            stats = index.stats("structural")
            print(f"  ✓ индекс {база_для(backend).name}: "
                  f"{stats['chunks']} кусков из {stats['documents']} документов")
            if backend == "local":
                print(f"  ✓ Ollama отвечает")
                print(f"  ✓ модели: {DEFAULT_LOCAL_MODEL}, "
                      f"{embeddings.LOCAL_МОДЕЛЬ}")
            print(f"  порог отказа: {порог_отказа(backend)}")
            print("\nГотово к работе.")
            sys.exit(0)
        for trouble in troubles:
            print(f"  ✗ {trouble}")
        sys.exit(1)

    ok, troubles = ready(backend)
    if not ok:
        print("Не готово:")
        for trouble in troubles:
            print(f"  {trouble}")
        sys.exit(1)

    words = [a for a in argv if not a.startswith("--")]
    session = ChatSession(
        f"ask-{backend}", index=Index(),
        provider=None if cloud else LOCAL,
        model=None if cloud else DEFAULT_LOCAL_MODEL,
        rerank=False)

    def answer(message: str):
        started = time.monotonic()
        reply = session.ask(message)
        reply.seconds_total = time.monotonic() - started
        return reply

    # Разовый вопрос: спросил и вышел, удобно для скриптов.
    if words:
        try:
            show_reply(answer(" ".join(words)))
        except LLMError as failure:
            print(f"сбой: {failure}")
            sys.exit(1)
        return

    stats = Index().stats("structural")
    where = "облако" if cloud else f"локально · {DEFAULT_LOCAL_MODEL}"
    print(f"Офлайн-помощник · {where} · индекс {stats['chunks']} кусков")
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
            task = session.task
            print(f"  цель: {task.goal or '(не определена)'}")
            for title, items in (("уточнено", task.clarified),
                                 ("зафиксировано", task.constraints)):
                if items:
                    print(f"  {title}:")
                    for item in items:
                        print(f"    — {item}")
            continue
        if message == "/забыть":
            session.forget_task()
            print("  память задачи очищена")
            continue
        if message == "/история":
            for turn in session.transcript():
                who = "вы" if turn.role == "user" else "он"
                print(f"  {who:3} {' '.join(turn.content.split())[:104]}")
            continue
        if message == "/источники":
            print(f"  индекс: {база_для(backend)}")
            for source in Index().sources("structural")[:12]:
                print(f"    {source}")
            continue
        if message.startswith("/"):
            print(f"  {HELP}")
            continue

        print("  думаю…", end="\r", flush=True)
        try:
            reply = answer(message)
        except LLMError as failure:
            print(f"  сбой: {failure}      ")
            continue
        print(" " * 20, end="\r")
        show_reply(reply)


if __name__ == "__main__":
    main()
