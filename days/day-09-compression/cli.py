#!/usr/bin/env python3
"""День 9: чат с агентом, который сжимает историю вместо того, чтобы её терять.

    python3 cli.py --compress                   сжатие включено
    python3 cli.py                              без сжатия, для сравнения
    python3 cli.py --compress --keep 2 --every 3   чаще сворачивать

Команды: /пересказ, /вес, /стат, /выход

Что происходит под капотом. Агент держит `keep` последних пар «как есть».
Когда сверх них накапливается `every` пар, эта пачка уходит в модель на
свёртку и возвращается одним абзацем пересказа. Пересказ хранится отдельно
(на диске, как и диалог) и подставляется в запрос рядом с ролью — ВМЕСТО
свёрнутых реплик.

В дне 6 вытесненные пары просто исчезали из окна. В дне 7 они хотя бы
оставались на диске, но в модель всё равно не попадали. Здесь они попадают —
в сжатом виде.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import Agent, LLMError  # noqa: E402
from memory import MemoryError_, open_store  # noqa: E402
from tokens import money  # noqa: E402

TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 90)
DIM, BOLD, CYAN, GREEN, RED, YELLOW = "2", "1", "36", "32", "31", "33"


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


HELP = """  /пересказ  показать сжатую историю целиком
  /вес       из чего складывается следующий запрос
  /стат      токены и деньги, отдельно на диалог и на сжатие
  /сброс     стереть диалог и пересказ
  /выход     завершить (или Ctrl+D)"""


def show_summary(agent: Agent) -> None:
    if not agent.compress:
        print(paint(DIM, "\n  Сжатие выключено. Включить: --compress"))
        return
    if not agent.summary:
        порог = agent.keep_last + agent.summarize_every
        есть = agent.remembers
        print(paint(DIM, f"\n  Пересказа ещё нет: свёртка включится, когда в окне "
                         f"станет больше {порог} пар (сейчас {есть})."))
        return

    s = agent.summary
    print(paint(BOLD, f"\n  Сжатая история — {s.covered} пар в {s.rounds} свёртках"))
    for строка in s.content.splitlines():
        if строка.strip():
            print(paint(CYAN, f"    {строка.strip()}"))
    print(paint(DIM, f"\n    обновлён {s.when if hasattr(s, 'when') else s.at}"))
    print(paint(DIM, f"    на свёртки ушло {s.tokens:,} токенов"))
    print(paint(DIM, f"    в окне «как есть» осталось {agent.remembers} пар"))


def show_weight(agent: Agent) -> None:
    w = agent.weigh()
    print(paint(BOLD, f"\n  Следующий запрос весит ~{w['total']:,} токенов"))
    print(paint(DIM, f"    обвязка          {w['overhead']:>7}"))
    print(paint(DIM, f"    роль             {w['role']:>7}"))
    if w["summary"]:
        print(paint(CYAN, f"    пересказ         {w['summary']:>7}  "
                          f"← вместо свёрнутых пар"))
    print(paint(DIM, f"    история в окне   {w['history']:>7}"))
    print(paint(DIM, f"    цена входа       {money(w['cost']):>7}"))


def show_stats(agent: Agent) -> None:
    s = agent.stats
    print(paint(BOLD, "\n  Счёт"))
    print(paint(DIM, f"    реплик:              {s.turns}"))
    print(paint(DIM, f"    токенов на диалог:   {s.dialogue_tokens:>8,}"))
    if s.compressions:
        print(paint(YELLOW, f"    токенов на сжатие:   {s.compression_tokens:>8,}  "
                            f"({s.compressions} свёрток за этот запуск)"))
        print(paint(YELLOW, f"    из них деньгами:     "
                            f"{money(agent.spent_on_compression):>8}"))
    print(paint(DIM, f"    всего за запуск:     {s.total_tokens:>8,}"))
    print(paint(DIM, f"    потрачено:           {agent.spent_pretty:>8}"))
    if agent.compress:
        # Эти два числа — «за всё время», они лежат на диске вместе
        # с пересказом и переживают перезапуск, в отличие от счётчиков выше.
        свёрнуто = agent.summary.covered if agent.summary else 0
        свёрток = agent.summary.rounds if agent.summary else 0
        потрачено = agent.summary.tokens if agent.summary else 0
        print(paint(DIM, f"    свёрнуто всего:      {свёрнуто} пар "
                         f"за {свёрток} свёрток"))
        print(paint(DIM, f"    на них ушло:         {потрачено:,} токенов "
                         f"(за всё время, включая прошлые запуски)"))
        print(paint(DIM, f"    в окне «как есть»:   {agent.remembers} "
                         f"(потолок {agent.keep_last + agent.summarize_every})"))


def parse(args: list[str]) -> dict:
    o = {"compress": "--compress" in args, "keep": 3, "every": 4, "session": None}
    for флаг, поле in (("--keep", "keep"), ("--every", "every"),
                       ("--session", "session")):
        if флаг in args:
            i = args.index(флаг)
            if i + 1 >= len(args):
                raise SystemExit(f"У флага {флаг} не хватает значения")
            o[поле] = args[i + 1]
    for поле in ("keep", "every"):
        try:
            o[поле] = int(o[поле])
        except ValueError:
            raise SystemExit(f"У флага --{поле} должно быть число") from None
        if o[поле] < 1:
            raise SystemExit(f"--{поле} меньше единицы не имеет смысла")
    return o


def main() -> int:
    o = parse(sys.argv[1:])
    try:
        store = open_store("sqlite")
    except MemoryError_ as exc:
        print(paint(RED, f"  Ошибка хранилища: {exc}"))
        return 1

    сессия = o["session"] or ("сжатие" if o["compress"] else "без-сжатия")
    agent = Agent(name="Ассистент", store=store, session=сессия,
                  compress=o["compress"], keep_last=o["keep"],
                  summarize_every=o["every"])

    print(paint(BOLD, f"\n  {agent.name}"))
    print(paint(DIM, f"  {agent.describe()}"))
    if o["compress"]:
        print(paint(DIM, f"  свёртка каждые {o['every']} пар, "
                         f"{o['keep']} последних держим как есть"))
    print(paint(DIM, "  /помощь — команды, Ctrl+D — выход\n"))

    while True:
        try:
            message = input(paint(CYAN, "вы › ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not message:
            continue

        cmd = message.lower()
        if cmd in ("/выход", "/quit"):
            break
        if cmd in ("/помощь", "/help"):
            print(paint(DIM, HELP)); continue
        if cmd in ("/пересказ", "/summary"):
            show_summary(agent); continue
        if cmd in ("/вес", "/weigh"):
            show_weight(agent); continue
        if cmd in ("/стат", "/stats"):
            show_stats(agent); continue
        if cmd in ("/сброс", "/reset"):
            agent.reset()
            print(paint(GREEN, "  Диалог и пересказ стёрты.")); continue

        свёрток_было = agent.stats.compressions

        print(paint(GREEN, f"{agent.name.lower()} › "), end="", flush=True)
        hint = "думает…"
        erase = "\b" * len(hint) + " " * len(hint) + "\b" * len(hint)
        if TTY:
            print(paint(DIM, hint), end="", flush=True)
        waiting = TTY

        def stop_waiting() -> None:
            nonlocal waiting
            if waiting:
                print(erase, end="", flush=True)
                waiting = False

        try:
            for piece in agent.stream(message):
                stop_waiting()
                print(piece, end="", flush=True)
        except LLMError as exc:
            stop_waiting()
            print(paint(RED, f"\n  Ошибка: {exc}")); continue
        except KeyboardInterrupt:
            stop_waiting()
            print(paint(DIM, "\n  прервано")); continue
        stop_waiting()

        if not agent.journal:
            print(paint(DIM, "\n  Пустой ответ.\n")); continue

        last = agent.journal[-1]
        хвост = (f"\n  вход {last.prompt_tokens:,} · ответ {last.completion_tokens} · "
                 f"в окне {agent.remembers} пар · {agent.spent_pretty}")
        print(paint(DIM, хвост))

        # Свёртка происходит молча внутри агента — но показать её надо:
        # это событие, за которое заплачено, и его стоит заметить.
        if agent.stats.compressions > свёрток_было:
            print(paint(YELLOW, f"  ⤶ история свёрнута: {agent.summary.covered} пар "
                                f"ушли в пересказ, /пересказ — посмотреть"))
        print()

    print(paint(DIM, f"  Итого: {agent.stats.total_tokens:,} токенов "
                     f"({agent.stats.compression_tokens:,} из них на сжатие), "
                     f"{agent.spent_pretty}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
