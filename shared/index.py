"""Индекс документов: нарезка, векторы, поиск (день 21).

Две стратегии нарезки, и сравнивать их — главная работа дня:

    fixed       окно фиксированной длины с перехлёстом. Не знает ничего
                о тексте, поэтому режет посреди фразы, зато куски ровные
                и предсказуемые по цене.
    structural  границы берутся из самого документа: заголовки в markdown,
                определения верхнего уровня в python. Куски осмысленные,
                но дико разного размера.

У structural есть неочевидная оговорка. Раздел на пять тысяч знаков —
плохой кусок: вектор размазывается по десятку тем и перестаёт отвечать
на конкретный вопрос. Поэтому длинные разделы всё равно дробятся по
размеру, но с сохранением имени раздела в метаданных. Чистая структурная
нарезка без потолка проигрывает именно из-за этого.

Метаданные у каждого куска: source, title, section, chunk_id.
"""

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from embeddings import embed, embed_one, в_байты, из_байтов, similarity

from embeddings import бэкенд as бэкенд_эмбеддингов  # noqa: E402
from memory import default_dir  # noqa: E402


def база_для(бэк: str | None = None) -> "Path":
    """Свой файл индекса на каждый бэкенд эмбеддингов.

    Векторы разных моделей несравнимы — у облачной 1536 измерений,
    у локальной 768. Класть их в одну таблицу значит получить поиск,
    который молча возвращает мусор. Поэтому индексы раздельные и
    переключаются той же переменной окружения, что и бэкенд.
    """
    бэк = бэк or бэкенд_эмбеддингов()
    имя = "index.db" if бэк == "cloud" else f"index-{бэк}.db"
    return default_dir() / имя


# Тот же каталог, что у кэша эмбеддингов и истории диалогов.
БАЗА = база_для()

FIXED, STRUCTURAL = "fixed", "structural"
СТРАТЕГИИ = (FIXED, STRUCTURAL)

# Окно и перехлёст для fixed. 1200 знаков — примерно 300 токенов: кусок
# ещё про одно, но уже с контекстом. Перехлёст спасает факты, попавшие
# на стык: без него ответ, разорванный границей, не находится ничем.
РАЗМЕР = 1200
ПЕРЕХЛЁСТ = 200

# Потолок для структурных кусков и минимум, ниже которого кусок не
# самостоятелен и приклеивается к соседу.
ПОТОЛОК = 1800
МИНИМУМ = 120


class IndexError_(RuntimeError):
    """Что-то не так с индексом."""


@dataclass
class Document:
    path: str
    title: str
    kind: str          # markdown | python
    text: str


@dataclass
class Chunk:
    source: str        # откуда взято — путь в репозитории
    title: str         # заголовок документа
    section: str       # раздел внутри документа
    chunk_id: str      # устойчивый идентификатор
    n: int             # порядковый номер внутри документа
    start: int         # смещение в исходном тексте
    text: str
    strategy: str = ""

    @property
    def length(self) -> int:
        return len(self.text)

    def as_dict(self, *, full: bool = False) -> dict:
        d = {"source": self.source, "title": self.title,
             "section": self.section, "chunk_id": self.chunk_id,
             "n": self.n, "start": self.start, "length": self.length,
             "strategy": self.strategy}
        d["text"] = self.text if full else self.text[:260]
        return d


