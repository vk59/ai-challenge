#!/usr/bin/env python3
"""MCP-сервер с планировщиком фоновых задач (день 18).

Отдаёт наружу управление задачами: завести, настроить, выключить, выполнить
сейчас, посмотреть накопленную сводку. Сами задачи выполняются фоновым
потоком по расписанию, данные копятся в SQLite.

Три типа задач, все настраиваемые:

    repo_digest    сводка по репозиторию: коммиты за период
    token_report   расход токенов и денег по диалогам агента
    reminder       напоминание с текстом

Про «24/7» честно: поток живёт, пока живёт процесс. Закрыли приложение —
фон встал, но при следующем запуске сработает наверстывание. Настоящий
круглосуточный фон ставится отдельно, см. install_agent.sh.
"""

import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from memory import open_store  # noqa: E402
from scheduler import Scheduler, SchedulerError  # noqa: E402
from tokens import money  # noqa: E402

КОРЕНЬ = Path(os.environ.get("AI_ADVENT_REPO")
              or Path(__file__).resolve().parents[2])
ЗЕРКАЛО = os.environ.get("AI_ADVENT_REPO_MIRROR") or ""
ИСТОЧНИК = Path(ЗЕРКАЛО) if ЗЕРКАЛО and Path(ЗЕРКАЛО).is_dir() else КОРЕНЬ / ".git"

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "ai-advent-scheduler"
SERVER_VERSION = "1.0"


def log(*части) -> None:
    print(*части, file=sys.stderr, flush=True)


def git(*аргументы: str) -> str:
    """Тот же приём, что в дне 17: не зависим от рабочей директории."""
    команда = ["git", "--git-dir", str(ИСТОЧНИК), *аргументы]
    готово = subprocess.run(команда, cwd="/tmp", capture_output=True,
                            text=True, timeout=30)
    if готово.returncode != 0:
        raise ValueError(f"git: {готово.stderr.strip()[:160]}")
    return готово.stdout


# ── что умеют делать задачи ─────────────────────────────────────────────
def исполнить_repo_digest(params: dict) -> tuple[str, dict]:
    """Сводка по репозиторию за последние N дней."""
    дней = int(params.get("days") or 7)
    с_какого = (datetime.now(timezone.utc) - timedelta(days=дней)
                ).strftime("%Y-%m-%d")
    строки = git("log", f"--since={с_какого}", "--format=%ad|%s",
                 "--date=short").strip().splitlines()
    if not строки:
        return f"За {дней} дн. коммитов нет.", {"commits": 0, "days": дней}

    по_датам = Counter(с.split("|", 1)[0] for с in строки)
    темы = [с.split("|", 1)[1] for с in строки]
    самый = по_датам.most_common(1)[0]
    сводка = (f"За {дней} дн.: {len(строки)} коммитов в {len(по_датам)} дней. "
              f"Плотнее всего {самый[0]} — {самый[1]}. "
              f"Последнее: «{темы[0][:60]}»")
    return сводка, {"commits": len(строки), "days": дней,
                    "by_date": dict(по_датам), "last": темы[0]}


def исполнить_token_report(params: dict) -> tuple[str, dict]:
    """Сколько потрачено токенов и денег по всем диалогам."""
    хранилище = open_store("sqlite")
    сессии = хранилище.sessions()
    if not сессии:
        return "Диалогов пока нет.", {"sessions": 0}
    всего = sum(с.tokens for с in сессии)
    пар = sum(с.pairs for с in сессии)
    топ = max(сессии, key=lambda с: с.tokens)
    модель = params.get("model") or "deepseek-v4-flash"
    from tokens import cost
    деньги = cost(модель, всего, 0)
    сводка = (f"{len(сессии)} диалогов, {пар} пар реплик, "
              f"{всего:,} токенов ≈ {money(деньги)}. "
              f"Больше всего в «{топ.name}»: {топ.tokens:,}")
    return сводка, {"sessions": len(сессии), "pairs": пар, "tokens": всего,
                    "top": топ.name, "cost": деньги}


def исполнить_reminder(params: dict) -> tuple[str, dict]:
    """Напоминание. Самая простая задача — и самая понятная на видео."""
    текст = str(params.get("text") or "").strip() or "Напоминание без текста"
    сейчас = datetime.now().strftime("%H:%M")
    return f"[{сейчас}] {текст}", {"text": текст, "at": сейчас}


ПЛАНИРОВЩИК = Scheduler()
ПЛАНИРОВЩИК.register("repo_digest", исполнить_repo_digest)
ПЛАНИРОВЩИК.register("token_report", исполнить_token_report)
ПЛАНИРОВЩИК.register("reminder", исполнить_reminder)

