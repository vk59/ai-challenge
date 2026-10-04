#!/usr/bin/env python3
"""Пути до shared и чужих дней (день 25).

В репозитории shared лежит уровнем выше, внутри собранного .app всё
сложено плоско рядом. Один модуль решает это для всех файлов дня.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parents[1] / "shared"


def setup() -> None:
    for path in (str(SHARED if SHARED.exists() else HERE), str(HERE)):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


__all__ = ["setup", "HERE", "SHARED"]