# ── стратегия 1: фиксированный размер ───────────────────────────────────
def нарезать_по_размеру(документ: Document, *, размер: int = РАЗМЕР,
                        перехлёст: int = ПЕРЕХЛЁСТ) -> list[Chunk]:
    текст = документ.text
    куски: list[Chunk] = []
    шаг = max(1, размер - перехлёст)
    позиция, номер = 0, 0
    while позиция < len(текст):
        конец = min(позиция + размер, len(текст))
        # Не рвём слово посередине: отступаем до пробела, если он близко.
        if конец < len(текст):
            пробел = текст.rfind(" ", позиция + шаг // 2, конец)
            if пробел > позиция:
                конец = пробел
        кусок = текст[позиция:конец].strip()
        if кусок:
            номер += 1
            куски.append(Chunk(
                source=документ.path, title=документ.title,
                # У fixed раздела нет и быть не может — он не смотрит
                # в текст. Пишем честно, а не подсовываем заголовок.
                section=f"знаки {позиция}–{конец}",
                chunk_id=f"{документ.path}#{FIXED}#{номер}",
                n=номер, start=позиция, text=кусок, strategy=FIXED))
        if конец >= len(текст):
            break
        позиция = конец - перехлёст if конец - перехлёст > позиция else конец
    return куски


# ── стратегия 2: по структуре ───────────────────────────────────────────
ЗАГОЛОВОК = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.M)
ОПРЕДЕЛЕНИЕ = re.compile(r"^(?:def|class)\s+(\w+)", re.M)


def _разделы_markdown(текст: str) -> list[tuple[int, str]]:
    """Возвращает (смещение, имя раздела) по заголовкам."""
    найденные = [(м.start(), м.group(2)) for м in ЗАГОЛОВОК.finditer(текст)]
    if not найденные or найденные[0][0] > 0:
        найденные.insert(0, (0, "начало"))
    return найденные


def _разделы_python(текст: str) -> list[tuple[int, str]]:
    """Границы — определения верхнего уровня; всё до первого зовём «шапка»."""
    найденные = [(м.start(), м.group(1)) for м in ОПРЕДЕЛЕНИЕ.finditer(текст)]
    if not найденные or найденные[0][0] > 0:
        найденные.insert(0, (0, "шапка модуля"))
    return найденные


def _раздробить(тело: str, потолок: int, минимум: int) -> list[str]:
    """Длинный раздел — на части не длиннее потолка.

    Режем по абзацам: это ближе к смыслу, чем счётчик знаков. Два места,
    где наивная версия врала:

    1. Абзац сам длиннее потолка — по абзацам он не делится никак,
       и кусок уезжал в индекс на 2361 знак при потолке 1800. Для такого
       абзаца откатываемся на резку по размеру.
    2. Хвост в конце раздела — последний `@dataclass` строкой уходил
       отдельным куском на десять знаков. Вектор десяти знаков не значит
       ничего, поэтому короткие части приклеиваем к предыдущей.
    """
    части: list[str] = []
    накопитель = ""
    for абзац in тело.split("\n\n"):
        if len(абзац) > потолок:
            if накопитель:
                части.append(накопитель)
                накопитель = ""
            for начало in range(0, len(абзац), потолок):
                части.append(абзац[начало:начало + потолок])
            continue
        if накопитель and len(накопитель) + len(абзац) + 2 > потолок:
            части.append(накопитель)
            накопитель = абзац
        else:
            накопитель = f"{накопитель}\n\n{абзац}" if накопитель else абзац
    if накопитель:
        части.append(накопитель)

    # Короткие части приклеиваем к соседу: назад, а первую — вперёд,
    # иначе одинокий заголовок перед длинной таблицей так и останется
    # куском на одиннадцать знаков.
    склеенные: list[str] = []
    вперёд = ""
    for часть in части:
        if вперёд:
            часть, вперёд = вперёд + "\n\n" + часть, ""
        if len(часть.strip()) < минимум:
            if склеенные:
                склеенные[-1] = склеенные[-1] + "\n\n" + часть
            else:
                вперёд = часть
            continue
        склеенные.append(часть)
    if вперёд:
        склеенные.append(вперёд) if not склеенные else None
    # Склейка хвоста может вывести часть на 2–5% за потолок (1879 при 1800).
    # Это осознанный размен: гонять её обратно через резку значит плодить
    # новый короткий хвост и зацикливаться.
    return склеенные


