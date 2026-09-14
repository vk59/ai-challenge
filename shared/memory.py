"""Хранилище диалога — то, что переживает выключение процесса.

День 6 держал историю в списке внутри объекта: закрыл программу — забыл всё.
Здесь появляется диск, и вместе с ним развилка, которой раньше не было:

    окно контекста  — что уезжает в модель, ограничено memory_turns
    архив           — что лежит на диске, не ограничено ничем

В дне 6 это было одно и то же, и `_trim()` уничтожал старые реплики навсегда.
Теперь подрезка касается только окна: на диске остаётся весь разговор.

Две реализации одного интерфейса — задание разрешает «JSON или SQLite»,
и разница между ними как раз в том, что происходит на каждой реплике:

    JsonStore    прочитать файл целиком → дописать → записать файл целиком
    SqliteStore  INSERT одной строки

Подробный разбор — в README дня 7. Обе используют только стандартную
библиотеку: внешних зависимостей в репозитории по-прежнему нет.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Каталог хранилища задаётся снаружи — тем же приёмом, что и .env в дне 6.
# Нативному приложению это обязательно: macOS не пускает .app в ~/Documents,
# поэтому build_app.sh уводит хранилище в ~/Library/Application Support.
ENV_DIR_VAR = "AI_ADVENT_MEMORY_DIR"

DEFAULT_SESSION = "default"
SQLITE_FILE = "agent.db"


class MemoryError_(Exception):
    """Понятная человеку ошибка работы с хранилищем."""


def now_iso() -> str:
    """Момент времени в UTC, в виде, пригодном и для JSON, и для сортировки."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_time(raw: str) -> str:
    """UTC с диска → местное время для показа человеку.

    Хранить в UTC, показывать в местном — обычное разделение, но забыть перевод
    в одном из интерфейсов легко: тогда список диалогов покажет 08:40, а сама
    переписка 11:40, и это будет одно и то же событие. Поэтому перевод один,
    и пользуются им все.
    """
    try:
        return datetime.fromisoformat(raw).astimezone().strftime("%d.%m %H:%M")
    except (ValueError, TypeError):
        return ""


def default_dir() -> Path:
    """Куда складывать историю, если каталог не указан явно."""
    raw = os.environ.get(ENV_DIR_VAR)
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[1] / "memory"


@dataclass
class Turn:
    """Одна реплика — то, что кладётся на диск.

    Сообщением для API является только пара role/content; остальное —
    наша бухгалтерия, в модель она не уезжает.
    """

    role: str                # user | assistant
    content: str
    at: str                  # когда произошло, ISO-8601 UTC
    tokens: int = 0          # у user — prompt_tokens, у assistant — completion_tokens
    seconds: float = 0.0     # сколько ждали ответ (заполнено у assistant)

    @property
    def message(self) -> dict:
        """Ровно то, что понимает API: лишние поля туда отправлять нельзя."""
        return {"role": self.role, "content": self.content}

    @property
    def when(self) -> str:
        """Время в местной зоне, коротко — для интерфейсов."""
        return local_time(self.at)

    def as_dict(self) -> dict:
        return {"role": self.role, "content": self.content, "at": self.at,
                "tokens": self.tokens, "seconds": self.seconds}

    @classmethod
    def from_dict(cls, raw: dict) -> "Turn":
        return cls(
            role=str(raw.get("role", "user")),
            content=str(raw.get("content", "")),
            at=str(raw.get("at", "")),
            tokens=int(raw.get("tokens", 0) or 0),
            seconds=float(raw.get("seconds", 0.0) or 0.0),
        )


@dataclass
class SessionInfo:
    """Сводка по одному сохранённому диалогу — для списка сессий."""

    name: str
    turns: int               # реплик всего (и вопросы, и ответы)
    tokens: int
    started: str
    updated: str

    @property
    def pairs(self) -> int:
        """Пар «вопрос-ответ» — в них удобнее считать глубину диалога."""
        return self.turns // 2

    @property
    def when(self) -> str:
        """Когда в диалог писали последний раз, в местной зоне."""
        return local_time(self.updated)


@dataclass
class Totals:
    """Всё, что накопилось в сессии за всё время, включая прошлые запуски."""

    turns: int = 0           # реплик пользователя, то есть пар
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0


