#!/usr/bin/env python3
"""День 24: тест проверялки цитат.

    python3 test_verify.py

Проверка, которая подтверждает любую цитату, бесполезна — именно она
и была бы самым опасным местом дня. Поэтому цитаты подкладываются
руками, в том числе заведомо выдуманные, и сверяется вердикт. Модель
не участвует: это тест логики.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from cite import verify_quote  # noqa: E402

CHUNK = """## Главные грабли: macOS не пускает приложение к репозиторию

Всё работало из терминала и падало в собранном `.app`:
fatal: Unable to read current working directory: Operation not permitted

Причина — TCC: приложению, запущенному из Finder, macOS не даёт читать
~/Documents, а репозиторий лежит именно там."""

CASES: list[tuple[str, str, bool]] = [
    ("дословно", "Причина — TCC: приложению, запущенному из Finder", True),
    ("дословно через перенос строки", "macOS не даёт читать\n~/Documents", True),
    ("другой регистр", "ПРИЧИНА — TCC: ПРИЛОЖЕНИЮ", True),
    ("лишние пробелы", "Причина  —   TCC:   приложению", True),
    ("с многоточием", "Причина — TCC … а репозиторий лежит именно там", True),
    ("лишняя точка на конце",
     "Причина — TCC: приложению, запущенному из Finder.", True),
    # Дальше — то, что проверка обязана отклонить.
    ("многоточие в обратном порядке",
     "репозиторий лежит … Причина — TCC", False),
    ("пересказ своими словами",
     "TCC запрещает приложениям из Finder читать домашнюю папку", False),
    ("подменено одно слово",
     "Причина — SIP: приложению, запущенному из Finder", False),
    ("выдумка целиком",
     "Для обхода нужно отключить Gatekeeper в настройках", False),
    ("слишком короткая, хоть и дословная", "TCC", False),
    ("пустая", "", False),
]


def main() -> None:
    wrong = 0
    for name, quote, expected in CASES:
        ok, ratio = verify_quote(quote, CHUNK)
        good = ok == expected
        wrong += not good
        print("%s %-36s подтверждена=%-5s доля %.2f (ждали %s)"
              % ("✓" if good else "✗", name, ok, ratio, expected))
    print(f"\nошибок проверялки: {wrong} из {len(CASES)}")
    sys.exit(1 if wrong else 0)


if __name__ == "__main__":
    main()