def нарезать_по_структуре(документ: Document, *, потолок: int = ПОТОЛОК,
                          минимум: int = МИНИМУМ) -> list[Chunk]:
    текст = документ.text
    границы = (_разделы_python(текст) if документ.kind == "python"
               else _разделы_markdown(текст))

    # Сначала собираем разделы целиком, потом доводим до вменяемых размеров.
    разделы: list[tuple[str, int, str]] = []
    for индекс, (смещение, имя) in enumerate(границы):
        конец = границы[индекс + 1][0] if индекс + 1 < len(границы) else len(текст)
        тело = текст[смещение:конец].strip()
        if тело:
            разделы.append((имя, смещение, тело))

    # Слишком мелкий раздел сам по себе бесполезен: один заголовок без
    # содержимого. Приклеиваем к предыдущему, сохраняя его имя. А вот
    # первому приклеиваться не к чему — его тащим вперёд, в следующий,
    # иначе заголовок документа навсегда остаётся куском на сорок знаков.
    собранные: list[tuple[str, int, str]] = []
    хвост = ""
    for имя, смещение, тело in разделы:
        if хвост:
            тело = хвост + "\n\n" + тело
            хвост = ""
        if len(тело) < минимум:
            if собранные:
                имя_п, смещение_п, тело_п = собранные[-1]
                собранные[-1] = (имя_п, смещение_п, тело_п + "\n\n" + тело)
            else:
                хвост = тело
            continue
        собранные.append((имя, смещение, тело))
    if хвост:
        собранные.append(("начало", 0, хвост))

    куски: list[Chunk] = []
    номер = 0
    for имя, смещение, тело in собранные:
        части = ([тело] if len(тело) <= потолок
                 else _раздробить(тело, потолок, минимум))
        всего = len(части)
        for порядок, часть in enumerate(части, 1):
            если_дробили = f" ({порядок}/{всего})" if всего > 1 else ""
            номер += 1
            куски.append(Chunk(
                source=документ.path, title=документ.title,
                section=имя + если_дробили,
                chunk_id=f"{документ.path}#{STRUCTURAL}#{номер}",
                n=номер, start=смещение, text=часть.strip(),
                strategy=STRUCTURAL))
    return куски


def нарезать(документ: Document, стратегия: str, **настройки) -> list[Chunk]:
    if стратегия == FIXED:
        return нарезать_по_размеру(документ, **настройки)
    if стратегия == STRUCTURAL:
        return нарезать_по_структуре(документ, **настройки)
    raise IndexError_(f"Неизвестная стратегия: {стратегия}")