ОПИСАНИЯ = {
    "repo_digest": "сводка по репозиторию: сколько коммитов за N дней",
    "token_report": "расход токенов и денег по всем диалогам",
    "reminder": "напоминание с заданным текстом",
}


# ── инструменты MCP ─────────────────────────────────────────────────────
ИНСТРУМЕНТЫ = [
    {
        "name": "list_tasks",
        "description": "Список фоновых задач: что запланировано, когда "
                       "следующий запуск, сколько раз выполнялось.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_task",
        "description": "Завести фоновую задачу по расписанию. Типы: "
                       + "; ".join(f"{к} — {о}" for к, о in ОПИСАНИЯ.items()),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "название задачи"},
                "kind": {"type": "string", "enum": list(ОПИСАНИЯ),
                         "description": "тип задачи"},
                "every_minutes": {"type": "integer",
                                  "description": "как часто, в минутах"},
                "text": {"type": "string",
                         "description": "для reminder — текст напоминания"},
                "days": {"type": "integer",
                         "description": "для repo_digest — за сколько дней"},
            },
            "required": ["kind", "every_minutes"],
        },
    },
    {
        "name": "run_task_now",
        "description": "Выполнить задачу немедленно, не дожидаясь срока.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "task_digest",
        "description": "Агрегированный результат задачи: сколько прогонов, "
                       "сколько удачных, что в последних сводках.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "update_task",
        "description": "Изменить настройки задачи: название, частоту, "
                       "параметры. Меняет срок следующего запуска.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
                "every_minutes": {"type": "integer"},
                "text": {"type": "string"},
                "days": {"type": "integer"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "delete_task",
        "description": "Удалить задачу вместе с её историей прогонов.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "toggle_task",
        "description": "Включить или выключить задачу, не удаляя её.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"},
                           "enabled": {"type": "boolean"}},
            "required": ["id", "enabled"],
        },
    },
]


def выполнить(имя: str, аргументы: dict) -> str:
    if имя == "list_tasks":
        задачи = ПЛАНИРОВЩИК.tasks()
        if not задачи:
            return ("Фоновых задач нет. Завести: add_task с типом "
                    + ", ".join(ОПИСАНИЯ))
        строки = [f"Задач: {len(задачи)}", ""]
        for з in задачи:
            значок = "●" if з.enabled else "○"
            строки.append(f"{значок} {з.id}. {з.name} [{з.kind}] — "
                          f"{з.interval_pretty}, {з.due_pretty}")
            if з.runs_count:
                строки.append(f"     выполнено {з.runs_count} раз, "
                              f"последний {з.last_run_at[:16].replace('T',' ')}")
        return "\n".join(строки)

    if имя == "add_task":
        вид = str(аргументы.get("kind") or "").strip()
        минут = int(аргументы.get("every_minutes") or 0)
        if минут < 1:
            raise ValueError("нужно every_minutes — как часто запускать")
        параметры = {}
        if аргументы.get("text"):
            параметры["text"] = str(аргументы["text"])
        if аргументы.get("days"):
            параметры["days"] = int(аргументы["days"])
        задача = ПЛАНИРОВЩИК.add_task(
            str(аргументы.get("name") or ОПИСАНИЯ.get(вид, вид)),
            вид, минут * 60, параметры)
        return (f"Задача {задача.id} «{задача.name}» заведена: "
                f"{задача.interval_pretty}, первый запуск {задача.due_pretty}.")

    if имя == "run_task_now":
        номер = int(аргументы.get("id") or 0)
        прогон = ПЛАНИРОВЩИК.run_task(номер)
        значок = "✓" if прогон.ok else "✕"
        return f"{значок} {прогон.summary}"

    if имя == "task_digest":
        сводка = ПЛАНИРОВЩИК.digest(int(аргументы.get("id") or 0))
        з = сводка["task"]
        строки = [f"Задача {з['id']} «{з['name']}» [{з['kind']}]",
                  f"расписание: {з['every']}, {з['due']}",
                  f"прогонов: {сводка['runs']} "
                  f"(удачных {сводка['ok']}, неудачных {сводка['failed']})"]
        if сводка["missed_periods"]:
            строки.append(f"пропущено периодов: {сводка['missed_periods']} "
                          f"(приложение было закрыто)")
        строки += ["", "Последние сводки:"]
        for з_ in сводка["history"][:6]:
            метка = "✓" if з_["ok"] else "✕"
            строки.append(f"  {метка} {з_['when']}  {з_['summary'][:80]}")
        return "\n".join(строки)

    if имя == "update_task":
        номер = int(аргументы.get("id") or 0)
        текущая = ПЛАНИРОВЩИК.task(номер)
        if текущая is None:
            raise ValueError(f"нет задачи с номером {номер}")
        параметры = dict(текущая.params)
        if аргументы.get("text") is not None:
            параметры["text"] = str(аргументы["text"])
        if аргументы.get("days") is not None:
            параметры["days"] = int(аргументы["days"])
        минут = аргументы.get("every_minutes")
        ПЛАНИРОВЩИК.update(
            номер,
            name=str(аргументы["name"]) if аргументы.get("name") else None,
            interval=int(минут) * 60 if минут else None,
            params=параметры)
        обновлённая = ПЛАНИРОВЩИК.task(номер)
        return (f"Задача {номер} обновлена: «{обновлённая.name}», "
                f"{обновлённая.interval_pretty}, {обновлённая.due_pretty}.")

    if имя == "delete_task":
        номер = int(аргументы.get("id") or 0)
        if not ПЛАНИРОВЩИК.delete(номер):
            raise ValueError(f"нет задачи с номером {номер}")
        return f"Задача {номер} удалена вместе с историей."

    if имя == "toggle_task":
        номер = int(аргументы.get("id") or 0)
        включить = bool(аргументы.get("enabled"))
        if not ПЛАНИРОВЩИК.toggle(номер, включить):
            raise ValueError(f"нет задачи с номером {номер}")
        return f"Задача {номер} {'включена' if включить else 'выключена'}."

    raise ValueError(f"нет такого инструмента: {имя}")


