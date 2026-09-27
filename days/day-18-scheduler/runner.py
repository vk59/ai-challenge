#!/usr/bin/env python3
"""Разовый прогон планировщика — для launchd (день 18).

Запускается по расписанию системой, делает один tick и выходит. Это и есть
настоящий круглосуточный фон: приложение может быть закрыто, ноутбук может
перезагружаться, а задачи всё равно выполняются.

    python3 runner.py          выполнить всё, чему вышел срок
    python3 runner.py --list   показать задачи и выйти

Ставится через install_agent.sh.
"""

import sys
from pathlib import Path

ЗДЕСЬ = Path(__file__).resolve().parent
sys.path.insert(0, str(ЗДЕСЬ.parents[1] / "shared"))
sys.path.insert(0, str(ЗДЕСЬ))

from scheduler import Scheduler  # noqa: E402
from server import исполнить_reminder, исполнить_repo_digest, исполнить_token_report  # noqa: E402


def main() -> int:
    планировщик = Scheduler()
    планировщик.register("repo_digest", исполнить_repo_digest)
    планировщик.register("token_report", исполнить_token_report)
    планировщик.register("reminder", исполнить_reminder)

    if "--list" in sys.argv:
        задачи = планировщик.tasks()
        print(f"Задач: {len(задачи)}  ·  база: {планировщик.path}")
        for з in задачи:
            значок = "●" if з.enabled else "○"
            print(f"  {значок} {з.id}. {з.name} [{з.kind}] — "
                  f"{з.interval_pretty}, {з.due_pretty}, "
                  f"прогонов {з.runs_count}")
        return 0

    сделано = планировщик.tick()
    if not сделано:
        print("Сроков не вышло, делать нечего.")
        return 0

    print(f"Выполнено задач: {len(сделано)}")
    for прогон in сделано:
        значок = "✓" if прогон.ok else "✕"
        пропуск = (f"  (пропущено периодов: {прогон.missed})"
                   if прогон.missed else "")
        print(f"  {значок} {прогон.summary[:110]}{пропуск}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
