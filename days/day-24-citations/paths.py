#!/usr/bin/env python3
"""Где лежат чужие модули — в репозитории и внутри бандла (день 24).

День 24 берёт контрольный набор из дня 22 и реранкинг из дня 23. В
репозитории они уровнем выше, внутри собранного .app — в подпапках рядом,
потому что иначе одноимённые файлы (`evaluate.py`, `paths.py`) затёрли бы
друг друга.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parents[1] / "shared"


def neighbour(name: str) -> Path:
    """Каталог дня: рядом (бандл) или уровнем выше (репозиторий)."""
    nearby = HERE / name
    return nearby if nearby.exists() else HERE.parents[0] / name


def setup() -> None:
    """Свой каталог — первым, чужие после. Порядок не косметика: в дне 22
    есть `evaluate.py`, и если его каталог окажется впереди, импорт
    достанется ему."""
    for path in (str(neighbour("day-23-rerank")), str(neighbour("day-22-rag")),
                 str(SHARED if SHARED.exists() else HERE), str(HERE)):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


__all__ = ["setup", "neighbour", "HERE", "SHARED"]
