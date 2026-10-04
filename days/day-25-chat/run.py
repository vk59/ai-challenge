#!/usr/bin/env python3
"""День 25: прогон сценариев с проверкой цели и источников.

    python3 run.py                 # оба сценария
    python3 run.py 1               # только первый
    python3 run.py -v              # плюс ответы целиком
    python3 run.py --no-rewrite    # без раскрытия уточнений
    python3 run.py --no-task       # без памяти задачи

Два последних флага не для красоты: именно они показывают, что приёмы
нужны. Без раскрытия уточнений реплики вроде «Сколько именно?» уходят
в поиск как есть, близость падает ниже порога и ассистент отказывается
отвечать на законный вопрос. Без памяти задачи он к концу диалога
не может сказать, с чего разговор начинался.

Проверяется по каждой реплике:

    источники   обязательны у УВЕРЕННОГО ответа. Первая версия проверки
                требовала их у всех подряд и показывала 6 из 12 — пока
                не выяснилось, что она неправа: на «с чего я начинал
                разговор?» ответ лежит в истории, а не в документах,
                и цитировать там нечего. Такие реплики помечены как meta,
                а настоящее нарушение — уверенный ответ без источника
    цель        осталось ли в цели диалога хоть одно опорное слово
    отказ       на вопросах не по теме он нужен, на остальных нет
    просьба     отказ обязан содержать просьбу уточнить, иначе диалог
                упирается в тупик
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from chat import ChatSession, TaskStore  # noqa: E402
from index import Index  # noqa: E402
from llm import LLMError  # noqa: E402
from memory import open_store  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402


def goal_holds(goal: str, words: list[str]) -> bool:
    low = (goal or "").lower()
    return any(word in low for word in words)


def run_scenario(scenario: dict, number: int, *, rewrite: bool, track: bool,
                 verbose: bool, index: Index) -> dict:
    session_name = f"сценарий-{number}"
    store = open_store("sqlite")
    store.clear(session_name)
    tasks = TaskStore()
    tasks.drop(session_name)

    session = ChatSession(session_name, store=store, index=index, tasks=tasks,
                          track_task=track)

    print(f"\n{'═' * 78}")
    print(f"{number}. {scenario['name']} — {len(scenario['messages'])} реплик")
    print(f"   цель держим по словам: {', '.join(scenario['goal_words'])}")
    print(f"   раскрытие уточнений: {'да' if rewrite else 'НЕТ'}"
          f"   память задачи: {'да' if track else 'НЕТ'}")

    stats = {"turns": 0, "with_sources": 0, "abstained": 0,
             "goal_kept": 0, "goal_checked": 0, "rewritten": 0,
             "wrong_abstain": 0, "missing_abstain": 0, "tokens": 0,
             "verified_quotes": 0, "fabricated_quotes": 0,
             "goal_lost_at": None, "last_goal": "",
             # Главные числа: сколько ответов требовали источника и
             # сколько его получили.
             "needed_sources": 0, "got_sources": 0,
             "refusals": 0, "refusals_with_ask": 0, "blanks": 0}

    for position, (message, kind) in enumerate(scenario["messages"], 1):
        # Отключаем раскрытие, подменяя историю пустой: так уточнение
        # уходит в поиск как есть, и видно, что без приёма ломается.
        if not rewrite:
            original_resolve = session.__class__.ask
        try:
            if rewrite:
                reply = session.ask(message)
            else:
                import chat as chat_module
                saved = chat_module.resolve_query
                chat_module.resolve_query = lambda m, t, k: (m, False, 0)
                try:
                    reply = session.ask(message)
                finally:
                    chat_module.resolve_query = saved
        except LLMError as failure:
            print(f"   {position:2}. сбой: {failure}")
            continue

        stats["turns"] += 1
        stats["tokens"] += reply.total_tokens
        stats["rewritten"] += reply.rewritten
        stats["with_sources"] += reply.has_sources
        stats["abstained"] += reply.abstained
        stats["verified_quotes"] += reply.cited.verified_quotes
        stats["fabricated_quotes"] += reply.cited.fabricated_quotes

        if kind == "offtopic" and not (reply.abstained
                                       or not reply.cited.confident):
            stats["missing_abstain"] += 1
        if kind != "offtopic" and reply.abstained:
            stats["wrong_abstain"] += 1
        if reply.cited.blank:
            stats["blanks"] += 1

        # Источники обязательны там, где ответ уверенный и не про переписку.
        confident = reply.cited.confident and not reply.abstained
        if confident and kind != "meta":
            stats["needed_sources"] += 1
            stats["got_sources"] += reply.has_sources
        if not confident:
            stats["refusals"] += 1
            low = (reply.answer or "").lower()
            stats["refusals_with_ask"] += ("уточни" in low or "уточне" in low
                                           or "?" in low)

        # Цель проверяем со третьей реплики: раньше она ещё складывается.
        if position >= 3 and track:
            stats["goal_checked"] += 1
            kept = goal_holds(reply.task.goal, scenario["goal_words"])
            stats["goal_kept"] += kept
            if not kept and stats["goal_lost_at"] is None:
                stats["goal_lost_at"] = position
        stats["last_goal"] = reply.task.goal

        marks = []
        if confident and kind != "meta":
            marks.append("ист." + ("✓" if reply.has_sources else "✗"))
        elif reply.has_sources:
            marks.append("ист.+")
        else:
            marks.append("ист.—")
        if reply.abstained:
            marks.append("ОТКАЗ")
        if reply.rewritten:
            marks.append("раскрыт")
        if position >= 3 and track:
            marks.append("цель" + ("✓" if goal_holds(reply.task.goal,
                                                     scenario["goal_words"])
                                   else "✗"))
        print(f"   {position:2}. [{kind:10}] {' '.join(marks):26} "
              f"{reply.total_tokens:5} ток.  {message[:40]}")
        if reply.rewritten:
            print(f"       запрос → {reply.query[:92]}")
        if verbose:
            print(f"       ответ  : {' '.join(reply.answer.split())[:160]}")
            for source in reply.sources[:2]:
                print(f"       {source['chunk_id']}")

    print(f"\n   цель в конце: {stats['last_goal'] or '(пусто)'}")
    task = session.task
    if task.constraints:
        print("   зафиксировано:")
        for item in task.constraints[:4]:
            print(f"     — {item[:96]}")
    return stats


def main() -> None:
    argv = sys.argv[1:]
    verbose = "-v" in argv or "--verbose" in argv
    rewrite = "--no-rewrite" not in argv
    track = "--no-task" not in argv
    numbers = [int(a) for a in argv if a.isdigit()]
    chosen = [(n, s) for n, s in enumerate(SCENARIOS, 1)
              if not numbers or n in numbers]

    index = Index()
    results = []
    for number, scenario in chosen:
        results.append((scenario, run_scenario(scenario, number,
                                               rewrite=rewrite, track=track,
                                               verbose=verbose, index=index)))

    print(f"\n{'═' * 78}")
    print(f"{'сценарий':32} {'реплик':>7} {'источники':>11} {'цель':>8} "
          f"{'отказы':>8} {'токенов':>9}")
    for scenario, stats in results:
        goal = (f"{stats['goal_kept']}/{stats['goal_checked']}"
                if stats["goal_checked"] else "—")
        sources = (f"{stats['got_sources']}/{stats['needed_sources']}"
                   if stats["needed_sources"] else "—")
        print(f"{scenario['name'][:32]:32} {stats['turns']:>7} "
              f"{sources:>11} {goal:>8} "
              f"{stats['refusals']:>8} {stats['tokens']:>9,}"
              .replace(",", " "))
    print("\n  «источники» — у уверенных ответов, не считая вопросов "
          "о самой переписке")

    print()
    for scenario, stats in results:
        beef = []
        if stats["wrong_abstain"]:
            beef.append(f"лишних отказов {stats['wrong_abstain']}")
        if stats["missing_abstain"]:
            beef.append(f"не отказался не по теме {stats['missing_abstain']}")
        if stats["goal_lost_at"]:
            beef.append(f"цель потеряна на реплике {stats['goal_lost_at']}")
        if stats["fabricated_quotes"]:
            beef.append(f"выдуманных цитат {stats['fabricated_quotes']}")
        if stats["needed_sources"] > stats["got_sources"]:
            beef.append(f"уверенных ответов без источника "
                        f"{stats['needed_sources'] - stats['got_sources']}")
        if stats["refusals"] > stats["refusals_with_ask"]:
            beef.append(f"отказов без просьбы уточнить "
                        f"{stats['refusals'] - stats['refusals_with_ask']}")
        if stats["blanks"]:
            beef.append(f"пустых ответов модели {stats['blanks']}")
        verdict = "; ".join(beef) if beef else "без нарушений"
        print(f"  {scenario['name'][:40]:42} {verdict}")
        print(f"  {'':42} уточнений раскрыто {stats['rewritten']}, "
              f"цитат подтверждено {stats['verified_quotes']}")


if __name__ == "__main__":
    main()
