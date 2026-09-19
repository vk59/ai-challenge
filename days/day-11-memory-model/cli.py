#!/usr/bin/env python3
"""День 11: три слоя памяти в терминале.

    python3 cli.py
    python3 cli.py --session задача-2

Команды:
    /слои                      показать все три слоя
    /запомнить <слой> <ключ>=<значение>   положить вручную
                               слой: рабочая | профиль | решение | знание
    /забыть <ключ>             стереть из долговременной
    /вес                       из чего складывается запрос
    /сброс                     стереть диалог и рабочую память (долгая цела)
    /выход
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import (DECISION, KNOWLEDGE, LONG, MemoryError_, PROFILE,  # noqa: E402
                    WORKING, open_store)
from tokens import money  # noqa: E402

TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 90)
DIM, BOLD, CYAN, GREEN, RED, YELLOW, MAG = "2", "1", "36", "32", "31", "33", "35"

СЛОИ = {"рабочая": (WORKING, None), "профиль": (LONG, PROFILE),
        "решение": (LONG, DECISION), "знание": (LONG, KNOWLEDGE)}
ВИДЫ = {PROFILE: "профиль", DECISION: "решения", KNOWLEDGE: "знания"}


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def show_layers(agent: Agent) -> None:
    L = agent.layers()
    print(paint(BOLD, "\n  Три слоя памяти\n"))

    s = L["short"]
    print(paint(YELLOW, f"  ● Краткосрочная") + paint(DIM, f"  область: {s['область']}"))
    print(paint(DIM, f"     в окне {s['в окне']} из {s['потолок']} пар, "
                     f"в архиве {s['в архиве']}"))
    for r in s["реплики"][-4:]:
        кто = "вы" if r["role"] == "user" else "агент"
        print(paint(DIM, f"     {кто}: {r['content'][:58]}"))

    w = L["working"]
    print(paint(CYAN, f"\n  ● Рабочая") + paint(DIM, f"  область: {w['область']}"))
    if not w["items"]:
        print(paint(DIM, "     (задача ещё не описана)"))
    for it in w["items"]:
        print(f"     {it['key']}: " + paint(DIM, str(it['value'])[:52]))

    l = L["long"]
    print(paint(MAG, f"\n  ● Долговременная") + paint(DIM, f"  область: {l['область']}"))
    if not l["items"]:
        print(paint(DIM, "     (агент вас ещё не знает)"))
    for вид in (PROFILE, DECISION, KNOWLEDGE):
        группа = [i for i in l["items"] if i["kind"] == вид]
        if not группа:
            continue
        print(paint(DIM, f"     ── {ВИДЫ[вид]}"))
        for it in группа:
            откуда = f"  ← {it['source']}" if it.get("source") else ""
            print(f"     {it['key']}: " + paint(DIM, str(it['value'])[:44] + откуда))

    print(paint(DIM, "\n  Краткосрочная и рабочая живут в этом диалоге."))
    print(paint(DIM, "  Долговременная — во всех: смените диалог, она останется."))


def show_weight(agent: Agent) -> None:
    w = agent.weigh()
    print(paint(BOLD, f"\n  Следующий запрос весит ~{w['total']:,} токенов"))
    print(paint(DIM, f"    обвязка           {w['overhead']:>7}"))
    print(paint(DIM, f"    роль              {w['role']:>7}"))
    if w["memos"]:
        print(paint(MAG, f"    долговременная    {w['memos']:>7}"))
    if w["facts"]:
        print(paint(CYAN, f"    рабочая           {w['facts']:>7}"))
    print(paint(YELLOW, f"    история в окне    {w['history']:>7}"))
    print(paint(DIM, f"    цена входа        {money(w['cost']):>7}"))


def main() -> int:
    args = sys.argv[1:]
    сессия = args[args.index("--session") + 1] if "--session" in args else "default"

    try:
        store = open_store("sqlite")
    except MemoryError_ as exc:
        print(paint(RED, f"  Ошибка хранилища: {exc}"))
        return 1

    agent = Agent(name="Помощник", store=store, session=сессия, memory_turns=4)
    agent.layered = True

    print(paint(BOLD, f"\n  {agent.name}"))
    print(paint(DIM, f"  диалог «{сессия}» · краткосрочная {agent.remembers} пар · "
                     f"рабочая {len(agent.facts)} · долговременная {len(agent.memos)}"))
    if agent.memos:
        print(paint(MAG, f"  Агент вас уже знает: {len(agent.memos)} записей "
                         f"из прошлых диалогов."))
    print(paint(DIM, "  /помощь — команды, Ctrl+D — выход\n"))

    while True:
        try:
            msg = input(paint(CYAN, "вы › ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not msg:
            continue

        части = msg.split(maxsplit=2)
        cmd = части[0].lower()

        if cmd in ("/выход", "/quit"):
            break
        if cmd in ("/помощь", "/help"):
            print(paint(DIM, __doc__.split("Команды:")[1])); continue
        if cmd in ("/слои", "/layers"):
            show_layers(agent); continue
        if cmd in ("/вес", "/weigh"):
            show_weight(agent); continue
        if cmd in ("/сброс", "/reset"):
            agent.reset()
            print(paint(GREEN, "  Диалог и рабочая память стёрты. "
                               "Долговременная цела.")); continue
        if cmd in ("/забыть", "/forget"):
            if len(части) < 2:
                print(paint(RED, "  Нужен ключ: /забыть имя")); continue
            print(paint(GREEN if agent.forget(части[1]) else DIM,
                        f"  {'Забыто' if True else ''}: {части[1]}")); continue
        if cmd in ("/запомнить", "/remember"):
            if len(части) < 3 or "=" not in части[2]:
                print(paint(RED, "  Формат: /запомнить профиль имя=Иван")); continue
            слой = части[1].lower()
            if слой not in СЛОИ:
                print(paint(RED, f"  Слой: {', '.join(СЛОИ)}")); continue
            ключ, значение = части[2].split("=", 1)
            куда, вид = СЛОИ[слой]
            try:
                agent.remember(ключ.strip(), значение.strip(), layer=куда,
                               kind=вид or PROFILE)
            except (ValueError, MemoryError_) as exc:
                print(paint(RED, f"  {exc}")); continue
            print(paint(GREEN, f"  Записано в {слой}: {ключ.strip()}")); continue

        было_р = {f.key for f in agent.facts}
        было_д = {m.key for m in agent.memos}

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

        if agent.journal:
            try:
                agent._route(agent.journal[-1].question, agent.journal[-1].answer)
            except LLMError:
                pass

        новые_р = sorted({f.key for f in agent.facts} - было_р)
        новые_д = sorted({m.key for m in agent.memos} - было_д)
        хвост = f"\n  в окне {agent.remembers} пар"
        if новые_р:
            хвост += paint(CYAN, f" · рабочая +{len(новые_р)}: {', '.join(новые_р)[:34]}")
        if новые_д:
            хвост += paint(MAG, f" · долговременная +{len(новые_д)}: "
                                f"{', '.join(новые_д)[:34]}")
        print(paint(DIM, хвост) + paint(DIM, f" · {agent.spent_pretty}\n"))

    print(paint(DIM, f"  Долговременная память сохранена: {len(agent.memos)} записей. "
                     f"Она будет здесь и в следующем диалоге."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
