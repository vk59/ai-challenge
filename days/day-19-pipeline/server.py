#!/usr/bin/env python3
"""MCP-сервер с пайплайном инструментов (день 19).

Три инструмента выстраиваются в цепочку:

    search(запрос)          → артефакт #N с найденным
    summarize(N)            → артефакт #M со сводкой
    save_to_file(M, имя)    → файл на диске + артефакт #K

Главное решение — как данные переходят между шагами. Они НЕ возвращаются
текстом и не проходят через модель: инструмент кладёт результат в артефакт
и отдаёт только номер и короткое превью. Следующий инструмент забирает
содержимое по номеру.

Почему так. Если вернуть найденное текстом, оно уедет в контекст, модель
перескажет его своими словами и передаст дальше уже искажённым — на каждом
шаге данные теряли бы точность, а на больших выборках ещё и стоили бы денег.

Проверяется это сверкой: у артефакта есть длина и sha256, и видно, что
summarize получил ровно то, что записал search.

Есть и четвёртый инструмент — run_pipeline: выполняет всю цепочку сам,
без участия модели в оркестрации. Он нужен, чтобы показать разницу между
«модель решила вызвать три инструмента» и «цепочка выполнилась как одно
целое».
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from artifacts import ArtifactError, ArtifactStore  # noqa: E402
from llm import LLMError, ask  # noqa: E402

КОРЕНЬ = Path(os.environ.get("AI_ADVENT_REPO")
              or Path(__file__).resolve().parents[2])
ЗЕРКАЛО = os.environ.get("AI_ADVENT_REPO_MIRROR") or ""
ИСТОЧНИК = Path(ЗЕРКАЛО) if ЗЕРКАЛО and Path(ЗЕРКАЛО).is_dir() else КОРЕНЬ / ".git"

# Куда save_to_file пишет файлы. В Documents приложение может не пустить
# (см. день 17), поэтому по умолчанию — рядом с остальными данными.
ВЫВОД = Path(os.environ.get("AI_ADVENT_OUTPUT_DIR")
             or Path(os.environ.get("AI_ADVENT_MEMORY_DIR")
                     or КОРЕНЬ / "memory") / "pipeline")

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "ai-advent-pipeline"
SERVER_VERSION = "1.0"

ХРАНИЛИЩЕ = ArtifactStore()

MODEL = "deepseek-v4-flash"
ПРЕДЕЛ_ПОИСКА = 12000        # сколько знаков найденного класть в артефакт


def log(*части) -> None:
    print(*части, file=sys.stderr, flush=True)


def git(*аргументы: str) -> str:
    команда = ["git", "--git-dir", str(ИСТОЧНИК), *аргументы]
    готово = subprocess.run(команда, cwd="/tmp", capture_output=True,
                            text=True, timeout=40)
    if готово.returncode != 0:
        # git grep возвращает 1, когда ничего не нашёл, — это не ошибка
        if готово.returncode == 1 and not готово.stderr.strip():
            return ""
        raise ValueError(f"git: {готово.stderr.strip()[:160]}")
    return готово.stdout


# ── шаг 1: поиск ────────────────────────────────────────────────────────
def выполнить_поиск(запрос: str, где: str = "all") -> tuple[str, dict]:
    """Ищет по истории и содержимому репозитория. Возвращает (текст, мета)."""
    запрос = (запрос or "").strip()
    if not запрос:
        raise ValueError("нужен непустой запрос")

    куски, мета = [], {"query": запрос, "commits": 0, "files": 0}

    if где in ("all", "commits"):
        коммиты = git("log", f"--grep={запрос}", "-i", "-25",
                      "--format=%h|%ad|%s|%b", "--date=short").strip()
        if коммиты:
            записи = [с for с in коммиты.split("\n") if "|" in с]
            мета["commits"] = len(записи)
            куски.append(f"=== Коммиты по запросу «{запрос}» ({len(записи)}) ===")
            for с in записи:
                части = с.split("|", 3)
                тело = (части[3][:400] + "…") if len(части) > 3 and части[3] else ""
                куски.append(f"\n[{части[0]}] {части[1]} {части[2]}\n{тело}")

    if где in ("all", "files"):
        # -I пропускает двоичные файлы, -n даёт номера строк
        найдено = git("grep", "-I", "-n", "-i", "--max-count=3",
                      запрос, "HEAD").strip()
        if найдено:
            строки = найдено.splitlines()[:60]
            мета["files"] = len({с.split(":", 2)[1] for с in строки
                                 if с.count(":") >= 2})
            куски.append(f"\n=== Упоминания в файлах ({len(строки)} строк) ===")
            for с in строки:
                части = с.split(":", 3)
                if len(части) >= 4:
                    куски.append(f"{части[1]}:{части[2]}  {части[3].strip()[:120]}")

    if not куски:
        return f"По запросу «{запрос}» ничего не найдено.", мета
    return "\n".join(куски)[:ПРЕДЕЛ_ПОИСКА], мета


# ── шаг 2: сводка ───────────────────────────────────────────────────────
РОЛЬ_СВОДКИ = (
    "Ты делаешь сжатую выжимку из результатов поиска по репозиторию. "
    "Сохрани конкретику: номера коммитов, даты, названия файлов, числа. "
    "Выброси повторы и служебный шум. Пиши по-русски, короткими пунктами, "
    "без вступления и выводов. Если данных мало — так и скажи, не выдумывай."
)


def выполнить_сводку(данные: str, стиль: str = "") -> tuple[str, dict]:
    указание = f"\n\nОсобое указание: {стиль}" if стиль else ""
    ответ = ask(
        f"Результаты поиска:\n\n{данные}\n\nСделай выжимку.{указание}",
        system=РОЛЬ_СВОДКИ, model=MODEL, temperature=0.2, max_tokens=1200)
    return ответ.text.strip(), {
        "prompt_tokens": ответ.prompt_tokens,
        "completion_tokens": ответ.completion_tokens,
        "model": ответ.model,
    }


# ── шаг 3: сохранение ───────────────────────────────────────────────────
def безопасное_имя(имя: str) -> str:
    """Имя файла без путей и сюрпризов: пишем только в свой каталог."""
    чистое = re.sub(r"[^\w\s.\-]", "", (имя or "").strip(), flags=re.UNICODE)
    чистое = чистое.replace(" ", "-").strip(".-") or "результат"
    if not чистое.lower().endswith((".md", ".txt", ".json")):
        чистое += ".md"
    return чистое[:80]


def выполнить_сохранение(данные: str, имя: str, заголовок: str) -> tuple[str, dict]:
    ВЫВОД.mkdir(parents=True, exist_ok=True)
    путь = ВЫВОД / безопасное_имя(имя)
    тело = f"# {заголовок}\n\n{данные}\n"
    путь.write_text(тело, encoding="utf-8")
    return str(путь), {"path": str(путь), "bytes": len(тело.encode("utf-8"))}


# ── инструменты MCP ─────────────────────────────────────────────────────
ИНСТРУМЕНТЫ = [
    {
        "name": "search",
        "description": "Шаг 1 пайплайна. Ищет по истории и содержимому "
                       "репозитория. Возвращает НОМЕР артефакта с найденным "
                       "и превью — сами данные в ответ не попадают.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "что искать"},
                "scope": {"type": "string", "enum": ["all", "commits", "files"],
                          "description": "где искать, по умолчанию везде"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "summarize",
        "description": "Шаг 2 пайплайна. Берёт артефакт по номеру и делает "
                       "выжимку. Возвращает номер нового артефакта.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "integer",
                                "description": "номер артефакта из search"},
                "style": {"type": "string",
                          "description": "особое указание к выжимке"},
            },
            "required": ["artifact_id"],
        },
    },
    {
        "name": "save_to_file",
        "description": "Шаг 3 пайплайна. Сохраняет артефакт по номеру в файл "
                       "и возвращает путь.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "integer"},
                "filename": {"type": "string",
                             "description": "имя файла, например отчёт.md"},
            },
            "required": ["artifact_id"],
        },
    },
    {
        "name": "run_pipeline",
        "description": "Выполняет всю цепочку разом: поиск → выжимка → файл. "
                       "Возвращает отчёт о каждом шаге и итоговый путь.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "filename": {"type": "string"},
                "style": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "show_chain",
        "description": "Показывает цепочку, из которой получен артефакт: "
                       "что из чего сделано, с длинами и хешами.",
        "inputSchema": {
            "type": "object",
            "properties": {"artifact_id": {"type": "integer"}},
            "required": ["artifact_id"],
        },
    },
    {
        "name": "list_artifacts",
        "description": "Список последних артефактов с номерами и размерами.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def выполнить(имя: str, аргументы: dict) -> str:
    if имя == "search":
        текст, мета = выполнить_поиск(str(аргументы.get("query") or ""),
                                      str(аргументы.get("scope") or "all"))
        артефакт = ХРАНИЛИЩЕ.put(
            "search", f"Поиск: {аргументы.get('query')}", текст,
            tool="search", meta=мета)
        return (f"Найдено и сохранено в артефакт #{артефакт.id}\n"
                f"  коммитов: {мета['commits']}, файлов: {мета['files']}\n"
                f"  объём: {артефакт.length} знаков, sha {артефакт.sha}\n"
                f"  превью: {артефакт.preview[:160]}\n\n"
                f"Дальше: summarize(artifact_id={артефакт.id})")

    if имя == "summarize":
        номер = int(аргументы.get("artifact_id") or 0)
        исходный = ХРАНИЛИЩЕ.get(номер)
        текст, мета = выполнить_сводку(исходный.content,
                                       str(аргументы.get("style") or ""))
        артефакт = ХРАНИЛИЩЕ.put(
            "summary", f"Выжимка из #{номер}", текст,
            tool="summarize", source_id=номер, meta=мета)
        сжатие = (round(исходный.length / артефакт.length, 1)
                  if артефакт.length else 0)
        return (f"Выжимка готова, артефакт #{артефакт.id}\n"
                f"  вход: #{номер}, {исходный.length} знаков (sha {исходный.sha})\n"
                f"  выход: {артефакт.length} знаков, сжатие в {сжатие} раз\n"
                f"  превью: {артефакт.preview[:200]}\n\n"
                f"Дальше: save_to_file(artifact_id={артефакт.id})")

    if имя == "save_to_file":
        номер = int(аргументы.get("artifact_id") or 0)
        исходный = ХРАНИЛИЩЕ.get(номер)
        имя_файла = str(аргументы.get("filename") or исходный.title)
        путь, мета = выполнить_сохранение(исходный.content, имя_файла,
                                          исходный.title)
        артефакт = ХРАНИЛИЩЕ.put("file", f"Файл {Path(путь).name}", путь,
                                 tool="save_to_file", source_id=номер, meta=мета)
        return (f"Сохранено: {путь}\n"
                f"  из артефакта #{номер} ({исходный.length} знаков)\n"
                f"  записано байт: {мета['bytes']}\n"
                f"  артефакт файла: #{артефакт.id}")

    if имя == "run_pipeline":
        запрос = str(аргументы.get("query") or "").strip()
        if not запрос:
            raise ValueError("нужен query")
        шаги = []

        текст, мета = выполнить_поиск(запрос)
        найденное = ХРАНИЛИЩЕ.put("search", f"Поиск: {запрос}", текст,
                                  tool="search", meta=мета)
        шаги.append(f"1. search   → #{найденное.id}  {найденное.length} знаков "
                    f"(sha {найденное.sha}), коммитов {мета['commits']}")

        сводка_текст, мета2 = выполнить_сводку(найденное.content,
                                               str(аргументы.get("style") or ""))
        сводка = ХРАНИЛИЩЕ.put("summary", f"Выжимка из #{найденное.id}",
                               сводка_текст, tool="summarize",
                               source_id=найденное.id, meta=мета2)
        шаги.append(f"2. summarize→ #{сводка.id}  {сводка.length} знаков "
                    f"(sha {сводка.sha}), вход #{найденное.id}")

        путь, мета3 = выполнить_сохранение(
            сводка.content, str(аргументы.get("filename") or запрос),
            f"Выжимка по запросу «{запрос}»")
        файл = ХРАНИЛИЩЕ.put("file", f"Файл {Path(путь).name}", путь,
                             tool="save_to_file", source_id=сводка.id, meta=мета3)
        шаги.append(f"3. save     → #{файл.id}  {мета3['bytes']} байт, "
                    f"вход #{сводка.id}")

        сжатие = (round(найденное.length / сводка.length, 1)
                  if сводка.length else 0)
        return ("Пайплайн выполнен целиком:\n\n" + "\n".join(шаги) +
                f"\n\nСжатие: {найденное.length} → {сводка.length} знаков "
                f"(в {сжатие} раз)\nФайл: {путь}")

    if имя == "show_chain":
        номер = int(аргументы.get("artifact_id") or 0)
        цепь = ХРАНИЛИЩЕ.chain(номер)
        if not цепь:
            raise ValueError(f"нет артефакта #{номер}")
        строки = [f"Цепочка до #{номер}:", ""]
        для_проверки = None
        for звено in цепь:
            стрелка = "└→ " if звено.source_id else "   "
            строки.append(f"{стрелка}#{звено.id} [{звено.kind}] {звено.title}")
            строки.append(f"     {звено.length} знаков, sha {звено.sha}, "
                          f"инструмент {звено.tool}, {звено.when}")
            если_вход = звено.source_id
            if если_вход and для_проверки is not None:
                строки.append(f"     вход: #{если_вход} "
                              f"({для_проверки} знаков) — данные взяты "
                              f"по ссылке, не через модель")
            для_проверки = звено.length
        return "\n".join(строки)

    if имя == "list_artifacts":
        список = ХРАНИЛИЩЕ.recent(limit=20)
        if not список:
            return "Артефактов нет. Начните с search."
        строки = [f"Артефактов: {len(список)}", ""]
        for а in список:
            откуда = f" ← #{а.source_id}" if а.source_id else ""
            строки.append(f"#{а.id} [{а.kind}]{откуда} {а.length} знаков "
                          f"· {а.title[:50]}")
        return "\n".join(строки)

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
        return ответ(номер, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": "Пайплайн: search → summarize → save_to_file. "
                            "Инструменты обмениваются номерами артефактов, "
                            "а не данными.",
        })

    if метод == "tools/list":
        return ответ(номер, {"tools": ИНСТРУМЕНТЫ})

    if метод == "tools/call":
        имя = параметры.get("name", "")
        аргументы = параметры.get("arguments") or {}
        log(f"вызов {имя}({аргументы})")
        try:
            текст = выполнить(имя, аргументы)
        except (ValueError, ArtifactError, LLMError) as exc:
            return ответ(номер, {
                "content": [{"type": "text", "text": f"Ошибка: {exc}"}],
                "isError": True})
        except Exception as exc:                          # noqa: BLE001
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
    log(f"  артефакты: {ХРАНИЛИЩЕ.path}")
    log(f"  файлы: {ВЫВОД}")
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
