#!/usr/bin/env python3
"""День 10: чат с переключателем стратегий управления контекстом.

    python3 cli.py --strategy window     последние N сообщений, остальное в мусор
    python3 cli.py --strategy facts      карточка «ключ-значение» + последние N
    python3 cli.py --strategy branch     то же окно, но с ветвлением диалога

Команды:
    /стратегия [имя]   показать или переключить на лету
    /факты             карточка фактов
    /вес               из чего складывается следующий запрос
    /стат              токены, отдельно на диалог и на служебные запросы
    /чекпоинт          показать текущую точку диалога
    /ветка <имя>       ответвиться отсюда и перейти в новую ветку
    /ветки             список веток
    /перейти <имя>     переключиться в другую ветку
    /сброс             стереть текущую ветку
    /выход

Переключатель работает на живом диалоге: стратегию можно сменить посреди
разговора и увидеть, как меняется то, что уезжает в модель.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from agent import STRATEGIES, Agent, LLMError  # noqa: E402
from memory import MemoryError_, open_store  # noqa: E402
from tokens import money  # noqa: E402

TTY = sys.stdout.isatty()
WIDTH = min(shutil.get_terminal_size((90, 24)).columns, 90)
DIM, BOLD, CYAN, GREEN, RED, YELLOW = "2", "1", "36", "32", "31", "33"

ОПИСАНИЕ = {
    "window": "последние N пар, остальное отбрасывается",
    "facts": "карточка «ключ-значение» + последние N пар",
    "branch": "окно + ветвление диалога от чекпоинта",
}


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


def show_facts(agent: Agent) -> None:
    if not agent.facts:
        если = ("" if agent.strategy == "facts"
                else "  Стратегия сейчас не «facts» — карточка не обновляется.")
        print(paint(DIM, f"\n  Карточка фактов пуста.{если}"))
        return
    print(paint(BOLD, f"\n  Карточка фактов — {len(agent.facts)} записей"))
    for f in agent.facts:
        print(paint(CYAN, f"    {f.key}: {f.value}"))
    print(paint(DIM, "\n    Обновляется после каждой реплики отдельным запросом."))
    print(paint(DIM, "    Ключ перезаписывается: новое значение вытесняет старое."))


def show_weight(agent: Agent) -> None:
    w = agent.weigh()
    print(paint(BOLD, f"\n  Следующий запрос весит ~{w['total']:,} токенов"))
    print(paint(DIM, f"    обвязка          {w['overhead']:>7}"))
    print(paint(DIM, f"    роль             {w['role']:>7}"))
    if w["summary"]:
        print(paint(CYAN, f"    пересказ         {w['summary']:>7}"))
    if w["facts"]:
        print(paint(CYAN, f"    факты            {w['facts']:>7}  ← вместо старых реплик"))
    print(paint(DIM, f"    история в окне   {w['history']:>7}"))
    print(paint(DIM, f"    цена входа       {money(w['cost']):>7}"))


def show_stats(agent: Agent) -> None:
    s = agent.stats
    print(paint(BOLD, "\n  Счёт"))
    print(paint(DIM, f"    стратегия:           {agent.strategy}"))
    print(paint(DIM, f"    реплик:              {s.turns}"))
    print(paint(DIM, f"    токенов на диалог:   {s.dialogue_tokens:>8,}"))
    if s.extractions:
        print(paint(YELLOW, f"    на карточку фактов:  {s.extraction_tokens:>8,}  "
                            f"({s.extractions} обновлений)"))
    if s.compressions:
        print(paint(YELLOW, f"    на свёртки:          {s.compression_tokens:>8,}"))
    print(paint(DIM, f"    всего:               {s.total_tokens:>8,}"))
    print(paint(DIM, f"    потрачено:           {agent.spent_pretty:>8}"))
    print(paint(DIM, f"    в окне:              {agent.remembers} из {agent.memory_turns} пар"))


def main() -> int:
    args = sys.argv[1:]
    стратегия = "window"
    if "--strategy" in args:
        стратегия = args[args.index("--strategy") + 1]
    if стратегия not in STRATEGIES:
        print(f"Неизвестная стратегия: {стратегия}. Ожидалось: {', '.join(STRATEGIES)}")
        return 1
    окно = int(args[args.index("--memory") + 1]) if "--memory" in args else 3

    try:
        store = open_store("sqlite")
    except MemoryError_ as exc:
        print(paint(RED, f"  Ошибка хранилища: {exc}"))
        return 1

    agent = Agent(name="Аналитик", store=store, session=f"день10-{стратегия}",
                  strategy=стратегия, memory_turns=окно)

    print(paint(BOLD, f"\n  {agent.name}"))
    print(paint(DIM, f"  стратегия: {стратегия} — {ОПИСАНИЕ[стратегия]}"))
    print(paint(DIM, f"  {agent.describe()}"))
    print(paint(DIM, "  /помощь — команды, Ctrl+D — выход\n"))

    while True:
        try:
            message = input(paint(CYAN, f"вы [{agent.strategy}] › ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not message:
            continue

        cmd = message.lower().split()
        голова = cmd[0] if cmd else ""

        if голова in ("/выход", "/quit"):
            break
        if голова in ("/помощь", "/help"):
            print(paint(DIM, __doc__.split("Команды:")[1].split("Переключатель")[0]))
            continue
        if голова in ("/стратегия", "/strategy"):
            if len(cmd) > 1:
                если = cmd[1]
                if если not in STRATEGIES:
                    print(paint(RED, f"  Неизвестная: {если}. "
                                     f"Есть: {', '.join(STRATEGIES)}"))
                    continue
                agent.strategy = если
                print(paint(GREEN, f"  Переключено на «{если}» — {ОПИСАНИЕ[если]}"))
            else:
                print(paint(DIM, f"\n  Сейчас: {agent.strategy} — "
                                 f"{ОПИСАНИЕ[agent.strategy]}"))
                for имя in STRATEGIES:
                    метка = " ←" if имя == agent.strategy else "  "
                    print(paint(DIM, f"   {метка} {имя:8} {ОПИСАНИЕ[имя]}"))
            continue
        if голова in ("/факты", "/facts"):
            show_facts(agent); continue
        if голова in ("/вес", "/weigh"):
            show_weight(agent); continue
        if голова in ("/стат", "/stats"):
            show_stats(agent); continue
        if голова in ("/чекпоинт", "/checkpoint"):
            print(paint(DIM, f"\n  Текущая точка: реплика {agent.checkpoint()}. "
                             f"Ответвиться: /ветка <имя>"))
            continue
        if голова in ("/ветка", "/branch"):
            if len(cmd) < 2:
                print(paint(RED, "  Нужно имя: /ветка эксперимент")); continue
            имя = f"день10-{cmd[1]}"
            try:
                сколько = agent.fork(имя)
            except LLMError as exc:
                print(paint(RED, f"  {exc}")); continue
            agent.switch(имя)
            print(paint(GREEN, f"  Ветка «{cmd[1]}» создана от реплики {сколько} "
                               f"и вы уже в ней."))
            continue
        if голова in ("/ветки", "/branches"):
            свои = [b for b in agent.branches() if b.startswith("день10-")]
            print(paint(BOLD, "\n  Диалоги и ветки"))
            for b in свои:
                метка = paint(GREEN, " ← вы здесь") if b == agent.session else ""
                print(f"    {b}{метка}")
            continue
        if голова in ("/перейти", "/switch"):
            if len(cmd) < 2:
                print(paint(RED, "  Нужно имя: /перейти эксперимент")); continue
            цель = cmd[1] if cmd[1].startswith("день10-") else f"день10-{cmd[1]}"
            agent.switch(цель)
            print(paint(GREEN, f"  Перешли в «{цель}»: {agent.archived} пар, "
                               f"{len(agent.facts)} фактов."))
            continue
        if голова in ("/сброс", "/reset"):
            agent.reset()
            print(paint(GREEN, "  Ветка стёрта.")); continue

        было_извлечений = agent.stats.extractions

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

        # stream() не обновляет карточку — это делает _remember при ask().
        # Для стратегии facts обновим явно, чтобы поведение совпадало.
        if agent.strategy == "facts" and agent.journal:
            последний = agent.journal[-1]
            try:
                agent._extract_facts(последний.question, последний.answer)
            except LLMError:
                pass

        last = agent.journal[-1]
        print(paint(DIM, f"\n  вход {last.prompt_tokens:,} · "
                         f"ответ {last.completion_tokens} · "
                         f"в окне {agent.remembers} пар · {agent.spent_pretty}"))
        if agent.stats.extractions > было_извлечений:
            print(paint(YELLOW, f"  ⊞ карточка обновлена: {len(agent.facts)} фактов, "
                                f"/факты — посмотреть"))
        print()

    print(paint(DIM, f"  Итого: {agent.stats.total_tokens:,} токенов "
                     f"({agent.stats.overhead_tokens:,} служебных), "
                     f"{agent.spent_pretty}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