# ── хранилище ───────────────────────────────────────────────────────────
class Index:
    """Индекс в SQLite: метаданные и векторы рядом."""

    def __init__(self, path: Path | str | None = None):
        # База вычисляется на создании, а не на импорте: переменную
        # окружения ставят и после импорта модуля.
        self.path = Path(path) if path else база_для()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._создать()

    def _соединение(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _создать(self) -> None:
        with self._соединение() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS chunks (
                            id        INTEGER PRIMARY KEY AUTOINCREMENT,
                            strategy  TEXT NOT NULL,
                            source    TEXT NOT NULL,
                            title     TEXT NOT NULL,
                            section   TEXT NOT NULL,
                            chunk_id  TEXT NOT NULL,
                            n         INTEGER NOT NULL,
                            start     INTEGER NOT NULL,
                            length    INTEGER NOT NULL,
                            text      TEXT NOT NULL,
                            vector    BLOB,
                            UNIQUE(strategy, chunk_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS по_стратегии "
                       "ON chunks(strategy)")

    # ── сборка ──────────────────────────────────────────────────────────
    def rebuild(self, документы: list[Document], стратегия: str, *,
                на_шаг=None, **настройки) -> dict:
        """Пересобирает индекс для одной стратегии и возвращает статистику."""
        куски: list[Chunk] = []
        for документ in документы:
            куски.extend(нарезать(документ, стратегия, **настройки))
        if not куски:
            raise IndexError_("Нарезка не дала ни одного куска")

        векторы = embed([к.text for к in куски], на_пачку=на_шаг)

        with self._соединение() as db:
            db.execute("DELETE FROM chunks WHERE strategy = ?", (стратегия,))
            db.executemany(
                "INSERT INTO chunks (strategy, source, title, section, "
                "chunk_id, n, start, length, text, vector) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(к.strategy, к.source, к.title, к.section, к.chunk_id,
                  к.n, к.start, к.length, к.text, в_байты(в))
                 for к, в in zip(куски, векторы)])
        return self.stats(стратегия)

    # ── чтение ──────────────────────────────────────────────────────────
    def stats(self, стратегия: str) -> dict:
        with self._соединение() as db:
            строка = db.execute(
                "SELECT COUNT(*) кусков, COUNT(DISTINCT source) документов, "
                "       SUM(length) знаков, AVG(length) средний, "
                "       MIN(length) наименьший, MAX(length) наибольший "
                "FROM chunks WHERE strategy = ?", (стратегия,)).fetchone()
            длины = [р[0] for р in db.execute(
                "SELECT length FROM chunks WHERE strategy = ? "
                "ORDER BY length", (стратегия,))]
        середина = длины[len(длины) // 2] if длины else 0
        разброс = 0.0
        if len(длины) > 1:
            среднее = sum(длины) / len(длины)
            разброс = (sum((д - среднее) ** 2 for д in длины) / len(длины)) ** 0.5
        return {
            "strategy": стратегия,
            "chunks": строка["кусков"] or 0,
            "documents": строка["документов"] or 0,
            "chars": строка["знаков"] or 0,
            "avg": round(строка["средний"] or 0),
            "median": середина,
            "min": строка["наименьший"] or 0,
            "max": строка["наибольший"] or 0,
            # Разброс длин — главная числовая разница между стратегиями.
            "spread": round(разброс),
        }

    def search(self, запрос: str, стратегия: str, *, k: int = 5
               ) -> list[tuple[Chunk, float]]:
        вектор = embed_one(запрос)
        with self._соединение() as db:
            строки = db.execute(
                "SELECT * FROM chunks WHERE strategy = ?", (стратегия,)
            ).fetchall()
        if not строки:
            raise IndexError_(
                f"Индекс для «{стратегия}» пуст. Соберите его: python3 build.py")

        оценки: list[tuple[Chunk, float]] = []
        for строка in строки:
            близость = similarity(вектор, из_байтов(строка["vector"]))
            оценки.append((self._кусок(строка), близость))
        оценки.sort(key=lambda п: п[1], reverse=True)
        return оценки[:k]

    def sources(self, стратегия: str) -> list[str]:
        with self._соединение() as db:
            return [р[0] for р in db.execute(
                "SELECT DISTINCT source FROM chunks WHERE strategy = ? "
                "ORDER BY source", (стратегия,))]

    def sample(self, стратегия: str, *, limit: int = 6) -> list[Chunk]:
        with self._соединение() as db:
            строки = db.execute(
                "SELECT * FROM chunks WHERE strategy = ? ORDER BY id LIMIT ?",
                (стратегия, limit)).fetchall()
        return [self._кусок(с) for с in строки]

    @staticmethod
    def _кусок(строка: sqlite3.Row) -> Chunk:
        return Chunk(source=строка["source"], title=строка["title"],
                     section=строка["section"], chunk_id=строка["chunk_id"],
                     n=строка["n"], start=строка["start"], text=строка["text"],
                     strategy=строка["strategy"])


__all__ = ["Index", "Chunk", "Document", "IndexError_", "нарезать",
           "нарезать_по_размеру", "нарезать_по_структуре",
           "FIXED", "STRUCTURAL", "СТРАТЕГИИ", "РАЗМЕР", "ПЕРЕХЛЁСТ",
           "ПОТОЛОК"]
