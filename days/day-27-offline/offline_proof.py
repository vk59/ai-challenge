#!/usr/bin/env python3
"""День 27: доказательство, что приложение не ходит в сеть.

    python3 offline_proof.py            # полный диалог при закрытой сети
    python3 offline_proof.py --cloud    # то же с облаком — должно упасть

Утверждать «работает без облачных моделей» на словах нельзя: в дне 26
окно считалось локальным, а на каждую реплику уходил запрос в OpenRouter
за вектором вопроса. Поэтому здесь сеть не просто не используется — она
физически закрыта.

Как закрыта: подменяется `socket.socket.connect`. Соединения на 127.0.0.1
и ::1 проходят, любое другое поднимает исключение с адресом, куда
пытались пойти. Это надёжнее, чем доверять коду: если хоть один запрос
уйдёт наружу, проверка не промолчит, а назовёт виновника.
"""

import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

LOCALHOST = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


class WentOutside(RuntimeError):
    """Код попытался выйти в сеть, хотя обещал этого не делать."""


class Airgap:
    """Контекст, в котором наружу выйти нельзя."""

    def __init__(self):
        self.attempts: list[str] = []
        self._real = socket.socket.connect

    def __enter__(self) -> "Airgap":
        attempts = self.attempts
        real = self._real

        def guarded(sock, address, *rest):
            host = address[0] if isinstance(address, tuple) else str(address)
            if host not in LOCALHOST:
                attempts.append(host)
                raise WentOutside(f"попытка соединения с {host}")
            return real(sock, address, *rest)

        socket.socket.connect = guarded
        return self

    def __exit__(self, *_) -> None:
        socket.socket.connect = self._real


def run_dialogue(label: str) -> bool:
    from chat import ChatSession, TaskStore
    from index import Index
    from llm import LOCAL
    from memory import open_store

    name = "офлайн-доказательство"
    store = open_store("sqlite")
    store.clear(name)
    TaskStore().drop(name)
    session = ChatSession(name, store=store, index=Index(), provider=LOCAL,
                          model="qwen2.5:3b", rerank=False)

    dialogue = [
        "Какая стратегия нарезки документов победила в сравнении?",
        "А на сколько именно?",
        "Какая столица Австралии?",
    ]
    ok = True
    for number, message in enumerate(dialogue, 1):
        reply = session.ask(message)
        marks = []
        marks.append("источников %d" % len(reply.sources))
        if reply.rewritten:
            marks.append("раскрыт")
        if reply.abstained:
            marks.append("ОТКАЗ по порогу")
        elif not reply.cited.confident:
            marks.append("модель отказалась")
        print(f"  {number}. {message[:46]:48} {', '.join(marks)}")
        if reply.rewritten:
            print(f"     искал: {reply.query[:80]}")
        print(f"     {' '.join(reply.answer.split())[:110]}")
        # Третий вопрос не по теме — отказ там правильный.
        expected_refusal = number == 3
        got_refusal = reply.abstained or not reply.cited.confident
        if expected_refusal != got_refusal:
            ok = False
    print(f"\n  цель диалога: {session.task.goal}")
    return ok


def main() -> None:
    import os

    cloud = "--cloud" in sys.argv
    os.environ["AI_ADVENT_EMBEDDINGS"] = "cloud" if cloud else "local"
    import importlib

    import cite
    import embeddings
    import index
    for module in (embeddings, index, cite):
        importlib.reload(module)
    import chat
    importlib.reload(chat)

    from index import база_для
    path = база_для()
    if not path.exists():
        print(f"Индекса нет: {path.name}")
        print(f"Соберите: AI_ADVENT_EMBEDDINGS={'cloud' if cloud else 'local'} "
              f"python3 ../day-21-indexing/build.py")
        sys.exit(1)

    print(f"Эмбеддинги: {embeddings.модель_бэкенда()} "
          f"({embeddings.размерность_бэкенда()} измерений)")
    print(f"Модель ответа: qwen2.5:3b через Ollama")
    print(f"Индекс: {path.name} · порог отказа {cite.порог_отказа()}")
    print(f"\nЗакрываю сеть: наружу нельзя, 127.0.0.1 можно.\n")

    airgap = Airgap()
    try:
        with airgap:
            good = run_dialogue("офлайн")
    except WentOutside as failure:
        print(f"\n  ✗ {failure}")
        print(f"\n{'═' * 70}")
        print("ПРОВАЛ: код вышел в сеть.")
        for host in dict.fromkeys(airgap.attempts):
            print(f"  пытался: {host}")
        sys.exit(1)
    except Exception as failure:                          # noqa: BLE001
        print(f"\n  сбой: {type(failure).__name__}: {failure}")
        sys.exit(1)

    print(f"\n{'═' * 70}")
    if airgap.attempts:
        print("ПРОВАЛ: были попытки выйти наружу:",
              ", ".join(dict.fromkeys(airgap.attempts)))
        sys.exit(1)
    print("Сеть была закрыта весь диалог. Попыток выйти наружу: 0.")
    print("Три реплики, поиск по индексу, ответ со ссылками, отказ "
          "на чужой вопрос —")
    print("всё посчитано на этой машине.")
    sys.exit(0 if good else 1)


if __name__ == "__main__":
    main()