# ── протокол ────────────────────────────────────────────────────────────
def ответ(номер, результат: dict) -> dict:
    return {"jsonrpc": "2.0", "id": номер, "result": результат}


def ошибка(номер, код: int, текст: str) -> dict:
    return {"jsonrpc": "2.0", "id": номер, "error": {"code": код, "message": текст}}


def обработать(письмо: dict) -> dict | None:
    метод, номер = письмо.get("method"), письмо.get("id")
    параметры = письмо.get("params") or {}

    if номер is None:
        log(f"уведомление: {метод}")
        return None

    if метод == "initialize":
        log(f"рукопожатие, клиент просит {параметры.get('protocolVersion')}")
        return ответ(номер, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": "Планировщик фоновых задач: сводки по репозиторию, "
                            "отчёты по токенам, напоминания.",
        })

    if метод == "tools/list":
        return ответ(номер, {"tools": ИНСТРУМЕНТЫ})

    if метод == "tools/call":
        имя = параметры.get("name", "")
        аргументы = параметры.get("arguments") or {}
        log(f"вызов {имя}({аргументы})")
        try:
            текст = выполнить(имя, аргументы)
        except (ValueError, SchedulerError) as exc:
            return ответ(номер, {
                "content": [{"type": "text", "text": f"Ошибка: {exc}"}],
                "isError": True})
        except Exception as exc:                          # noqa: BLE001
            log(f"внутренний сбой: {exc!r}")
            return ответ(номер, {
                "content": [{"type": "text", "text": f"Сбой: {exc}"}],
                "isError": True})
        return ответ(номер, {"content": [{"type": "text", "text": текст}]})

    if метод == "ping":
        return ответ(номер, {})

    return ошибка(номер, -32601, f"Метод не поддерживается: {метод}")


def main() -> int:
    log(f"{SERVER_NAME} {SERVER_VERSION} запущен")
    log(f"  база задач: {ПЛАНИРОВЩИК.path}")
    # Наверстывание: то, чему срок вышел, пока процесс не работал.
    догнали = ПЛАНИРОВЩИК.tick()
    if догнали:
        log(f"  наверстано задач при старте: {len(догнали)}")
    ПЛАНИРОВЩИК.start()
    log("  фоновый поток запущен")

    try:
        for строка in sys.stdin:
            строка = строка.strip()
            if not строка:
                continue
            try:
                письмо = json.loads(строка)
            except json.JSONDecodeError as exc:
                print(json.dumps(ошибка(None, -32700, str(exc)),
                                 ensure_ascii=False), flush=True)
                continue
            готовый = обработать(письмо)
            if готовый is not None:
                print(json.dumps(готовый, ensure_ascii=False), flush=True)
    finally:
        ПЛАНИРОВЩИК.stop()
        log("stdin закрыт, фон остановлен")
    return 0


if __name__ == "__main__":
    sys.exit(main())