class Store:
    """Общий интерфейс хранилища. Агент знает только его, не реализацию."""

    kind = "?"
    label = "?"

    def append(self, session: str, turn: Turn) -> None:
        """Дописать одну реплику. Вызывается сразу, а не при выходе."""
        raise NotImplementedError

    def load(self, session: str, limit: int | None = None) -> list[Turn]:
        """Достать реплики; limit — сколько последних (None — все)."""
        raise NotImplementedError

    def totals(self, session: str) -> Totals:
        """Суммарный расход по сессии за всю её историю."""
        raise NotImplementedError

    def sessions(self) -> list[SessionInfo]:
        """Все сохранённые диалоги, свежие сверху."""
        raise NotImplementedError

    def clear(self, session: str) -> None:
        """Стереть сессию. Забыть — значит забыть и на диске тоже."""
        raise NotImplementedError

    # ── общее для реализаций ────────────────────────────────────────────
    @staticmethod
    def _aligned(turns: list[Turn]) -> list[Turn]:
        """Окно не должно начинаться с ответа модели.

        Срез последних N реплик может разрезать пару пополам, и тогда диалог
        для API начинается с assistant — формально допустимо, но выглядит так,
        будто модель заговорила первой. Отрезаем висящий хвост.
        """
        if turns and turns[0].role == "assistant":
            return turns[1:]
        return turns

    @staticmethod
    def _totals_of(turns: list[Turn]) -> Totals:
        totals = Totals()
        for turn in turns:
            if turn.role == "user":
                totals.turns += 1
                totals.prompt_tokens += turn.tokens
            else:
                totals.completion_tokens += turn.tokens
                totals.seconds += turn.seconds
        totals.seconds = round(totals.seconds, 1)
        return totals


class JsonStore(Store):
    """Диалог в JSON-файле: по файлу на сессию, целиком читаемый глазами.

    Главный плюс — прозрачность: `cat memory/default.json` показывает всё,
    что помнит агент. Главный минус — цена дозаписи: JSON не умеет «дописать
    в конец», поэтому каждая реплика переписывает файл целиком. На сотне
    реплик незаметно, на десятках тысяч — уже нет.

    Запись атомарная: сначала во временный файл рядом, потом os.replace.
    Без этого обрыв на середине записи (Ctrl+C, разряженный ноутбук) оставил
    бы обрезанный JSON, то есть потерю всего диалога, а не последней реплики.
    """

    kind = "json"
    label = "JSON-файл"

    def __init__(self, directory: Path | str | None = None) -> None:
        self.dir = Path(directory) if directory else default_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        # Читаем-меняем-пишем неатомарно по своей природе, а веб-сервер
        # многопоточный: без замка две одновременные реплики затрут друг друга.
        self._lock = threading.Lock()

    def __str__(self) -> str:
        return f"{self.label} · {self.dir}"

    # ── интерфейс ───────────────────────────────────────────────────────
    def append(self, session: str, turn: Turn) -> None:
        with self._lock:
            data = self._read(session)
            data["turns"].append(turn.as_dict())
            data["updated"] = turn.at
            data.setdefault("started", turn.at)
            self._write(session, data)

    def load(self, session: str, limit: int | None = None) -> list[Turn]:
        turns = [Turn.from_dict(raw) for raw in self._read(session)["turns"]]
        if limit is not None and len(turns) > limit:
            turns = self._aligned(turns[-limit:])
        return turns

    def totals(self, session: str) -> Totals:
        return self._totals_of(self.load(session))

    def sessions(self) -> list[SessionInfo]:
        found = []
        for path in self.dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            turns = data.get("turns") or []
            found.append(SessionInfo(
                name=str(data.get("session") or path.stem),
                turns=len(turns),
                tokens=sum(int(t.get("tokens", 0) or 0) for t in turns),
                started=str(data.get("started") or ""),
                updated=str(data.get("updated") or ""),
            ))
        return sorted(found, key=lambda s: s.updated, reverse=True)

    def clear(self, session: str) -> None:
        with self._lock:
            self._path(session).unlink(missing_ok=True)

    # ── внутреннее ──────────────────────────────────────────────────────
    def _path(self, session: str) -> Path:
        return self.dir / f"{_safe_name(session)}.json"

    def _read(self, session: str) -> dict:
        path = self._path(session)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"session": session, "started": "", "updated": "", "turns": []}
        except json.JSONDecodeError as exc:
            raise MemoryError_(f"Файл истории повреждён: {path} ({exc})") from exc
        except OSError as exc:
            raise MemoryError_(f"Не читается файл истории {path}: {exc}") from exc

        data.setdefault("session", session)
        data.setdefault("turns", [])
        return data

    def _write(self, session: str, data: dict) -> None:
        path = self._path(session)
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)          # атомарная подмена в пределах ФС
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise MemoryError_(f"Не пишется файл истории {path}: {exc}") from exc


