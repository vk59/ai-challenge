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


@dataclass
class Summary:
    """Сжатый пересказ вытесненной части диалога (день 9).

    Лежит отдельно от реплик и подставляется в запрос ВМЕСТО них. Хранить
    его обязательно на диске: иначе перезапуск вернёт агента к обрывку
    окна, а всё, что было свёрнуто, пропадёт — то есть сжатие окажется
    способом терять память, а не экономить токены.
    """

    content: str
    at: str = ""
    covered: int = 0         # сколько пар реплик свёрнуто в этот текст
    tokens: int = 0          # во сколько токенов обошлось само сжатие
    rounds: int = 0          # сколько раз пересобирался (сжатие накопительное)

    def as_dict(self) -> dict:
        return {"content": self.content, "at": self.at, "covered": self.covered,
                "tokens": self.tokens, "rounds": self.rounds}

    @classmethod
    def from_dict(cls, raw: dict) -> "Summary":
        return cls(
            content=str(raw.get("content", "")),
            at=str(raw.get("at", "")),
            covered=int(raw.get("covered", 0) or 0),
            tokens=int(raw.get("tokens", 0) or 0),
            rounds=int(raw.get("rounds", 0) or 0),
        )


# Слои памяти (день 11). Отличаются не форматом, а ОБЛАСТЬЮ ЖИЗНИ:
#
#   SHORT   текущий диалог — реплики, живут в сессии, вытесняются окном
#   WORKING данные текущей задачи — факты, живут в сессии, переживают окно
#   LONG    профиль, решения, знания — живут ВНЕ сессий, общие для всех
#
# Граница между WORKING и LONG проходит именно по области: рабочая память
# умирает вместе с задачей, долговременная переезжает в следующий диалог.
# Если бы обе лежали в сессии, разделение было бы косметическим.
SHORT, WORKING, LONG = "short", "working", "long"
LAYERS = (SHORT, WORKING, LONG)

# Подтипы долговременной памяти — три, как просит задание.
PROFILE, DECISION, KNOWLEDGE = "profile", "decision", "knowledge"
LONG_KINDS = (PROFILE, DECISION, KNOWLEDGE)


@dataclass
class Memo:
    """Запись долговременной памяти — живёт вне всякой сессии (день 11)."""

    key: str
    value: str
    kind: str = PROFILE      # profile | decision | knowledge
    at: str = ""
    source: str = ""         # из какого диалога приехало, для проверяемости

    def as_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "kind": self.kind,
                "at": self.at, "source": self.source}

    @classmethod
    def from_dict(cls, raw: dict) -> "Memo":
        return cls(key=str(raw.get("key", "")), value=str(raw.get("value", "")),
                   kind=str(raw.get("kind", PROFILE)), at=str(raw.get("at", "")),
                   source=str(raw.get("source", "")))


