#!/usr/bin/env python3
"""Пути до shared и дня 22 с контрольными вопросами (день 27)."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parents[1] / "shared"


def neighbour(name: str) -> Path:
    nearby = HERE / name
    return nearby if nearby.exists() else HERE.parents[0] / name


def setup() -> None:
    for path in (str(neighbour("day-22-rag")),
                 str(SHARED if SHARED.exists() else HERE), str(HERE)):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


__all__ = ["setup", "neighbour", "HERE", "SHARED"]
