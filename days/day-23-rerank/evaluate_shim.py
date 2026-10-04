#!/usr/bin/env python3
"""Переходник к проверялке дня 22.

Там функция называется `исход`, и переименовывать её нельзя: по дню 22
записывается видео, код заморожен. А новый код должен быть на латинице.
Прослойка решает оба требования и стоит три строки.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

sys.path.insert(0, str(paths.day22_dir()))

from evaluate import исход as _verdict  # noqa: E402


def verdict_of(text: str, expect: list[list[str]]) -> str:
    """попал | признался | соврал"""
    return _verdict(text, expect)


__all__ = ["verdict_of"]