@dataclass
class Fact:
    """Один факт из диалога — ключ и значение (день 10).

    Отдельная сущность, а не строка в пересказе: факты перезаписываются
    по ключу. Сказали «бюджет 800 тысяч», потом «бюджет подняли до миллиона» —
    в памяти должно остаться второе, а не оба подряд. Пересказ дня 9 так
    не умеет: он только прирастает.
    """

    key: str
    value: str
    at: str = ""

    def as_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "at": self.at}

    @classmethod
    def from_dict(cls, raw: dict) -> "Fact":
        return cls(key=str(raw.get("key", "")), value=str(raw.get("value", "")),
                   at=str(raw.get("at", "")))


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

    # ── сжатая история (день 9) ─────────────────────────────────────────
    def save_summary(self, session: str, summary: Summary) -> None:
        """Сохранить пересказ. Один на сессию: он накопительный, не список."""
        raise NotImplementedError

    def load_summary(self, session: str) -> Summary | None:
        """Достать пересказ; None — если сессию ещё ни разу не сжимали."""
        raise NotImplementedError

    # ── факты и ветки (день 10) ─────────────────────────────────────────
    def save_facts(self, session: str, facts: list[Fact]) -> None:
        """Переписать набор фактов целиком: они живут как единая карточка."""
        raise NotImplementedError

    def load_facts(self, session: str) -> list[Fact]:
        """Факты сессии; пустой список — если их ещё не извлекали."""
        raise NotImplementedError

    # ── долговременная память, вне сессий (день 11) ─────────────────────
    def remember(self, memo: Memo) -> None:
        """Записать в долговременную память. Ключ уникален глобально."""
        raise NotImplementedError

    def recall(self, kind: str | None = None) -> list[Memo]:
        """Достать долговременную память; kind — отфильтровать по подтипу."""
        raise NotImplementedError

    def forget(self, key: str) -> bool:
        """Забыть одну запись навсегда. True — если было что забывать."""
        raise NotImplementedError

    def fork(self, session: str, branch: str, upto: int) -> int:
        """Ответвить новый диалог от первых upto реплик исходного.

        Реплики копируются, а не связываются ссылкой на родителя. Ссылка
        экономнее, но тогда каждая загрузка ветки означала бы рекурсивный
        обход предков, а удаление родителя ломало бы потомков. Копия —
        это чуть больше байт на диске и полная независимость веток,
        что здесь и требуется: ветки должны расходиться и жить сами.

        Возвращает, сколько реплик скопировано.
        """
        источник = self.load(session)[:upto]
        for turn in источник:
            self.append(branch, turn)
        # Факты и пересказ наследуются: ветка начинается с того же состояния
        # памяти, иначе развилка была бы нечестной.
        факты = self.load_facts(session)
        if факты:
            self.save_facts(branch, факты)
        пересказ = self.load_summary(session)
        if пересказ:
            self.save_summary(branch, пересказ)
        return len(источник)

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

    def save_summary(self, session: str, summary: Summary) -> None:
        with self._lock:
            data = self._read(session)
            data["summary"] = summary.as_dict()
            self._write(session, data)

    def load_summary(self, session: str) -> Summary | None:
        raw = self._read(session).get("summary")
        return Summary.from_dict(raw) if raw else None

    def save_facts(self, session: str, facts: list[Fact]) -> None:
        with self._lock:
            data = self._read(session)
            data["facts"] = [f.as_dict() for f in facts]
            self._write(session, data)

    def load_facts(self, session: str) -> list[Fact]:
        return [Fact.from_dict(r) for r in self._read(session).get("facts") or []]

    # Долговременная память в отдельном файле, а не в файле сессии — она
    # и по смыслу вне сессий, и физически должна лежать отдельно, иначе
    # удаление диалога унесло бы с собой профиль пользователя.
    def _long_path(self) -> Path:
        return self.dir / "_longterm.json"

    def remember(self, memo: Memo) -> None:
        with self._lock:
            записи = {m.key: m for m in self._read_long()}
            записи[memo.key] = memo
            self._write_long(list(записи.values()))

    def recall(self, kind: str | None = None) -> list[Memo]:
        записи = self._read_long()
        if kind:
            записи = [m for m in записи if m.kind == kind]
        return sorted(записи, key=lambda m: (m.kind, m.key))

    def forget(self, key: str) -> bool:
        with self._lock:
            записи = self._read_long()
            осталось = [m for m in записи if m.key != key]
            if len(осталось) == len(записи):
                return False
            self._write_long(осталось)
            return True

    def _read_long(self) -> list[Memo]:
        try:
            raw = json.loads(self._long_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as exc:
            raise MemoryError_(f"Долговременная память повреждена: {exc}") from exc
        return [Memo.from_dict(r) for r in raw.get("memos") or []]

    def _write_long(self, записи: list[Memo]) -> None:
        путь = self._long_path()
        tmp = путь.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps({"memos": [m.as_dict() for m in записи]},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, путь)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise MemoryError_(f"Не пишется долговременная память: {exc}") from exc

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

    -- Сжатая история (день 9). Отдельная таблица, а не строка в turns:
    -- пересказ не является репликой диалога и в модель уезжает иначе —
    -- рядом с ролью, а не в списке сообщений.
    -- IF NOT EXISTS делает миграцию баз дней 7-8 бесплатной.
    CREATE TABLE IF NOT EXISTS summaries (
        session  TEXT    PRIMARY KEY,
        content  TEXT    NOT NULL,
        at       TEXT    NOT NULL DEFAULT '',
        covered  INTEGER NOT NULL DEFAULT 0,
        tokens   INTEGER NOT NULL DEFAULT 0,
        rounds   INTEGER NOT NULL DEFAULT 0
    );

    -- Факты «ключ-значение» (день 10). Ключ уникален в пределах сессии:
    -- новое значение вытесняет старое, в этом весь смысл — память должна
    -- обновляться, а не прирастать.
    CREATE TABLE IF NOT EXISTS facts (
        session  TEXT    NOT NULL,
        key      TEXT    NOT NULL,
        value    TEXT    NOT NULL,
        at       TEXT    NOT NULL DEFAULT '',
        PRIMARY KEY (session, key)
    );

    -- Долговременная память (день 11). Обратите внимание: колонки session
    -- здесь НЕТ, и это главное отличие слоя. Профиль, решения и знания
    -- общие для всех диалогов и переезжают из одного в другой.
    CREATE TABLE IF NOT EXISTS longterm (
        key      TEXT    PRIMARY KEY,
        value    TEXT    NOT NULL,
        kind     TEXT    NOT NULL DEFAULT 'profile',
        at       TEXT    NOT NULL DEFAULT '',
        source   TEXT    NOT NULL DEFAULT ''
    );
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
            # Пересказ тоже: иначе «забыть» оставило бы на диске выжимку
            # из стёртого диалога, и она всплыла бы при следующем запуске.
            db.execute("DELETE FROM summaries WHERE session = ?", (session,))
            db.execute("DELETE FROM facts WHERE session = ?", (session,))

    def save_summary(self, session: str, summary: Summary) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO summaries (session, content, at, covered, tokens, rounds)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(session) DO UPDATE SET"
                "   content = excluded.content, at = excluded.at,"
                "   covered = excluded.covered, tokens = excluded.tokens,"
                "   rounds = excluded.rounds",
                (session, summary.content, summary.at, summary.covered,
                 summary.tokens, summary.rounds),
            )

    def load_summary(self, session: str) -> Summary | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT content, at, covered, tokens, rounds FROM summaries"
                " WHERE session = ?",
                (session,),
            ).fetchone()
        return Summary(*row) if row else None

    def save_facts(self, session: str, facts: list[Fact]) -> None:
        with self._connect() as db:
            # Карточка фактов переписывается целиком: так исчезнувший из
            # диалога факт не остаётся висеть на диске навсегда.
            db.execute("DELETE FROM facts WHERE session = ?", (session,))
            db.executemany(
                "INSERT INTO facts (session, key, value, at) VALUES (?, ?, ?, ?)",
                [(session, f.key, f.value, f.at) for f in facts],
            )

    def load_facts(self, session: str) -> list[Fact]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT key, value, at FROM facts WHERE session = ? ORDER BY key",
                (session,),
            ).fetchall()
        return [Fact(*row) for row in rows]

    def remember(self, memo: Memo) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO longterm (key, value, kind, at, source)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                "   kind = excluded.kind, at = excluded.at, source = excluded.source",
                (memo.key, memo.value, memo.kind, memo.at, memo.source),
            )

    def recall(self, kind: str | None = None) -> list[Memo]:
        with self._connect() as db:
            if kind:
                rows = db.execute(
                    "SELECT key, value, kind, at, source FROM longterm"
                    " WHERE kind = ? ORDER BY key", (kind,)).fetchall()
            else:
                rows = db.execute(
                    "SELECT key, value, kind, at, source FROM longterm"
                    " ORDER BY kind, key").fetchall()
        return [Memo(*row) for row in rows]

    def forget(self, key: str) -> bool:
        with self._connect() as db:
            cur = db.execute("DELETE FROM longterm WHERE key = ?", (key,))
            return cur.rowcount > 0

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
           "Summary", "Fact", "Memo", "open_store", "default_dir", "now_iso",
           "local_time", "DEFAULT_SESSION", "ENV_DIR_VAR", "MemoryError_",
           "SHORT", "WORKING", "LONG", "LAYERS",
           "PROFILE", "DECISION", "KNOWLEDGE", "LONG_KINDS"]
