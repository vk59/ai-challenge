#!/usr/bin/env python3
"""День 12: чат с персонализацией.

    python3 cli.py                      без профиля
    python3 cli.py --profile новичок    сразу с профилем

Команды:
    /профили              список профилей
    /профиль <имя>        включить (или «нет», чтобы снять)
    /создать <имя>        создать профиль по шагам
    /кто                  что сейчас уезжает в модель из профиля
    /вес                  из чего складывается запрос
    /выход

Профиль — это не память. Память агент набирает сам из разговора, профиль
задаётся сознательно и меняется одним переключением, в том числе посреди
диалога: спросите одно и то же до и после — форма ответа изменится.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import MemoryError_, Profile, open_store  # noqa: E402
from tokens import money  # noqa: E402

TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 90)
DIM, BOLD, CYAN, GREEN, RED, YELLOW, MAG = "2", "1", "36", "32", "31", "33", "35"

ЗАГОТОВКИ = {
    "новичок": Profile("новичок", tone="дружелюбно и ободряюще",
                       level="начинающий", format="пошагово, с примерами",
                       language="русский"),
    "senior": Profile("senior", tone="сухо, как коллеге", level="senior",
                      format="только суть, без введения",
                      constraints="не объяснять базовые понятия"),
    "аудитор": Profile("аудитор", tone="формально",
                       level="специалист по безопасности",
                       format="маркированный список требований",
                       constraints="не приводить примеры кода"),
}


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def show_profiles(agent: Agent) -> None:
    профили = agent.profiles()
    print(paint(BOLD, "\n  Профили"))
    if not профили:
        print(paint(DIM, "    пусто. Создать: /создать новичок"))
        print(paint(DIM, f"    готовые заготовки: {', '.join(ЗАГОТОВКИ)}"))
        return
    for p in профили:
        активен = agent.profile and agent.profile.name == p.name
        метка = paint(GREEN, " ← включён") if активен else ""
        print(f"    {p.name}{метка}")
        for подпись, значение in p.filled:
            print(paint(DIM, f"       {подпись}: {значение[:58]}"))


def show_who(agent: Agent) -> None:
    if not agent.profile:
        print(paint(DIM, "\n  Профиль не включён — агент отвечает как всем."))
        print(paint(DIM, "  Включить: /профиль новичок"))
        return
    print(paint(BOLD, f"\n  Активен профиль «{agent.profile.name}»"))
    print(paint(DIM, "  В каждый запрос уезжает вот это:\n"))
    for строка in agent.profile.as_prompt().splitlines():
        print(paint(MAG, f"    {строка}"))
    print(paint(DIM, f"\n  Это {agent.weigh()['profile']} токенов в КАЖДОМ запросе."))


def создать(agent: Agent, имя: str) -> None:
    if имя in ЗАГОТОВКИ:
        agent.save_profile(ЗАГОТОВКИ[имя])
        print(paint(GREEN, f"  Профиль «{имя}» создан из заготовки и включён."))
        return
    print(paint(DIM, f"\n  Создаём «{имя}». Пустая строка — пропустить поле."))
    поля = {}
    for поле, подпись in Profile.ПОЛЯ:
        if поле == "extra":
            continue
        try:
            поля[поле] = input(paint(CYAN, f"    {подпись}: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
    agent.save_profile(Profile(name=имя, **поля))
    print(paint(GREEN, f"  Профиль «{имя}» создан и включён."))


def main() -> int:
    args = sys.argv[1:]
    старт = args[args.index("--profile") + 1] if "--profile" in args else None

    try:
        store = open_store("sqlite")
    except MemoryError_ as exc:
        print(paint(RED, f"  Ошибка хранилища: {exc}"))
        return 1

    agent = Agent(name="Ассистент", store=store, session="день12", memory_turns=6)
    if старт:
        if not agent.use_profile(старт):
            if старт in ЗАГОТОВКИ:
                agent.save_profile(ЗАГОТОВКИ[старт])
            else:
                print(paint(YELLOW, f"  Профиля «{старт}» нет. "
                                    f"Заготовки: {', '.join(ЗАГОТОВКИ)}"))

    print(paint(BOLD, f"\n  {agent.name}"))
    if agent.profile:
        print(paint(MAG, f"  Профиль: {agent.profile.name}"))
    else:
        print(paint(DIM, "  Профиль не включён"))
    print(paint(DIM, "  /помощь — команды, Ctrl+D — выход\n"))

    while True:
        try:
            msg = input(paint(CYAN, f"вы › ")).strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not msg:
            continue

        части = msg.split(maxsplit=1)
        cmd = части[0].lower()

        if cmd in ("/выход", "/quit"):
            break
        if cmd in ("/помощь", "/help"):
            print(paint(DIM, __doc__.split("Команды:")[1].split("Профиль —")[0]))
            continue
        if cmd in ("/профили", "/profiles"):
            show_profiles(agent); continue
        if cmd in ("/кто", "/who"):
            show_who(agent); continue
        if cmd in ("/вес", "/weigh"):
            w = agent.weigh()
            print(paint(BOLD, f"\n  Запрос весит ~{w['total']} токенов"))
            print(paint(DIM, f"    роль      {w['role']:>6}"))
            print(paint(MAG, f"    профиль   {w['profile']:>6}"))
            print(paint(DIM, f"    память    {w['memos'] + w['facts']:>6}"))
            print(paint(DIM, f"    история   {w['history']:>6}"))
            continue
        if cmd in ("/создать", "/new"):
            if len(части) < 2:
                print(paint(RED, "  Нужно имя: /создать новичок")); continue
            создать(agent, части[1].strip()); continue
        if cmd in ("/профиль", "/profile"):
            if len(части) < 2:
                show_who(agent); continue
            имя = части[1].strip()
            if имя.lower() in ("нет", "none", "-"):
                agent.use_profile(None)
                print(paint(GREEN, "  Профиль снят.")); continue
            if agent.use_profile(имя):
                print(paint(GREEN, f"  Включён «{имя}». Спросите то же самое "
                                   f"и сравните."))
            elif имя in ЗАГОТОВКИ:
                agent.save_profile(ЗАГОТОВКИ[имя])
                print(paint(GREEN, f"  Создан из заготовки и включён «{имя}»."))
            else:
                print(paint(RED, f"  Нет профиля «{имя}». /профили — список"))
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

        if agent.journal:
            last = agent.journal[-1]
            профиль = (paint(MAG, f" · профиль «{agent.profile.name}»")
                       if agent.profile else paint(DIM, " · без профиля"))
            print(paint(DIM, f"\n  {last.completion_tokens} токенов")
                  + профиль + paint(DIM, f" · {agent.spent_pretty}\n"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
