#!/usr/bin/env python3
"""MCP-сервер вокруг Git и GitHub — летопись этого проекта.

День 16 научил клиента разговаривать по MCP. Здесь появляется сервер вокруг
настоящего внешнего интерфейса: git на диске и GitHub через gh CLI.

Почему именно git. Данных много и они свои: двадцать коммитов с подробными
сообщениями за шестнадцать дней. Агент, подключённый к такому серверу,
получает доступ к летописи собственной разработки и может ответить «что
делалось в девятый день» — не выдумкой, а выпиской из истории.

Запускается клиентом как дочерний процесс, не руками:

    python3 ask.py "Что я делал в девятый день?"

Два правила stdio-сервера, как и в дне 16: в stdout только JSON-RPC,
всё остальное — в stderr.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Внутри собранного .app файл лежит в Resources/, и путь «на два уровня
# вверх» указывает уже не на репозиторий. Поэтому корень можно задать
# снаружи — launcher из build_app.sh так и делает.
КОРЕНЬ = Path(os.environ.get("AI_ADVENT_REPO")
              or Path(__file__).resolve().parents[2])

# Зеркало репозитория для запуска из .app.
#
# macOS (TCC) не даёт приложению, запущенному из Finder, читать ~/Documents,
# и разрешения этого не выпросить из кода: git падает с
# «Unable to read current working directory: Operation not permitted».
# Из терминала всё работает, потому что терминалу доступ уже выдан.
#
# Поэтому build_app.sh кладёт bare-зеркало в Application Support, куда
# доступ свободный, и приложение читает историю оттуда. История задним
# числом не меняется, так что зеркало, снятое при сборке, не врёт —
# устаревать может только последний коммит.
ЗЕРКАЛО = os.environ.get("AI_ADVENT_REPO_MIRROR") or ""
ИСТОЧНИК = Path(ЗЕРКАЛО) if ЗЕРКАЛО and Path(ЗЕРКАЛО).is_dir() else КОРЕНЬ / ".git"
# В зеркале нет рабочего дерева: файлы читаются из объектов, а не с диска.
БЕЗ_ДЕРЕВА = ИСТОЧНИК != КОРЕНЬ / ".git"

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "ai-advent-git"
SERVER_VERSION = "1.0"

# Сколько отдавать максимум: результат уезжает в модель и оплачивается
# токенами, поэтому обрезаем здесь, а не «пусть разберётся».
ПРЕДЕЛ_СТРОК = 60
ПРЕДЕЛ_ЗНАКОВ = 4000


def log(*части) -> None:
    print(*части, file=sys.stderr, flush=True)


# Откуда запускать дочерние процессы. НЕ из репозитория — и вот почему.
#
# Когда приложение запущено из Finder, macOS (TCC) не даёт ему читать
# ~/Documents. subprocess с cwd=КОРЕНЬ делает chdir туда, после чего git
# при старте вызывает getcwd() и падает:
#
#     fatal: Unable to read current working directory: Operation not permitted
#
# Причём падает он на ЧТЕНИИ СВОЕЙ рабочей директории, а не репозитория.
# Поэтому запускаемся из заведомо доступного места, а пути к репозиторию
# передаём явными ключами — тогда getcwd() ни при чём.
БЕЗОПАСНЫЙ_CWD = "/tmp"


def git(*аргументы: str) -> str:
    """Запускает git с явными путями, не полагаясь на рабочую директорию."""
    команда = ["git", "--git-dir", str(ИСТОЧНИК)]
    if not БЕЗ_ДЕРЕВА:
        команда += ["--work-tree", str(КОРЕНЬ)]
    команда += list(аргументы)
    готово = subprocess.run(команда, cwd=БЕЗОПАСНЫЙ_CWD,
                            capture_output=True, text=True, timeout=30)
    if готово.returncode != 0:
        подсказка = ""
        if "Operation not permitted" in готово.stderr:
            подсказка = ("\nПохоже, macOS не пускает приложение к папке "
                         f"{КОРЕНЬ}. Выдайте доступ: Системные настройки → "
                         "Конфиденциальность и безопасность → Файлы и папки.")
        raise ValueError(f"git {' '.join(аргументы)}: "
                         f"{готово.stderr.strip()[:200]}{подсказка}")
    return готово.stdout


def обрезать(текст: str) -> str:
    строки = текст.splitlines()
    if len(строки) > ПРЕДЕЛ_СТРОК:
        строки = строки[:ПРЕДЕЛ_СТРОК] + [f"… ещё {len(текст.splitlines()) - ПРЕДЕЛ_СТРОК} строк"]
    готово = "\n".join(строки)
    return готово[:ПРЕДЕЛ_ЗНАКОВ]


def найти_коммит_дня(день: int) -> str:
    """Коммит, которым сдавался день N. Ищем по началу сообщения."""
    вывод = git("log", "--format=%H|%s", "--all")
    образец = re.compile(rf"^День {день}\b", re.IGNORECASE)
    for строка in вывод.splitlines():
        если_есть = строка.split("|", 1)
        if len(если_есть) == 2 and образец.match(если_есть[1].strip()):
            return если_есть[0]
    raise ValueError(f"не нашёл коммит для дня {день}")


# ── инструменты ─────────────────────────────────────────────────────────
ИНСТРУМЕНТЫ = [
    {
        "name": "day_summary",
        "description": "Что делалось в указанный день проекта: сообщение "
                       "коммита целиком и список изменённых файлов. "
                       "Используй, когда спрашивают про конкретный день.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "day": {"type": "integer",
                        "description": "номер дня, например 9"},
            },
            "required": ["day"],
        },
    },
    {
        "name": "search_commits",
        "description": "Найти коммиты по слову в сообщении. Используй, когда "
                       "спрашивают, когда появилась какая-то возможность.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "слово или фраза"},
                "limit": {"type": "integer", "description": "сколько вернуть"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "file_history",
        "description": "История изменений файла: в каких коммитах менялся "
                       "и насколько. Используй для вопросов про конкретный файл.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "путь от корня, например shared/agent.py"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "repo_stats",
        "description": "Сводка по репозиторию: сколько коммитов, дней, файлов, "
                       "строк и когда был первый и последний коммит.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "commits_by_day",
        "description": "Сколько коммитов сделано в каждый календарный день. "
                       "Используй для вопросов вида «когда работали больше "
                       "всего», «в какой день сколько коммитов».",
        "inputSchema": {
            "type": "object",
            "properties": {
                "top": {"type": "integer",
                        "description": "показать только N самых плотных дней"},
            },
        },
    },
    {
        "name": "github_info",
        "description": "Сведения о репозитории на GitHub через gh CLI: "
                       "имя, видимость, последний push. Требует авторизованного gh.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def выполнить(имя: str, аргументы: dict) -> str:
    if имя == "day_summary":
        день = аргументы.get("day")
        if день is None:
            raise ValueError("нужен номер дня в параметре day")
        коммит = найти_коммит_дня(int(день))
        сообщение = git("log", "-1", "--format=%s%n%n%b", коммит).strip()
        файлы = git("show", "--stat", "--format=", коммит).strip()
        дата = git("log", "-1", "--format=%ad", "--date=short", коммит).strip()
        return обрезать(f"День {день}, коммит {коммит[:8]} от {дата}\n\n"
                        f"{сообщение}\n\nИзменённые файлы:\n{файлы}")

    if имя == "search_commits":
        запрос = str(аргументы.get("query") or "").strip()
        if not запрос:
            raise ValueError("нужен текст в параметре query")
        предел = int(аргументы.get("limit") or 10)
        вывод = git("log", f"--grep={запрос}", "-i", f"-{предел}",
                    "--format=%h %ad %s", "--date=short").strip()
        if not вывод:
            return f"По запросу «{запрос}» коммитов не нашлось."
        сколько = len(вывод.splitlines())
        return обрезать(f"Нашлось коммитов: {сколько}\n\n{вывод}")

    if имя == "file_history":
        путь = str(аргументы.get("path") or "").strip()
        if not путь:
            raise ValueError("нужен путь в параметре path")
        вывод = git("log", "--follow", "--format=%h %ad %s", "--date=short",
                    "--", путь).strip()
        if not вывод:
            return f"Файл «{путь}» в истории не встречается."
        правок = len(вывод.splitlines())
        return обрезать(f"Файл {путь}: правок {правок}\n\n{вывод}")

    if имя == "repo_stats":
        коммитов = git("rev-list", "--count", "HEAD").strip()
        первый = git("log", "--reverse", "--format=%ad", "--date=short"
                     ).splitlines()[0]
        последний = git("log", "-1", "--format=%ad", "--date=short").strip()
        дней = len({путь.split("/")[1] for путь in
                    git("ls-tree", "-r", "HEAD", "--name-only").splitlines()
                    if путь.startswith("days/") and "/" in путь[5:]})
        # ls-files работает по индексу, которого в зеркале нет; ls-tree
        # читает то же самое прямо из коммита.
        файлы = git("ls-tree", "-r", "HEAD", "--name-only").splitlines()
        файлов = len(файлы)

        if БЕЗ_ДЕРЕВА:
            # Считать строки в зеркале значило бы вытаскивать каждый файл
            # из объектов — дорого и незачем. Честнее не показывать число,
            # чем показать неверное.
            строки_итог = "строк: считаются только при запуске из репозитория"
        else:
            сумма, не_прочитано = 0, 0
            for имя_файла in файлы:
                полный = КОРЕНЬ / имя_файла
                try:
                    сумма += sum(1 for _ in полный.open(encoding="utf-8",
                                                         errors="ignore"))
                except OSError:
                    не_прочитано += 1
            строки_итог = (f"строк всего: {сумма}" if не_прочитано == 0
                           else f"строк: {сумма} (не прочитано "
                                f"{не_прочитано} файлов)")
        return (f"Репозиторий AI Advent\n"
                f"коммитов: {коммитов}\n"
                f"папок с днями: {дней}\n"
                f"файлов под версией: {файлов}\n"
                f"{строки_итог}\n"
                f"первый коммит: {первый}\n"
                f"последний коммит: {последний}")

    if имя == "commits_by_day":
        даты = git("log", "--format=%ad", "--date=short").split()
        if not даты:
            return "Коммитов нет."
        подсчёт: dict[str, int] = {}
        for дата in даты:
            подсчёт[дата] = подсчёт.get(дата, 0) + 1
        по_убыванию = sorted(подсчёт.items(), key=lambda п: (-п[1], п[0]))
        предел = аргументы.get("top")
        показать = по_убыванию[:int(предел)] if предел else по_убыванию
        максимум = по_убыванию[0]
        строки = [f"Всего коммитов: {len(даты)} за {len(подсчёт)} дней",
                  f"Больше всего — {максимум[0]}: {максимум[1]} коммитов", ""]
        for дата, сколько in показать:
            строки.append(f"{дата}  {'█' * сколько} {сколько}")
        return обрезать("\n".join(строки))

    if имя == "github_info":
        # -R вместо cwd: по той же причине, что и у git выше. Адрес
        # достаём из самого репозитория, чтобы не зашивать его в код.
        адрес = git("config", "--get", "remote.origin.url").strip()
        краткий = адрес.split(":")[-1].removesuffix(".git") if адрес else ""
        команда = ["gh", "repo", "view", "--json",
                   "name,visibility,pushedAt,description,url"]
        if краткий:
            команда.insert(3, краткий)
        готово = subprocess.run(команда, cwd=БЕЗОПАСНЫЙ_CWD,
                                capture_output=True, text=True, timeout=30)
        if готово.returncode != 0:
            raise ValueError(f"gh: {готово.stderr.strip()[:200]}")
        d = json.loads(готово.stdout)
        return (f"GitHub: {d.get('name')}\n"
                f"видимость: {d.get('visibility')}\n"
                f"последний push: {d.get('pushedAt')}\n"
                f"адрес: {d.get('url')}")

    raise ValueError(f"нет такого инструмента: {имя}")


# ── протокол ────────────────────────────────────────────────────────────
def ответ(номер, результат: dict) -> dict:
    return {"jsonrpc": "2.0", "id": номер, "result": результат}


def ошибка(номер, код: int, текст: str) -> dict:
    return {"jsonrpc": "2.0", "id": номер, "error": {"code": код, "message": текст}}


def обработать(письмо: dict) -> dict | None:
    метод = письмо.get("method")
    номер = письмо.get("id")
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
            "instructions": "История репозитория AI Advent: что делалось "
                            "по дням, когда что появилось, как менялись файлы.",
        })

    if метод == "tools/list":
        log(f"список инструментов ({len(ИНСТРУМЕНТЫ)})")
        return ответ(номер, {"tools": ИНСТРУМЕНТЫ})

    if метод == "tools/call":
        имя = параметры.get("name", "")
        аргументы = параметры.get("arguments") or {}
        log(f"вызов {имя}({аргументы})")
        try:
            текст = выполнить(имя, аргументы)
        except (ValueError, subprocess.TimeoutExpired) as exc:
            return ответ(номер, {
                "content": [{"type": "text", "text": f"Ошибка: {exc}"}],
                "isError": True})
        except Exception as exc:                     # noqa: BLE001
            log(f"внутренний сбой: {exc!r}")
            return ответ(номер, {
                "content": [{"type": "text", "text": f"Сбой: {exc}"}],
                "isError": True})
        log(f"  → {len(текст)} знаков")
        return ответ(номер, {"content": [{"type": "text", "text": текст}]})

    if метод == "ping":
        return ответ(номер, {})

    return ошибка(номер, -32601, f"Метод не поддерживается: {метод}")


def main() -> int:
    log(f"{SERVER_NAME} {SERVER_VERSION} запущен")
    log(f"  источник истории: {ИСТОЧНИК}"
        + ("  (зеркало, без рабочего дерева)" if БЕЗ_ДЕРЕВА else ""))
    for строка in sys.stdin:
        строка = строка.strip()
        if not строка:
            continue
        try:
            письмо = json.loads(строка)
        except json.JSONDecodeError as exc:
            print(json.dumps(ошибка(None, -32700, str(exc)), ensure_ascii=False),
                  flush=True)
            continue
        готовый = обработать(письмо)
        if готовый is not None:
            print(json.dumps(готовый, ensure_ascii=False), flush=True)
    log("stdin закрыт")
    return 0


if __name__ == "__main__":
    sys.exit(main())
