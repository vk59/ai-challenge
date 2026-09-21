#!/usr/bin/env python3
"""День 15: контролируемые переходы — условия на рёбрах.

    python3 cli.py
    python3 cli.py --session импорт

Команды:
    /состояние              где задача и что заперто
    /задача <цель>          завести задачу
    /утвердить план         поставить отметку «план утверждён»
    /утвердить проверку     поставить отметку «проверка пройдена»
    /снять план             снять отметку обратно
    /дальше <этап>          попытаться перейти
    /права                  что можно и чего нельзя на текущем этапе
    /выход

Отличие от дня 13. Там переход проверялся на наличие ребра: из планирования
в готово нельзя, потому что такого ребра нет. Здесь ребро planning → execution
ЕСТЬ, но заперто: пройти по нему можно только после того, как человек
утвердит план. Модель утвердить его не может — иначе условие не значило бы
ничего.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import (DONE, EXECUTION, PLANNING, STAGE_LABELS, VALIDATION,  # noqa: E402
                    MemoryError_, open_store)

TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 90)
DIM, BOLD, CYAN, GREEN, RED, YELLOW, MAG = "2", "1", "36", "32", "31", "33", "35"

ПО_РУССКИ = {"планирование": PLANNING, "выполнение": EXECUTION,
             "проверка": VALIDATION, "готово": DONE}
ОТМЕТКИ = {"план": "plan_approved", "плана": "plan_approved",
           "проверку": "validation_passed", "проверка": "validation_passed",
           "валидацию": "validation_passed"}


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def полоса(view: dict) -> str:
    куски = []
    for s in view["stages"]:
        метка = STAGE_LABELS[s["stage"]]
        if s["current"]:
            куски.append(paint(GREEN, f"[{метка}]"))
        elif s["passed"]:
            куски.append(paint(DIM, f"✓{метка}"))
        else:
            куски.append(paint(DIM, f"·{метка}"))
    return " → ".join(куски)


def show_state(agent: Agent) -> None:
    view = agent.task_view()
    if not view:
        print(paint(DIM, "\n  Задача не заведена: /задача Импорт остатков"))
        return
    print(paint(BOLD, "\n  Состояние задачи"))
    if view["goal"]:
        print(f"    цель:   {view['goal']}")
    print(f"    этап:   {полоса(view)}")

    print(paint(BOLD, "\n    Переходы отсюда:"))
    if not view["allowed"]:
        print(paint(GREEN, "      задача завершена"))
    for g in view["allowed"]:
        if g["met"]:
            print(paint(GREEN, f"      ✓ {g['label']} — открыт"))
        else:
            print(paint(RED, f"      ✕ {g['label']} — ЗАПЕРТ: нужно чтобы "
                             f"{g['guard']}"))

    if view["approved"]:
        print(paint(BOLD, "\n    Поставленные отметки:"))
        for ключ, когда in view["approved"].items():
            подпись = next((g["label"] for g in view["guards"] if g["key"] == ключ),
                           ключ)
            print(paint(GREEN, f"      ✓ {подпись}") + paint(DIM, f"  {когда[:16]}"))
    else:
        print(paint(DIM, "\n    Отметок нет."))


def show_rights(agent: Agent) -> None:
    view = agent.task_view()
    if not view or not view["rights"]:
        print(paint(DIM, "\n  Задача не заведена."))
        return
    print(paint(BOLD, f"\n  Этап «{view['label']}» — что агенту разрешено"))
    print(paint(GREEN, f"    можно:  {view['rights'].get('можно','')}"))
    print(paint(RED, f"    НЕЛЬЗЯ: {view['rights'].get('нельзя','')}"))
    print(paint(DIM, "\n  Это уезжает в модель вместе с состоянием задачи."))


def main() -> int:
    args = sys.argv[1:]
    сессия = args[args.index("--session") + 1] if "--session" in args else "задача-15"
    try:
        store = open_store("sqlite")
    except MemoryError_ as exc:
        print(paint(RED, f"  Ошибка хранилища: {exc}"))
        return 1

    agent = Agent(name="Исполнитель", store=store, session=сессия, memory_turns=6)
    agent.tracking = True
    # Условия на рёбрах и права этапа — это и есть день 15.
    agent.guards = True

    print(paint(BOLD, f"\n  {agent.name} · задача «{сессия}»"))
    if agent.task:
        view = agent.task_view()
        print("  " + полоса(view))
        заперто = [g for g in view["allowed"] if not g["met"]]
        if заперто:
            print(paint(RED, f"  Заперто: {', '.join(g['label'] for g in заперто)}"))
    else:
        print(paint(DIM, "  Задача не заведена: /задача <цель>"))
    print(paint(DIM, "  /помощь — команды, Ctrl+D — пауза\n"))

    while True:
        try:
            метка = agent.task.label if agent.task else "нет задачи"
            msg = input(paint(CYAN, f"вы [{метка}] › ")).strip()
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
            print(paint(DIM, __doc__.split("Команды:")[1].split("Отличие")[0]))
            continue
        if cmd in ("/состояние", "/state"):
            show_state(agent); continue
        if cmd in ("/права", "/rights"):
            show_rights(agent); continue
        if cmd in ("/задача", "/task"):
            if not хвост:
                print(paint(RED, "  Нужна цель")); continue
            agent.start_task(хвост)
            print(paint(GREEN, "  Задача заведена, этап «планирование»."))
            print(paint(DIM, "  Переход в выполнение заперт до утверждения плана."))
            continue
        if cmd in ("/утвердить", "/approve"):
            ключ = ОТМЕТКИ.get(хвост.lower())
            if not ключ:
                print(paint(RED, "  Что утверждаем: /утвердить план "
                                 "или /утвердить проверку")); continue
            ок, текст = agent.approve(ключ)
            print(paint(GREEN if ок else RED, f"  {текст}"))
            if ок:
                print(paint(DIM, "  Переход открыт — /состояние покажет."))
            continue
        if cmd in ("/снять", "/revoke"):
            ключ = ОТМЕТКИ.get(хвост.lower())
            if not ключ:
                print(paint(RED, "  Что снимаем: /снять план")); continue
            print(paint(YELLOW if agent.revoke(ключ) else DIM,
                        "  Отметка снята." if agent.revoke(ключ) or True
                        else "")); continue
        if cmd in ("/дальше", "/advance"):
            цель = ПО_РУССКИ.get(хвост.lower(), хвост.lower())
            ок, почему = agent.advance(цель, "вручную")
            print(paint(GREEN if ок else RED, f"  {почему}"))
            if not ок and "требует" in почему:
                print(paint(DIM, "  Это условие на ребре: ребро есть, "
                                 "но заперто. Утвердить: /утвердить план"))
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

        итог = {}
        if agent.tracking and agent.journal:
            try:
                итог = agent._track_task(agent.journal[-1].question,
                                         agent.journal[-1].answer)
            except LLMError:
                pass
        хвост_строки = f"\n  этап: {agent.task.label if agent.task else '—'}"
        if итог.get("moved"):
            хвост_строки += paint(GREEN, f" · {итог['moved']}")
        if итог.get("rejected"):
            хвост_строки += paint(RED, f" · автомат отклонил: {итог['rejected']}")
        print(paint(DIM, хвост_строки) + paint(DIM, f" · {agent.spent_pretty}\n"))

    if agent.task:
        view = agent.task_view()
        заперто = [g["label"] for g in view["allowed"] if not g["met"]]
        print(paint(DIM, f"  Пауза на этапе «{agent.task.label}»."))
        if заперто:
            print(paint(DIM, f"  Заперты переходы: {', '.join(заперто)}"))
        print(paint(DIM, "  Запустите снова — отметки и этап на месте."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
