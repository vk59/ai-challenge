#!/usr/bin/env python3
"""День 20: тест самой проверялки маршрутов.

    python3 test_checks.py

Проверка, которая всегда говорит «✓», бесполезна. Здесь журнал вызовов
подаётся вручную, в том числе заведомо неправильный, и сверяется, что
`проверить()` выносит ожидаемый вердикт. Модель не участвует — это тест
логики, а не поведения агента.
"""

import sys
from pathlib import Path

ЗДЕСЬ = Path(__file__).resolve().parent
sys.path.insert(0, str(ЗДЕСЬ.parents[1] / "shared"))
sys.path.insert(0, str(ЗДЕСЬ))

from flow import проверить  # noqa: E402

СЛУЧАИ = [
    ("всё верно",
     {"servers": {"git", "pipe"}, "expect": ["git__repo_stats", "pipe__search"],
      "forbid": ["pipe__run_pipeline"],
      "order": ["pipe__search", "pipe__summarize"]},
     ["git__repo_stats", "pipe__search", "pipe__summarize"], True),
    ("не тот сервер: вместо git пошёл в pipe",
     {"servers": {"git"}, "expect": ["git__search_commits"],
      "forbid": ["pipe__search"]},
     ["pipe__search"], False),
    ("порядок нарушен: сжал раньше, чем нашёл",
     {"servers": {"pipe"}, "expect": ["pipe__search", "pipe__summarize"],
      "order": ["pipe__search", "pipe__summarize"]},
     ["pipe__summarize", "pipe__search"], False),
    ("шаг пропущен: не сохранил в файл",
     {"servers": {"pipe"},
      "expect": ["pipe__search", "pipe__summarize", "pipe__save_to_file"]},
     ["pipe__search", "pipe__summarize"], False),
    ("инструменты не вызывались вовсе",
     {"servers": {"git"}, "expect": ["git__repo_stats"]},
     [], False),
    # Модель вправе уточнить поиск лишним вызовом — это не ошибка порядка.
    ("лишний повтор порядок не рушит",
     {"servers": {"pipe"}, "expect": ["pipe__search"],
      "order": ["pipe__search", "pipe__summarize"]},
     ["pipe__search", "pipe__search", "pipe__summarize"], True),
    # Независимые части длинного флоу могут перемешаться, лишь бы
    # зависимая цепочка осталась в порядке.
    ("чужой вызов в середине цепочки не мешает",
     {"servers": {"pipe", "sched"},
      "expect": ["pipe__search", "pipe__save_to_file", "sched__add_task"],
      "order": ["pipe__search", "pipe__summarize", "pipe__save_to_file"]},
     ["pipe__search", "pipe__summarize", "sched__add_task",
      "pipe__save_to_file"], True),
]


def main() -> None:
    плохо = 0
    for имя, сценарий, вызовы, ждём in СЛУЧАИ:
        итоги = проверить(сценарий, вызовы)
        получилось = all(ладно for ладно, _ in итоги)
        ок = получилось == ждём
        плохо += not ок
        print("%s %-46s вердикт %-5s (ждали %s)"
              % ("✓" if ок else "✗", имя, получилось, ждём))
        if not ок:
            for ладно, текст in итоги:
                print("      ↳ %s %s" % ("✓" if ладно else "✗", текст))
    print("\nпроверялка ошиблась в %d случаях из %d" % (плохо, len(СЛУЧАИ)))
    sys.exit(1 if плохо else 0)


if __name__ == "__main__":
    main()
