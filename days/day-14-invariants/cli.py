#!/usr/bin/env python3
"""День 14: инварианты — ограничения, которые нельзя нарушать.

    python3 cli.py
    python3 cli.py --no-audit        не проверять ответы

Команды:
    /инварианты                 список ограничений
    /добавить <текст>           завести ограничение
    /почему <N> <причина>       дописать причину к ограничению N
    /выкл <N>   /вкл <N>        отключить или включить
    /удалить <N>                убрать совсем
    /промпт                     что именно уезжает в модель
    /выход

Проверяется конфликтом: заведите ограничение «не предлагать MongoDB»,
попросите перейти на MongoDB — и посмотрите, откажется ли агент и как
он это объяснит. Потом отключите ограничение и спросите то же самое.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import (ARCH, INVARIANT_SCOPES, RULE, STACK, SCOPE_LABELS,  # noqa: E402
                    MemoryError_, open_store)

TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 90)
DIM, BOLD, CYAN, GREEN, RED, YELLOW, MAG = "2", "1", "36", "32", "31", "33", "35"

# Чтобы не заставлять человека придумывать ограничения с нуля на видео.
ПРИМЕРЫ = [
    ("Только PostgreSQL 16. NoSQL-хранилища не предлагать.",
     "вся аналитика построена на SQL и оконных функциях", STACK),
    ("Архитектура монолитная. Микросервисы не вводить.",
     "команда из двух человек, эксплуатировать некому", ARCH),
    ("Персональные данные не покидают контур РФ.",
     "требование 152-ФЗ", RULE),
]


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def show_invariants(agent: Agent) -> None:
    if not agent.invariants:
        print(paint(DIM, "\n  Ограничений нет."))
        print(paint(DIM, "  Завести: /добавить Только PostgreSQL 16"))
        print(paint(DIM, f"  Или взять примеры: /примеры"))
        return
    print(paint(BOLD, f"\n  Ограничения проекта — {len(agent.invariants)}"))
    for i in agent.invariants:
        значок = paint(GREEN, "●") if i.active else paint(DIM, "○")
        подпись = paint(DIM, f"[{i.scope_label}]")
        состояние = "" if i.active else paint(DIM, "  (отключено)")
        print(f"    {значок} {i.id}. {подпись} {i.text}{состояние}")
        if i.rationale:
            print(paint(DIM, f"         причина: {i.rationale}"))
    живых = len([i for i in agent.invariants if i.active])
    print(paint(DIM, f"\n    Активных {живых} — они уезжают в каждый запрос."))


def show_audit(итог: dict) -> None:
    if not итог or not итог.get("checked"):
        return
    if итог.get("ok"):
        print(paint(GREEN, f"  ✓ проверено ограничений: {итог['checked']} — "
                           f"нарушений нет"))
        return
    for n in итог.get("violations", []):
        print(paint(RED, f"  ⚠ НАРУШЕНО ограничение {n['id']}: {n['text']}"))
        if n.get("quote"):
            print(paint(RED, f"     цитата: «{n['quote']}»"))
        if n.get("why"):
            print(paint(DIM, f"     {n['why']}"))


def main() -> int:
    args = sys.argv[1:]
    try:
        store = open_store("sqlite")
    except MemoryError_ as exc:
        print(paint(RED, f"  Ошибка хранилища: {exc}"))
        return 1

    agent = Agent(name="Архитектор", store=store, session="инварианты",
                  memory_turns=6)
    agent.auditing = "--no-audit" not in args

    print(paint(BOLD, f"\n  {agent.name}"))
    живых = len([i for i in agent.invariants if i.active])
    if живых:
        print(paint(MAG, f"  Действует ограничений: {живых}"))
    else:
        print(paint(DIM, "  Ограничений пока нет: /примеры или /добавить"))
    print(paint(DIM, f"  Проверка ответов: {'включена' if agent.auditing else 'выключена'}"))
    print(paint(DIM, "  /помощь — команды, Ctrl+D — выход\n"))

    while True:
        try:
            msg = input(paint(CYAN, "вы › ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not msg:
            continue

        части = msg.split(maxsplit=1)
        cmd = части[0].lower()
        хвост = части[1].strip() if len(части) > 1 else ""

        if cmd in ("/выход", "/quit"):
            break
        if cmd in ("/помощь", "/help"):
            print(paint(DIM, __doc__.split("Команды:")[1].split("Проверяется")[0]))
            continue
        if cmd in ("/инварианты", "/inv"):
            show_invariants(agent); continue
        if cmd == "/примеры":
            for текст, причина, область in ПРИМЕРЫ:
                agent.add_invariant(текст, rationale=причина, scope=область)
            print(paint(GREEN, f"  Заведено {len(ПРИМЕРЫ)} ограничения."))
            show_invariants(agent); continue
        if cmd in ("/добавить", "/add"):
            if not хвост:
                print(paint(RED, "  Нужен текст: /добавить Только PostgreSQL")); continue
            inv = agent.add_invariant(хвост)
            print(paint(GREEN, f"  Ограничение {inv.id} заведено."))
            print(paint(DIM, f"  Причину дописать: /почему {inv.id} <текст>"))
            continue
        if cmd == "/почему":
            куски = хвост.split(maxsplit=1)
            if len(куски) < 2 or not куски[0].isdigit():
                print(paint(RED, "  Формат: /почему 1 вся аналитика на SQL")); continue
            найден = next((i for i in agent.invariants if i.id == int(куски[0])), None)
            if not найден:
                print(paint(RED, "  Нет такого номера")); continue
            найден.rationale = куски[1]
            store.save_invariant(найден)
            agent.invariants = store.invariants()
            print(paint(GREEN, "  Причина записана.")); continue
        if cmd in ("/выкл", "/вкл", "/off", "/on"):
            if not хвост.isdigit():
                print(paint(RED, "  Нужен номер: /выкл 1")); continue
            включить = cmd in ("/вкл", "/on")
            if agent.toggle_invariant(int(хвост), включить):
                print(paint(GREEN if включить else YELLOW,
                            f"  Ограничение {хвост} "
                            f"{'включено' if включить else 'отключено'}."))
                if not включить:
                    print(paint(DIM, "  Спросите то же самое и сравните ответ."))
            else:
                print(paint(RED, "  Нет такого номера"))
            continue
        if cmd in ("/удалить", "/del"):
            if not хвост.isdigit():
                print(paint(RED, "  Нужен номер: /удалить 1")); continue
            print(paint(GREEN if agent.drop_invariant(int(хвост)) else RED,
                        f"  {'Удалено' if True else ''}: {хвост}")); continue
        if cmd == "/промпт":
            блок = agent._invariants_prompt()
            if not блок:
                print(paint(DIM, "\n  Активных ограничений нет.")); continue
            print(paint(BOLD, "\n  В модель уезжает вот это:\n"))
            for строка in блок.splitlines():
                print(paint(MAG, f"    {строка}"))
            print(paint(DIM, f"\n  {agent.weigh()['invariants']} токенов "
                             f"в КАЖДОМ запросе."))
            continue

        print(paint(GREEN, f"{agent.name.lower()} › "), end="", flush=True)
        hint = "думает…"
        erase = "\b" * len(hint) + " " * len(hint) + "\b" * len(hint)
        if TTY:
            print(paint(DIM, hint), end="", flush=True)
        waiting = TTY

        def stop():
            nonlocal waiting
            if waiting:
                print(erase, end="", flush=True); waiting = False

        try:
            for piece in agent.stream(msg):
                stop(); print(piece, end="", flush=True)
        except LLMError as exc:
            stop(); print(paint(RED, f"\n  Ошибка: {exc}")); continue
        except KeyboardInterrupt:
            stop(); print(paint(DIM, "\n  прервано")); continue
        stop()
        print()

        # Аудит — отдельный запрос уже ПОСЛЕ ответа. Блок в промпте это
        # просьба; проверка показывает, выполнена ли она на самом деле.
        if agent.auditing and agent.journal:
            try:
                show_audit(agent.audit(agent.journal[-1].answer))
            except LLMError as exc:
                print(paint(DIM, f"  (проверить не удалось: {exc})"))
        print(paint(DIM, f"  {agent.spent_pretty}\n"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
