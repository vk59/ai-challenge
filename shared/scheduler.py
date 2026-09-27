"""Планировщик фоновых задач (день 18).

Задачи и результаты живут в SQLite, поэтому переживают перезапуск. Пока
процесс работает, фоновый поток выполняет всё, чему подошёл срок.

Про «24/7» здесь надо говорить честно. Поток живёт ровно столько, сколько
живёт процесс: закрыли приложение — фон встал. Поэтому у планировщика есть
второй механизм, наверстывание: при старте он смотрит, чему срок уже вышел,
и выполняет это.

Важная тонкость наверстывания. Если приложение не запускали три дня, а
задача ежечасная, выполнять её семьдесят два раза подряд — бессмысленно
и дорого. Она выполняется ОДИН раз и честно сообщает, сколько периодов
пропущено. Для сводок это правильно: нужна свежая сводка, а не семьдесят
две старых.

Настоящий круглосуточный фон — это launchd-агент, он ставится отдельно
(см. день 18, install_agent.sh).

    планировщик = Scheduler()
    планировщик.register("digest", собрать_сводку)
    планировщик.add_task("Сводка по репозиторию", "digest", 3600)
    планировщик.start()
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from memory import default_dir, local_time, now_iso

SCHEDULER_FILE = "scheduler.db"

# Как часто поток просыпается посмотреть, не пора ли что-то делать.
# Раз в пять секунд: достаточно точно для задач от минуты и не греет процессор.
ТИК = 5.0

# Сколько прогонов хранить на задачу. Без потолка таблица растёт вечно,
# а для сводки нужны последние, а не все за год.
ХРАНИТЬ_ПРОГОНОВ = 50


class SchedulerError(Exception):
    """Понятная человеку ошибка планировщика."""


def _разобрать(сырое: str, пусто: str = "{}"):
    try:
        return json.loads(сырое or пусто)
    except json.JSONDecodeError:
        return json.loads(пусто)


@dataclass
class Run:
    """Один прогон задачи."""

    id: int = 0
    task_id: int = 0
    at: str = ""
    ok: bool = True
    summary: str = ""
    payload: dict = field(default_factory=dict)
    seconds: float = 0.0
    missed: int = 0          # сколько периодов пропущено перед этим прогоном

    @property
    def when(self) -> str:
        return local_time(self.at)


@dataclass
class Task:
    """Задача по расписанию."""

    id: int = 0
    name: str = ""
    kind: str = ""
    interval: int = 3600         # секунд между запусками
    params: dict = field(default_factory=dict)
    enabled: bool = True
    created_at: str = ""
    last_run_at: str = ""
    next_run_at: str = ""
    runs_count: int = 0

    @property
    def due_in(self) -> float:
        """Сколько секунд до следующего запуска. Отрицательное — просрочено."""
        if not self.next_run_at:
            return 0.0
        try:
            срок = datetime.fromisoformat(self.next_run_at)
        except ValueError:
            return 0.0
        return (срок - datetime.now(timezone.utc)).total_seconds()

    @property
    def due_pretty(self) -> str:
        if not self.enabled:
            return "выключена"
        осталось = self.due_in
        if осталось <= 0:
            return "пора запускать"
        if осталось < 90:
            return f"через {int(осталось)} с"
        if осталось < 5400:
            return f"через {int(осталось // 60)} мин"
        return f"через {осталось / 3600:.1f} ч"

    @property
    def interval_pretty(self) -> str:
        # «каждые 60 с» формально верно, но человек говорит «каждую минуту».
        # Интервал виден в интерфейсе, поэтому читаемость тут не украшение.
        с = self.interval
        if с < 60:
            return f"каждые {с} с"
        if с == 60:
            return "каждую минуту"
        if с < 3600:
            минут = с // 60
            хвост = f" {с % 60} с" if с % 60 else ""
            return f"каждые {минут} мин{хвост}"
        if с == 3600:
            return "каждый час"
        if с < 86400:
            часов = с / 3600
            return f"каждые {часов:.0f} ч" if часов == int(часов) \
                else f"каждые {часов:.1f} ч"
        if с == 86400:
            return "раз в сутки"
        return f"каждые {с / 86400:.0f} дн"

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "interval": self.interval, "params": self.params,
                "enabled": self.enabled, "created_at": self.created_at,
                "last_run_at": self.last_run_at, "next_run_at": self.next_run_at,
                "runs_count": self.runs_count, "due": self.due_pretty,
                "every": self.interval_pretty,
                "last_when": local_time(self.last_run_at)}


class Scheduler:
    """Хранит задачи, выполняет их по сроку и копит результаты."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS tasks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL,
        kind        TEXT    NOT NULL,
        interval    INTEGER NOT NULL DEFAULT 3600,
        params      TEXT    NOT NULL DEFAULT '{}',
        enabled     INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT    NOT NULL DEFAULT '',
        last_run_at TEXT    NOT NULL DEFAULT '',
        next_run_at TEXT    NOT NULL DEFAULT '',
        runs_count  INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS runs (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id  INTEGER NOT NULL,
        at       TEXT    NOT NULL,
        ok       INTEGER NOT NULL DEFAULT 1,
        summary  TEXT    NOT NULL DEFAULT '',
        payload  TEXT    NOT NULL DEFAULT '{}',
        seconds  REAL    NOT NULL DEFAULT 0,
        missed   INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS runs_by_task ON runs (task_id, id);
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_dir() / SCHEDULER_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(self.SCHEMA)
        self._исполнители: dict[str, callable] = {}
        self._поток: threading.Thread | None = None
        self._стоп = threading.Event()
        # Замок на выполнение: фоновый поток и «запустить сейчас» из интерфейса
        # не должны выполнять одну задачу одновременно.
        self._замок = threading.Lock()

    @contextmanager
    def _connect(self):
        try:
            db = sqlite3.connect(self.path, timeout=10)
        except sqlite3.Error as exc:
            raise SchedulerError(f"Не открывается база задач: {exc}") from exc
        try:
            with db:
                yield db
        except sqlite3.Error as exc:
            raise SchedulerError(f"Ошибка базы задач: {exc}") from exc
        finally:
            db.close()

    # ── регистрация исполнителей ────────────────────────────────────────
    def register(self, kind: str, func) -> None:
        """Связать тип задачи с функцией, которая её делает.

        Функция принимает (params: dict) и возвращает (сводка: str, данные: dict).
        """
        self._исполнители[kind] = func

    @property
    def kinds(self) -> list[str]:
        return sorted(self._исполнители)

    # ── управление задачами ─────────────────────────────────────────────
    def add_task(self, name: str, kind: str, interval: int,
                 params: dict | None = None, *, start_now: bool = False) -> Task:
        name = (name or "").strip() or kind
        if kind not in self._исполнители:
            raise SchedulerError(f"Неизвестный тип задачи: {kind}. "
                                 f"Доступны: {', '.join(self.kinds) or '—'}")
        if interval < 10:
            raise SchedulerError("Интервал меньше десяти секунд не имеет смысла")

        сейчас = datetime.now(timezone.utc)
        следующий = сейчас if start_now else сейчас.timestamp() + interval
        следующий_iso = (сейчас.isoformat(timespec="seconds") if start_now
                         else datetime.fromtimestamp(следующий, timezone.utc)
                         .isoformat(timespec="seconds"))
        with self._connect() as db:
            курсор = db.execute(
                "INSERT INTO tasks (name, kind, interval, params, enabled,"
                " created_at, next_run_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (name, kind, interval, json.dumps(params or {}, ensure_ascii=False),
                 now_iso(), следующий_iso))
            номер = int(курсор.lastrowid)
        return self.task(номер)

    def task(self, task_id: int) -> Task | None:
        with self._connect() as db:
            строка = db.execute(
                "SELECT id, name, kind, interval, params, enabled, created_at,"
                " last_run_at, next_run_at, runs_count FROM tasks WHERE id = ?",
                (task_id,)).fetchone()
        return self._в_задачу(строка) if строка else None

    def tasks(self) -> list[Task]:
        with self._connect() as db:
            строки = db.execute(
                "SELECT id, name, kind, interval, params, enabled, created_at,"
                " last_run_at, next_run_at, runs_count FROM tasks ORDER BY id"
            ).fetchall()
        return [self._в_задачу(с) for с in строки]

    @staticmethod
    def _в_задачу(с) -> Task:
        return Task(id=с[0], name=с[1], kind=с[2], interval=с[3],
                    params=_разобрать(с[4]), enabled=bool(с[5]), created_at=с[6],
                    last_run_at=с[7], next_run_at=с[8], runs_count=с[9])

    def toggle(self, task_id: int, enabled: bool) -> bool:
        with self._connect() as db:
            изменено = db.execute(
                "UPDATE tasks SET enabled = ?, next_run_at = CASE WHEN ? THEN ?"
                " ELSE next_run_at END WHERE id = ?",
                (int(enabled), int(enabled), self._через(0), task_id)).rowcount
        return изменено > 0

    def update(self, task_id: int, *, name: str | None = None,
               interval: int | None = None, params: dict | None = None) -> bool:
        текущая = self.task(task_id)
        if текущая is None:
            return False
        if interval is not None and interval < 10:
            raise SchedulerError("Интервал меньше десяти секунд не имеет смысла")
        with self._connect() as db:
            db.execute(
                "UPDATE tasks SET name = ?, interval = ?, params = ?,"
                " next_run_at = ? WHERE id = ?",
                (name if name is not None else текущая.name,
                 interval if interval is not None else текущая.interval,
                 json.dumps(params if params is not None else текущая.params,
                            ensure_ascii=False),
                 # Сдвигаем срок от текущего момента: менять интервал и оставлять
                 # старый срок — значит получить неожиданный запуск.
                 self._через(interval if interval is not None else текущая.interval),
                 task_id))
        return True

    def delete(self, task_id: int) -> bool:
        with self._connect() as db:
            db.execute("DELETE FROM runs WHERE task_id = ?", (task_id,))
            return db.execute("DELETE FROM tasks WHERE id = ?",
                              (task_id,)).rowcount > 0

    @staticmethod
    def _через(секунд: float) -> str:
        миг = datetime.now(timezone.utc).timestamp() + секунд
        return datetime.fromtimestamp(миг, timezone.utc).isoformat(timespec="seconds")

    # ── выполнение ──────────────────────────────────────────────────────
    def run_task(self, task_id: int, *, missed: int = 0) -> Run:
        """Выполняет задачу немедленно и записывает результат."""
        задача = self.task(task_id)
        if задача is None:
            raise SchedulerError(f"Нет задачи с номером {task_id}")
        исполнитель = self._исполнители.get(задача.kind)
        if исполнитель is None:
            raise SchedulerError(f"Некому выполнять «{задача.kind}»")

        with self._замок:
            начало = time.monotonic()
            try:
                сводка, данные = исполнитель(задача.params)
                ок = True
            except Exception as exc:                      # noqa: BLE001
                # Упавшая задача не должна ронять планировщик: она просто
                # записывает неудачный прогон и ждёт следующего срока.
                сводка, данные, ок = f"Ошибка: {exc}", {}, False
            потрачено = round(time.monotonic() - начало, 2)

            сейчас = now_iso()
            with self._connect() as db:
                db.execute(
                    "INSERT INTO runs (task_id, at, ok, summary, payload,"
                    " seconds, missed) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (task_id, сейчас, int(ок), str(сводка)[:4000],
                     json.dumps(данные or {}, ensure_ascii=False), потрачено,
                     missed))
                db.execute(
                    "UPDATE tasks SET last_run_at = ?, next_run_at = ?,"
                    " runs_count = runs_count + 1 WHERE id = ?",
                    (сейчас, self._через(задача.interval), task_id))
                # Подрезаем историю прогонов
                db.execute(
                    "DELETE FROM runs WHERE task_id = ? AND id NOT IN ("
                    "  SELECT id FROM runs WHERE task_id = ? ORDER BY id DESC"
                    "  LIMIT ?)", (task_id, task_id, ХРАНИТЬ_ПРОГОНОВ))
        return self.runs(task_id, limit=1)[0]

    def tick(self) -> list[Run]:
        """Выполняет всё, чему вышел срок. Возвращает состоявшиеся прогоны."""
        сделано = []
        for задача in self.tasks():
            if not задача.enabled or задача.due_in > 0:
                continue
            # Сколько периодов пропустили, пока процесс не работал.
            пропущено = 0
            if задача.interval > 0 and задача.due_in < -задача.interval:
                пропущено = int(-задача.due_in // задача.interval)
            try:
                сделано.append(self.run_task(задача.id, missed=пропущено))
            except SchedulerError:
                continue
        return сделано

    # ── фоновый поток ───────────────────────────────────────────────────
    def start(self) -> None:
        if self._поток is not None:
            return
        self._стоп.clear()
        self._поток = threading.Thread(target=self._крутиться, daemon=True)
        self._поток.start()

    def stop(self) -> None:
        self._стоп.set()
        self._поток = None

    def _крутиться(self) -> None:
        # Первый тик сразу: это и есть наверстывание пропущенного за время,
        # пока процесс не работал.
        while not self._стоп.is_set():
            try:
                self.tick()
            except Exception:                             # noqa: BLE001
                pass
            self._стоп.wait(ТИК)

    @property
    def running(self) -> bool:
        return self._поток is not None and self._поток.is_alive()

    # ── результаты ──────────────────────────────────────────────────────
    def runs(self, task_id: int, *, limit: int = 20) -> list[Run]:
        with self._connect() as db:
            строки = db.execute(
                "SELECT id, task_id, at, ok, summary, payload, seconds, missed"
                " FROM runs WHERE task_id = ? ORDER BY id DESC LIMIT ?",
                (task_id, limit)).fetchall()
        return [Run(id=с[0], task_id=с[1], at=с[2], ok=bool(с[3]), summary=с[4],
                    payload=_разобрать(с[5]), seconds=с[6], missed=с[7])
                for с in строки]

    def digest(self, task_id: int, *, limit: int = 20) -> dict:
        """Агрегированный результат: не последний прогон, а картина по всем."""
        задача = self.task(task_id)
        if задача is None:
            raise SchedulerError(f"Нет задачи с номером {task_id}")
        прогоны = self.runs(task_id, limit=limit)
        удачных = sum(1 for п in прогоны if п.ok)
        пропущено = sum(п.missed for п in прогоны)
        среднее = (round(sum(п.seconds for п in прогоны) / len(прогоны), 2)
                   if прогоны else 0.0)
        return {
            "task": задача.as_dict(),
            "runs": len(прогоны),
            "ok": удачных,
            "failed": len(прогоны) - удачных,
            "missed_periods": пропущено,
            "avg_seconds": среднее,
            "last": прогоны[0].summary if прогоны else "",
            "history": [{"when": п.when, "ok": п.ok, "summary": п.summary[:200],
                         "missed": п.missed, "seconds": п.seconds}
                        for п in прогоны],
        }


__all__ = ["Scheduler", "Task", "Run", "SchedulerError", "SCHEDULER_FILE"]
