#!/usr/bin/env python3
"""День 31: MCP-сервер о состоянии проекта ПРЯМО СЕЙЧАС.

    python3 server.py        # говорит по stdio, запускается клиентом

Сервер дня 17 рассказывает историю: кто что коммитил, сколько строк,
когда. Этого мало для ассистента разработчика. Вопросы «в какой я ветке»,
«что я не закоммитил», «что изменилось в файле» касаются текущего
состояния, а истории в нём нет вовсе.

Разделение намеренное: история и состояние — разные источники, и
смешивать их в одном сервере значило бы получить свалку из полутора
десятков инструментов, среди которых модель начнёт путаться. В дне 20
это уже проверено: девятнадцать инструментов без карты маршрутов дают
промахи.

Рабочее дерево нельзя прочитать из зеркала: `git clone --mirror` копирует
только историю, файлов там нет. Поэтому, если репозиторий недоступен
(собранное приложение не видит ~/Documents), сервер честно говорит об
этом, а не возвращает пустоту, похожую на «изменений нет».
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_VAR = "AI_ADVENT_REPO"


def repo_path() -> Path | None:
    """Где лежит рабочий репозиторий. None — если недоступен."""
    raw = os.environ.get(REPO_VAR, "").strip()
    candidate = Path(raw).expanduser() if raw else HERE.parents[1]
    try:
        if (candidate / ".git").exists():
            # Проверяем не наличие папки, а возможность прочитать:
            # macOS отдаёт .git как существующий, но не пускает внутрь.
            subprocess.run(["git", "-C", str(candidate), "rev-parse", "HEAD"],
                           capture_output=True, timeout=10, check=True)
            return candidate
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return None


def git(*args: str, limit: int = 20000) -> str:
    """git в рабочем дереве. Ошибку возвращаем текстом, не поднимаем."""
    repo = repo_path()
    if repo is None:
        return ("НЕТ ДОСТУПА К РАБОЧЕМУ ДЕРЕВУ.\n"
                "Состояние файлов читается только из настоящего репозитория: "
                "в зеркале (git clone --mirror) файлов нет вовсе, там одна "
                "история. Запустите ассистента из терминала в папке проекта "
                f"или укажите путь в {REPO_VAR}.")
    try:
        # core.quotepath=false — иначе git отдаёт пути с кириллицей
        # в экранированных кавычках, и в списке каталогов появляется
        # мусорная строка вида «"days: 3».
        done = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
            capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as failure:
        return f"git не отработал: {failure}"
    out = (done.stdout or "").strip()
    if done.returncode != 0:
        return f"git вернул {done.returncode}: {(done.stderr or '').strip()[:300]}"
    return out[:limit] + ("\n…обрезано" if len(out) > limit else "")


# ── инструменты ─────────────────────────────────────────────────────────
def tool_current_branch() -> str:
    """Ветка, последний коммит и расхождение с origin."""
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch.startswith(("НЕТ ДОСТУПА", "git ")):
        return branch
    head = git("log", "-1", "--format=%h %s (%ar)")
    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    lines = [f"ветка: {branch}", f"последний коммит: {head}"]
    if not upstream.startswith("git вернул"):
        counts = git("rev-list", "--left-right", "--count", f"{upstream}...HEAD")
        parts = counts.split()
        if len(parts) == 2:
            behind, ahead = parts
            lines.append(f"относительно {upstream}: впереди на {ahead}, "
                         f"позади на {behind}")
    else:
        lines.append("ветка не связана с origin")
    return "\n".join(lines)


def tool_working_state() -> str:
    """Что не закоммичено: изменённые, новые, удалённые файлы."""
    raw = git("status", "--porcelain=v1")
    if raw.startswith(("НЕТ ДОСТУПА", "git ")):
        return raw
    if not raw:
        return "рабочее дерево чистое: незакоммиченных изменений нет"

    buckets: dict[str, list[str]] = {"изменено": [], "добавлено": [],
                                     "удалено": [], "новые": []}
    for line in raw.splitlines():
        code, _, name = line.partition(" ")
        name = name.strip() or line[3:].strip()
        if line.startswith("??"):
            buckets["новые"].append(name)
        elif "D" in code:
            buckets["удалено"].append(name)
        elif "A" in code:
            buckets["добавлено"].append(name)
        else:
            buckets["изменено"].append(name)

    parts = []
    for title, names in buckets.items():
        if not names:
            continue
        shown = names[:15]
        parts.append(f"{title} ({len(names)}):\n  "
                     + "\n  ".join(shown)
                     + (f"\n  …и ещё {len(names) - 15}"
                        if len(names) > 15 else ""))
    stat = git("diff", "--shortstat")
    if stat:
        parts.append(f"итого по изменённым: {stat}")
    return "\n\n".join(parts)


def tool_diff(path: str = "", staged: bool = False) -> str:
    """Что именно изменилось — в файле или во всём дереве."""
    args = ["diff"]
    if staged:
        args.append("--cached")
    if path:
        args += ["--", path]
    raw = git(*args, limit=8000)
    if raw.startswith(("НЕТ ДОСТУПА", "git ")):
        return raw
    if not raw:
        where = f" в {path}" if path else ""
        return (f"изменений{where} нет"
                + (" среди подготовленных к коммиту" if staged else ""))
    return raw


def tool_list_files(folder: str = "", pattern: str = "") -> str:
    """Файлы под версионным контролем. Для вопросов о структуре проекта."""
    args = ["ls-files"]
    if folder:
        args.append(folder.rstrip("/") + "/")
    raw = git(*args, limit=40000)
    if raw.startswith(("НЕТ ДОСТУПА", "git ")):
        return raw
    names = [n for n in raw.splitlines() if n]
    if pattern:
        low = pattern.lower()
        names = [n for n in names if low in n.lower()]
    if not names:
        return f"ничего не нашлось" + (f" по «{pattern}»" if pattern else "")

    # Больше сотни имён модели не помогают, а контекст съедают. Сворачиваем
    # в дерево по верхним каталогам.
    if len(names) > 80:
        tree: dict[str, int] = {}
        for name in names:
            top = name.split("/")[0] if "/" in name else "(корень)"
            tree[top] = tree.get(top, 0) + 1
        lines = [f"файлов всего: {len(names)}, по каталогам:"]
        for top, count in sorted(tree.items(), key=lambda p: -p[1]):
            lines.append(f"  {top}: {count}")
        lines.append("\nуточните каталог или образец, чтобы увидеть имена")
        return "\n".join(lines)
    return f"файлов: {len(names)}\n" + "\n".join(names)


def tool_recent_commits(limit: int = 10) -> str:
    """Последние коммиты — чем занимались в ближайшее время."""
    limit = max(1, min(40, int(limit)))
    raw = git("log", f"-{limit}", "--format=%h  %ad  %s", "--date=short")
    if raw.startswith(("НЕТ ДОСТУПА", "git ")):
        return raw
    return raw or "коммитов нет"


TOOLS = [
    {"name": "current_branch",
     "description": "Текущая ветка git, последний коммит и расхождение "
                    "с origin. Используй, когда спрашивают, где мы сейчас, "
                    "в какой ветке, что последнее закоммичено.",
     "inputSchema": {"type": "object", "properties": {}},
     "run": lambda args: tool_current_branch()},

    {"name": "working_state",
     "description": "Незакоммиченные изменения: что изменено, добавлено, "
                    "удалено, какие файлы новые. Используй для вопросов "
                    "«что я не сохранил», «над чем сейчас работа».",
     "inputSchema": {"type": "object", "properties": {}},
     "run": lambda args: tool_working_state()},

    {"name": "diff",
     "description": "Текст изменений: во всём дереве или в одном файле. "
                    "Используй, когда спрашивают, что именно поменялось.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string",
                  "description": "путь к файлу, пусто — всё дерево"},
         "staged": {"type": "boolean",
                    "description": "показать подготовленное к коммиту"}}},
     "run": lambda args: tool_diff(str(args.get("path") or ""),
                                   bool(args.get("staged")))},

    {"name": "list_files",
     "description": "Файлы проекта под версионным контролем, с фильтром "
                    "по каталогу и образцу имени. Используй для вопросов "
                    "о структуре: где лежит, из чего состоит проект.",
     "inputSchema": {"type": "object", "properties": {
         "folder": {"type": "string", "description": "каталог, например shared"},
         "pattern": {"type": "string", "description": "кусок имени файла"}}},
     "run": lambda args: tool_list_files(str(args.get("folder") or ""),
                                         str(args.get("pattern") or ""))},

    {"name": "recent_commits",
     "description": "Последние коммиты с датами. Используй для вопросов "
                    "«чем занимались недавно», «что было вчера».",
     "inputSchema": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "сколько, по умолчанию 10"}}},
     "run": lambda args: tool_recent_commits(args.get("limit") or 10)},
]


# ── протокол ────────────────────────────────────────────────────────────
def reply(message_id, result=None, error=None) -> None:
    body = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    sys.stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = message.get("method")
        message_id = message.get("id")

        if method == "initialize":
            reply(message_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "project-state", "version": "1.0"}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            reply(message_id, {"tools": [
                {k: v for k, v in tool.items() if k != "run"}
                for tool in TOOLS]})
        elif method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            tool = next((t for t in TOOLS if t["name"] == name), None)
            if tool is None:
                reply(message_id, error={"code": -32601,
                                         "message": f"нет инструмента {name}"})
                continue
            try:
                text = tool["run"](args)
            except Exception as failure:                  # noqa: BLE001
                text = f"инструмент упал: {type(failure).__name__}: {failure}"
            reply(message_id, {"content": [{"type": "text", "text": text}]})
        elif message_id is not None:
            reply(message_id, error={"code": -32601,
                                     "message": f"нет метода {method}"})


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        repo = repo_path()
        print(f"репозиторий: {repo or 'НЕДОСТУПЕН'}")
        for tool in TOOLS:
            print(f"\n── {tool['name']}")
            print(tool["run"]({})[:400])
        sys.exit(0)
    main()
