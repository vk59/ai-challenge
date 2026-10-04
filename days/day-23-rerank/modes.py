#!/usr/bin/env python3
"""День 23: четыре режима поиска — от простого к полному.

Режимы отличаются ровно одним приёмом за раз. Иначе сравнение показывало бы
«стало лучше», не говоря, от чего именно.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from rerank import FINAL_K, THRESHOLD, WIDE_K  # noqa: E402

MODES: dict[str, dict] = {
    "base": {
        "title": "без второго этапа",
        "caption": f"топ-{FINAL_K} как есть — режим дня 22",
        "options": {},
    },
    "threshold": {
        "title": "порог близости",
        "caption": f"взять {WIDE_K}, отсечь ниже {THRESHOLD}, оставить {FINAL_K}",
        "options": {"threshold": THRESHOLD},
    },
    "rerank": {
        "title": "реранкинг моделью",
        "caption": f"взять {WIDE_K}, переупорядочить, оставить {FINAL_K}",
        "options": {"threshold": THRESHOLD, "rerank": True},
    },
    "full": {
        "title": "переписывание + реранкинг",
        "caption": "вопрос → поисковый запрос → широкая выдача → порог → реранк",
        "options": {"threshold": THRESHOLD, "rerank": True, "rewrite": True},
    },
}

ORDER = ["base", "threshold", "rerank", "full"]


def options_for(mode: str) -> dict:
    if mode not in MODES:
        raise KeyError(f"Нет режима «{mode}». Есть: {', '.join(ORDER)}")
    return dict(MODES[mode]["options"])


__all__ = ["MODES", "ORDER", "options_for", "WIDE_K", "FINAL_K", "THRESHOLD"]
