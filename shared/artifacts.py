"""Артефакты — данные, которые инструменты передают друг другу (день 19).

Зачем это нужно. Когда инструменты выстраиваются в цепочку, результат
первого должен попасть во второй. Наивный путь — вернуть данные текстом:
тогда они уедут в контекст модели, она их перескажет и передаст дальше
своими словами. На каждом шаге данные будут терять точность, а на длинных
выборках ещё и стоить денег.

Поэтому инструмент возвращает не данные, а НОМЕР артефакта. Данные лежат
в SQLite, модель видит только номер и короткое превью, а следующий
инструмент забирает содержимое по номеру. Модель оркеструет цепочку,
но сами данные через неё не проходят.

    artifact_id = поиск(...)        → #7, 4200 знаков
    summarize(7)                    → #8, 380 знаков
    save_to_file(8, "отчёт.md")     → файл на диске

Проверяется это просто: у артефакта есть длина и sha256, и по ним видно,
что второй инструмент получил ровно то, что записал первый.
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from memory import default_dir, local_time, now_iso

ARTIFACTS_FILE = "artifacts.db"

# Сколько знаков показывать модели в превью. Больше незачем: превью нужно,
# чтобы она поняла, что получилось, а не чтобы читала данные целиком.
ПРЕВЬЮ = 240


class ArtifactError(Exception):
    """Понятная человеку ошибка работы с артефактами."""


@dataclass
class Artifact:
    """Кусок данных, произведённый инструментом."""

    id: int = 0
    kind: str = ""              # search | summary | file …
    title: str = ""
    content: str = ""
    meta: dict = field(default_factory=dict)
    source_id: int = 0          # из какого артефакта получен
    tool: str = ""              # какой инструмент произвёл
    at: str = ""

    @property
    def length(self) -> int:
        return len(self.content)

    @property
    def sha(self) -> str:
        """Короткий хеш — им и проверяется, что данные не подменились."""
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:12]

    @property
    def preview(self) -> str:
        одна = " ".join(self.content.split())
        return одна[:ПРЕВЬЮ] + "…" if len(одна) > ПРЕВЬЮ else одна

    @property
    def when(self) -> str:
        return local_time(self.at)

    def as_dict(self, *, full: bool = False) -> dict:
        готово = {"id": self.id, "kind": self.kind, "title": self.title,
                  "length": self.length, "sha": self.sha, "tool": self.tool,
                  "source_id": self.source_id, "at": self.at,
                  "when": self.when, "preview": self.preview, "meta": self.meta}
        if full:
            готово["content"] = self.content
        return готово


class ArtifactStore:
    """Хранилище артефактов. Одно на приложение, живёт в SQLite."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS artifacts (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        kind      TEXT    NOT NULL DEFAULT '',
        title     TEXT    NOT NULL DEFAULT '',
        content   TEXT    NOT NULL DEFAULT '',
        meta      TEXT    NOT NULL DEFAULT '{}',
        source_id INTEGER NOT NULL DEFAULT 0,
        tool      TEXT    NOT NULL DEFAULT '',
        at        TEXT    NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS artifacts_by_source ON artifacts (source_id);
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_dir() / ARTIFACTS_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(self.SCHEMA)

    @contextmanager
    def _connect(self):
        try:
            db = sqlite3.connect(self.path, timeout=10)
        except sqlite3.Error as exc:
            raise ArtifactError(f"Не открывается база артефактов: {exc}") from exc
        try:
            with db:
                yield db
        except sqlite3.Error as exc:
            raise ArtifactError(f"Ошибка базы артефактов: {exc}") from exc
        finally:
            db.close()

    def put(self, kind: str, title: str, content: str, *, tool: str = "",
            source_id: int = 0, meta: dict | None = None) -> Artifact:
        сейчас = now_iso()
        with self._connect() as db:
            курсор = db.execute(
                "INSERT INTO artifacts (kind, title, content, meta, source_id,"
                " tool, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, title, content, json.dumps(meta or {}, ensure_ascii=False),
                 source_id, tool, сейчас))
            номер = int(курсор.lastrowid)
        return Artifact(id=номер, kind=kind, title=title, content=content,
                        meta=meta or {}, source_id=source_id, tool=tool, at=сейчас)

    def get(self, artifact_id: int) -> Artifact:
        with self._connect() as db:
            строка = db.execute(
                "SELECT id, kind, title, content, meta, source_id, tool, at"
                " FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if not строка:
            raise ArtifactError(f"Нет артефакта #{artifact_id}")
        try:
            мета = json.loads(строка[4] or "{}")
        except json.JSONDecodeError:
            мета = {}
        return Artifact(id=строка[0], kind=строка[1], title=строка[2],
                        content=строка[3], meta=мета, source_id=строка[5],
                        tool=строка[6], at=строка[7])

    def recent(self, *, limit: int = 30) -> "list[Artifact]":
        """Последние артефакты. Метод назван не list намеренно: внутри класса
        такое имя затеняет встроенный list, и аннотации ниже перестают
        работать — ошибка неочевидная и ловится только запуском."""
        with self._connect() as db:
            строки = db.execute(
                "SELECT id, kind, title, content, meta, source_id, tool, at"
                " FROM artifacts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        готово = []
        for с in строки:
            try:
                мета = json.loads(с[4] or "{}")
            except json.JSONDecodeError:
                мета = {}
            готово.append(Artifact(id=с[0], kind=с[1], title=с[2], content=с[3],
                                   meta=мета, source_id=с[5], tool=с[6], at=с[7]))
        return готово

    def chain(self, artifact_id: int) -> list[Artifact]:
        """Цепочка, из которой получен артефакт: от исходного к этому.

        Ради неё в каждом артефакте хранится source_id. Без цепочки нельзя
        показать, ЧТО из чего получилось, — а это и есть главное в пайплайне.
        """
        звенья: list[Artifact] = []
        номер = artifact_id
        видели = set()
        while номер and номер not in видели:
            видели.add(номер)
            try:
                звено = self.get(номер)
            except ArtifactError:
                break
            звенья.append(звено)
            номер = звено.source_id
        return list(reversed(звенья))

    def clear(self) -> int:
        with self._connect() as db:
            сколько = db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
            db.execute("DELETE FROM artifacts")
        return int(сколько)


__all__ = ["ArtifactStore", "Artifact", "ArtifactError", "ARTIFACTS_FILE"]
