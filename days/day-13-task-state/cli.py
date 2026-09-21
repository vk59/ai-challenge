#!/usr/bin/env python3
"""День 13: задача как конечный автомат — в терминале.

    python3 cli.py                      продолжить задачу в чате «задача»
    python3 cli.py --session деплой     другая задача
    python3 cli.py --no-track           не следить автоматически

Команды:
    /состояние            где сейчас задача
    /задача <цель>        завести новую задачу
    /дальше <этап>        перевести этап вручную
    /шаг <текст>          записать текущий шаг
    /ждём <текст>         записать ожидаемое действие
    /выход

Главное в этом дне — проверяется выходом и повторным запуском. Состояние
лежит отдельно от диалога, поэтому пауза на любом этапе ничего не ломает:
запустите снова, и агент продолжит с того же шага, не переспрашивая.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import (DONE, EXECUTION, PLANNING, STAGE_LABELS, STAGES,  # noqa: E402
                    VALIDATION, MemoryError_, open_store)
from tokens import money  # noqa: E402

TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 90)
DIM, BOLD, CYAN, GREEN, RED, YELLOW, MAG = "2", "1", "36", "32", "31", "33", "35"

# Русские названия принимаются наравне с английскими: команду набирают руками.
ПО_РУССКИ = {"планирование": PLANNING, "выполнение": EXECUTION,
             "проверка": VALIDATION, "готово": DONE}


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def полоса(view: dict) -> str:
    """Этапы одной строкой: пройденные, текущий и будущие."""
    куски = []
    for s in view["stages"]:
        если_текущий = s["current"]
        метка = STAGE_LABELS[s["stage"]]
        if если_текущий:
            куски.append(paint(GREEN, f"[{метка}]"))
        elif s["passed"]:
            куски.append(paint(DIM, f"✓{метка}"))
        else:
            куски.append(paint(DIM, f"·{метка}"))
    return " → ".join(куски)


def show_state(agent: Agent) -> None:
    view = agent.task_view()
    if not view:
        print(paint(DIM, "\n  Задача не заведена. Завести: /задача Сделать импорт"))
        return
    print(paint(BOLD, "\n  Состояние задачи"))
    if view["goal"]:
        print(f"    цель:    {view['goal']}")
    print(f"    этап:    {полоса(view)}")
    if view["step"]:
        print(f"    шаг:     {view['step']}")
    if view["expecting"]:
        print(paint(YELLOW, f"    ждём:    {view['expecting']}"))
    if view["allowed"]:
        куда = ", ".join(a["label"] for a in view["allowed"])
        print(paint(DIM, f"    дальше:  {куда}"))
    else:
        print(paint(GREEN, "    задача завершена"))
    if view["log"]:
        print(paint(DIM, "\n    переходы:"))
        for z in view["log"]:
            откуда = STAGE_LABELS.get(z.get("from"), "")
            куда = STAGE_LABELS.get(z.get("to"), z.get("to", ""))
            стрелка = f"{откуда} → {куда}" if откуда else куда
            заметка = f"  ({z['note']})" if z.get("note") else ""
            print(paint(DIM, f"      {z.get('at','')[:16].replace('T',' ')}  "
                             f"{стрелка}{заметка}"))


def main() -> int:
    args = sys.argv[1:]
    сессия = args[args.index("--session") + 1] if "--session" in args else "задача"

    try:
        store = open_store("sqlite")
    except MemoryError_ as exc:
        print(paint(RED, f"  Ошибка хранилища: {exc}"))
        return 1

    agent = Agent(name="Исполнитель", store=store, session=сессия, memory_turns=6)
    agent.tracking = "--no-track" not in args

    print(paint(BOLD, f"\n  {agent.name} · задача «{сессия}»"))
    if agent.task:
        view = agent.task_view()
        print(paint(DIM, "  Продолжаем с того же места:"))
        print("  " + полоса(view))
        if view["step"]:
            print(paint(DIM, f"  шаг: {view['step']}"))
        if view["expecting"]:
            print(paint(YELLOW, f"  ждём: {view['expecting']}"))
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
            print(paint(DIM, __doc__.split("Команды:")[1].split("Главное")[0]))
            continue
        if cmd in ("/состояние", "/state"):
            show_state(agent); continue
        if cmd in ("/задача", "/task"):
            if not хвост:
                print(paint(RED, "  Нужна цель: /задача Сделать импорт")); continue
            agent.start_task(хвост)
            print(paint(GREEN, f"  Задача заведена, этап «планирование».")); continue
        if cmd in ("/шаг", "/step"):
            agent.update_task(step=хвост)
            print(paint(GREEN, "  Шаг записан.")); continue
        if cmd in ("/ждём", "/ждем", "/expect"):
            agent.update_task(expecting=хвост)
            print(paint(GREEN, "  Ожидание записано.")); continue
        if cmd in ("/дальше", "/advance"):
            цель = ПО_РУССКИ.get(хвост.lower(), хвост.lower())
            получилось, почему = agent.advance(цель, "вручную")
            print(paint(GREEN if получилось else RED, f"  {почему}"))
            if not получилось and agent.task:
                print(paint(DIM, "  Автомат не пускает: переход должен быть "
                                 "ребром графа, а не пожеланием."))
            continue

        было = agent.task.stage if agent.task else None

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

        # stream() не следит за задачей — это делает _remember при ask().
        итог = {}
        if agent.tracking and agent.journal:
            try:
                итог = agent._track_task(agent.journal[-1].question,
                                         agent.journal[-1].answer)
            except LLMError:
                pass

        хвост_строки = f"\n  этап: {agent.task.label if agent.task else '—'}"
        if итог.get("moved"):
            хвост_строки += paint(GREEN, f" · переход: {итог['moved']}")
        if итог.get("rejected"):
            хвост_строки += paint(RED, f" · автомат отклонил: {итог['rejected']}")
        if agent.task and agent.task.expecting:
            хвост_строки += paint(YELLOW, f" · ждём: {agent.task.expecting[:40]}")
        print(paint(DIM, хвост_строки) + paint(DIM, f" · {agent.spent_pretty}\n"))

    if agent.task:
        print(paint(DIM, f"  Пауза на этапе «{agent.task.label}»."))
        if agent.task.step:
            print(paint(DIM, f"  Шаг: {agent.task.step}"))
        print(paint(DIM, "  Запустите снова — продолжим отсюда, без объяснений."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
