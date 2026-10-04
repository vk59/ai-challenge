#!/usr/bin/env python3
"""День 21: сбор корпуса для индексации.

    python3 corpus.py        # что попадёт в индекс и сколько это страниц

Корпус — сам репозиторий: README всех дней, документы из docs/ и общий
код из shared/. Выбор не от лени: задание просит 20–30 страниц текста
«или эквивалент в коде», а здесь и то и другое, причём на разнородных
документах разница между стратегиями нарезки видна лучше всего. Markdown
размечен заголовками, python — определениями, и структурная нарезка
обходится с ними по-разному.

За страницу считаем 1800 знаков — машинописная страница.
"""

import os
import sys
from pathlib import Path

ЗДЕСЬ = Path(__file__).resolve().parent


def _корень() -> Path:
    """Откуда читать документы.

    Приложение, запущенное из Finder, до ~/Documents не достаёт (macOS TCC),
    поэтому build_app.sh копирует документы в Application Support и
    передаёт путь через переменную окружения. В репозитории её нет, и
    корнем остаётся сам репозиторий.
    """
    явно = os.environ.get("AI_ADVENT_CORPUS")
    if явно and Path(явно).expanduser().exists():
        return Path(явно).expanduser()
    return ЗДЕСЬ.parents[1]


КОРЕНЬ = _корень()
sys.path.insert(0, str(КОРЕНЬ / "shared"))

from index import Document  # noqa: E402

НА_СТРАНИЦЕ = 1800

# Что в индекс не идёт. ui.html — почти целиком CSS, он бы забил корпус
# мусором; __pycache__ и dist очевидны.
МИМО = ("__pycache__", "dist", ".venv", "node_modules")


def _заголовок(путь: Path, текст: str) -> str:
    """Для markdown — первый заголовок, для кода — путь."""
    if путь.suffix == ".md":
        for строка in текст.splitlines():
            if строка.startswith("# "):
                return строка[2:].strip()
    return путь.name


def собрать(корень: Path | None = None) -> list[Document]:
    корень = корень or КОРЕНЬ
    документы: list[Document] = []

    пути: list[Path] = []
    пути.append(корень / "README.md")
    пути.extend(sorted((корень / "docs").glob("*.md")))
    пути.extend(sorted((корень / "days").glob("day-*/README.md")))
    пути.extend(sorted((корень / "days").glob("day-*/video.md")))
    пути.extend(sorted((корень / "shared").glob("*.py")))

    for путь in пути:
        if not путь.exists() or any(ч in путь.parts for ч in МИМО):
            continue
        текст = путь.read_text(encoding="utf-8").strip()
        if len(текст) < 200:          # заглушки индексировать нечего
            continue
        документы.append(Document(
            path=str(путь.relative_to(корень)),
            title=_заголовок(путь, текст),
            kind="python" if путь.suffix == ".py" else "markdown",
            text=текст))
    return документы


def сводка(документы: list[Document]) -> dict:
    знаков = sum(len(д.text) for д in документы)
    по_виду: dict[str, list[int]] = {}
    for д in документы:
        по_виду.setdefault(д.kind, []).append(len(д.text))
    return {
        "documents": len(документы),
        "chars": знаков,
        "pages": round(знаков / НА_СТРАНИЦЕ, 1),
        "by_kind": {вид: {"files": len(длины), "chars": sum(длины)}
                    for вид, длины in sorted(по_виду.items())},
    }


def main() -> None:
    документы = собрать()
    с = сводка(документы)
    print(f"Документов: {с['documents']} · {с['chars']:,} знаков "
          f"· ≈{с['pages']} страниц (по {НА_СТРАНИЦЕ} знаков)"
          .replace(",", " "))
    for вид, числа in с["by_kind"].items():
        print(f"  {вид:9} {числа['files']:3} файлов, "
              f"{числа['chars']:,} знаков".replace(",", " "))
    print()
    for д in документы:
        print(f"  {len(д.text):7,}  {д.kind:9} {д.path}".replace(",", " "))


if __name__ == "__main__":
    main()
