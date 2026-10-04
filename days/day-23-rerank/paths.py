#!/usr/bin/env python3
"""Где лежат чужие модули — в репозитории и внутри бандла (день 23).

День 23 переиспользует набор вопросов и проверялку исхода из дня 22.
В репозитории они уровнем выше: `../day-22-rag`. Внутри собранного .app
всё сложено плоско в Resources, и build_app.sh кладёт их в подпапку
`day-22-rag` рядом — иначе `evaluate.py` дня 22 перезаписал бы наш
одноимённый файл.

Два размещения — два пути, и выбирать между ними должен один модуль,
а не каждый импортирующий файл по-своему.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parents[1] / "shared"


def day22_dir() -> Path:
    """Каталог дня 22: рядом (бандл) или уровнем выше (репозиторий)."""
    nearby = HERE / "day-22-rag"
    return nearby if nearby.exists() else HERE.parents[0] / "day-22-rag"


def setup() -> None:
    """Разложить пути так, чтобы наши модули имели приоритет над чужими.

    Порядок важен: свой каталог первым. В дне 22 есть файл `evaluate.py`,
    и если его каталог окажется впереди, `import evaluate` достанется ему,
    а не нашему — с другим набором функций.
    """
    for path in (str(day22_dir()), str(SHARED if SHARED.exists() else HERE),
                 str(HERE)):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


__all__ = ["setup", "day22_dir", "HERE", "SHARED"]