class SqliteStore(Store):
    """Диалог в SQLite: все сессии в одной таблице, дозапись — один INSERT.

    Файл перестаёт читаться глазами, зато исчезают ровно те проблемы, которые
    у JSON приходится решать руками: запись одной реплики не трогает остальные,
    блокировки и атомарность транзакции — забота движка, а выборка последних N
    реплик делается запросом, а не чтением всего диалога в память.

    sqlite3 — часть стандартной библиотеки, ставить ничего не нужно.
    """

    kind = "sqlite"
    label = "SQLite"

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS turns (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        session  TEXT    NOT NULL,
        role     TEXT    NOT NULL,
        content  TEXT    NOT NULL,
        at       TEXT    NOT NULL,
        tokens   INTEGER NOT NULL DEFAULT 0,
        seconds  REAL    NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS turns_by_session ON turns (session, id);
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_dir() / SQLITE_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(self.SCHEMA)

    def __str__(self) -> str:
        return f"{self.label} · {self.path}"

    # ── интерфейс ───────────────────────────────────────────────────────
    def append(self, session: str, turn: Turn) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO turns (session, role, content, at, tokens, seconds)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session, turn.role, turn.content, turn.at, turn.tokens, turn.seconds),
            )

    def load(self, session: str, limit: int | None = None) -> list[Turn]:
        with self._connect() as db:
            if limit is None:
                rows = db.execute(
                    "SELECT role, content, at, tokens, seconds FROM turns"
                    " WHERE session = ? ORDER BY id",
                    (session,),
                ).fetchall()
                return [Turn(*row) for row in rows]

            # Последние N — выбираем с конца и разворачиваем: так в память
            # не поднимается весь диалог ради десяти реплик.
            rows = db.execute(
                "SELECT role, content, at, tokens, seconds FROM turns"
                " WHERE session = ? ORDER BY id DESC LIMIT ?",
                (session, limit),
            ).fetchall()
        return self._aligned([Turn(*row) for row in reversed(rows)])

    def totals(self, session: str) -> Totals:
        with self._connect() as db:
            row = db.execute(
                "SELECT"
                "  SUM(role = 'user'),"
                "  SUM(CASE WHEN role = 'user' THEN tokens ELSE 0 END),"
                "  SUM(CASE WHEN role = 'assistant' THEN tokens ELSE 0 END),"
                "  SUM(seconds)"
                " FROM turns WHERE session = ?",
                (session,),
            ).fetchone()
        return Totals(
            turns=int(row[0] or 0),
            prompt_tokens=int(row[1] or 0),
            completion_tokens=int(row[2] or 0),
            seconds=round(float(row[3] or 0.0), 1),
        )

    def sessions(self) -> list[SessionInfo]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT session, COUNT(*), SUM(tokens), MIN(at), MAX(at)"
                " FROM turns GROUP BY session ORDER BY MAX(at) DESC"
            ).fetchall()
        return [
            SessionInfo(name=row[0], turns=int(row[1] or 0), tokens=int(row[2] or 0),
                        started=row[3] or "", updated=row[4] or "")
            for row in rows
        ]

    def clear(self, session: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM turns WHERE session = ?", (session,))

    # ── внутреннее ──────────────────────────────────────────────────────
    @contextmanager
    def _connect(self):
        """Соединение на операцию: так хранилище безопасно из любого потока.

        Держать одно соединение на весь процесс было бы чуть быстрее, но
        потребовало бы check_same_thread=False и своего замка — а веб-сервер
        здесь многопоточный. Открытие файла стоит микросекунды, не тот случай,
        где имеет смысл экономить.
        """
        try:
            db = sqlite3.connect(self.path, timeout=10)
        except sqlite3.Error as exc:
            raise MemoryError_(f"Не открывается база {self.path}: {exc}") from exc
        try:
            with db:                      # коммит на выходе, откат при ошибке
                yield db
        except sqlite3.Error as exc:
            raise MemoryError_(f"Ошибка базы {self.path}: {exc}") from exc
        finally:
            db.close()


def open_store(kind: str = "sqlite", location: Path | str | None = None) -> Store:
    """Фабрика: `--store json` или `--store sqlite` из интерфейсов."""
    kind = (kind or "").strip().lower()
    if kind == "json":
        return JsonStore(location)
    if kind in ("sqlite", "sql", "db"):
        return SqliteStore(location)
    raise MemoryError_(f"Неизвестное хранилище: {kind!r}. Ожидалось json или sqlite")


def _safe_name(session: str) -> str:
    """Имя сессии → имя файла. Кириллица проходит как есть, мусор — нет."""
    cleaned = "".join(
        char if (char.isalnum() or char in "-_") else "-"
        for char in session.strip()
    ).strip("-")
    return cleaned or DEFAULT_SESSION


__all__ = ["Store", "JsonStore", "SqliteStore", "Turn", "SessionInfo", "Totals",
           "open_store", "default_dir", "now_iso", "local_time", "DEFAULT_SESSION",
           "ENV_DIR_VAR", "MemoryError_"]
