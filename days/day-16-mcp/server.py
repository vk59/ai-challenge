#!/usr/bin/env python3
"""MCP-сервер над памятью агента — на стандартной библиотеке.

Отдаёт наружу то, что агент накопил за дни 7–15: диалоги, долговременную
память, состояние задач и ограничения проекта. Любой MCP-клиент (наш из
shared/mcp.py, Claude Desktop, что угодно) может это увидеть и вызвать.

Запускается не руками, а клиентом — как дочерний процесс:

    python3 list_tools.py --server memory

Если запустить напрямую, он будет молча ждать JSON-RPC в stdin. Это не
зависание, это нормальная жизнь stdio-сервера.

Два правила, которые нельзя нарушать:

    1. В stdout идёт ТОЛЬКО JSON-RPC, по одному объекту на строку.
       Любой лишний print сломает протокол у клиента.
    2. Логи и диагностика — в stderr. Его клиент читает отдельно.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))

from memory import STAGE_LABELS, open_store  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "ai-advent-memory"
SERVER_VERSION = "1.0"


def log(*части) -> None:
    """Только в stderr: stdout занят протоколом."""
    print(*части, file=sys.stderr, flush=True)


# ── инструменты ─────────────────────────────────────────────────────────
# Описание каждого — то, что увидит клиент в tools/list. Схема параметров
# обычный JSON Schema: по ней клиент понимает, что можно передать.
ИНСТРУМЕНТЫ = [
    {
        "name": "list_chats",
        "description": "Список сохранённых диалогов агента: имя, число пар "
                       "реплик и когда обновлялся.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer",
                          "description": "сколько вернуть, по умолчанию 20"},
            },
        },
    },
    {
        "name": "recall",
        "description": "Долговременная память агента: что он знает о "
                       "собеседнике, какие решения приняты, какие факты усвоены.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": ["profile", "decision", "knowledge"],
                         "description": "отфильтровать по виду записи"},
            },
        },
    },
    {
        "name": "task_state",
        "description": "Состояние задачи в диалоге: этап, текущий шаг, "
                       "ожидаемое действие и журнал переходов.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string",
                            "description": "имя диалога"},
            },
            "required": ["session"],
        },
    },
    {
        "name": "invariants",
        "description": "Ограничения проекта, которые агент не имеет права "
                       "нарушать: архитектура, стек, бизнес-правила.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "only_active": {"type": "boolean",
                                "description": "только действующие"},
            },
        },
    },
]


def выполнить(имя: str, аргументы: dict) -> str:
    """Делает работу инструмента и возвращает текст для клиента."""
    хранилище = open_store("sqlite")

    if имя == "list_chats":
        предел = int(аргументы.get("limit") or 20)
        сессии = хранилище.sessions()[:предел]
        if not сессии:
            return "Сохранённых диалогов нет."
        строки = [f"Диалогов: {len(сессии)}", ""]
        for s in сессии:
            строки.append(f"• {s.name} — {s.pairs} пар, обновлён {s.when}")
            if s.title != s.name:
                строки.append(f"  «{s.title}»")
        return "\n".join(строки)

    if имя == "recall":
        вид = (аргументы.get("kind") or "").strip() or None
        записи = хранилище.recall(вид)
        if not записи:
            return "Долговременная память пуста."
        строки = [f"Записей: {len(записи)}", ""]
        for m in записи:
            строки.append(f"• [{m.kind}] {m.key}: {m.value}")
        return "\n".join(строки)

    if имя == "task_state":
        сессия = str(аргументы.get("session") or "").strip()
        if not сессия:
            raise ValueError("нужно имя диалога в параметре session")
        задача = хранилище.load_task(сессия)
        if задача is None:
            return f"В диалоге «{сессия}» задача не заведена."
        строки = [f"Диалог «{сессия}»",
                  f"этап: {STAGE_LABELS.get(задача.stage, задача.stage)}"]
        if задача.goal:
            строки.append(f"цель: {задача.goal}")
        if задача.step:
            строки.append(f"шаг: {задача.step}")
        if задача.expecting:
            строки.append(f"ждём: {задача.expecting}")
        if задача.approved:
            строки.append(f"отметки: {', '.join(задача.approved)}")
        переходы = [z for z in задача.log if z.get("to")]
        if переходы:
            путь = " → ".join(STAGE_LABELS.get(z["to"], z["to"])
                              for z in переходы[-5:])
            строки.append(f"пройдено: {путь}")
        return "\n".join(строки)

    if имя == "invariants":
        только = bool(аргументы.get("only_active"))
        список = хранилище.invariants(only_active=только)
        if not список:
            return "Ограничений не заведено."
        строки = [f"Ограничений: {len(список)}", ""]
        for i in список:
            метка = "" if i.active else " (отключено)"
            строки.append(f"{i.id}. [{i.scope_label}] {i.text}{метка}")
            if i.rationale:
                строки.append(f"   причина: {i.rationale}")
        return "\n".join(строки)

    raise ValueError(f"нет такого инструмента: {имя}")


# ── протокол ────────────────────────────────────────────────────────────
def ответ(номер, результат: dict) -> dict:
    return {"jsonrpc": "2.0", "id": номер, "result": результат}


def ошибка(номер, код: int, текст: str) -> dict:
    return {"jsonrpc": "2.0", "id": номер, "error": {"code": код, "message": текст}}


def обработать(письмо: dict) -> dict | None:
    """Один запрос → один ответ. None означает «отвечать не надо»."""
    метод = письмо.get("method")
    номер = письмо.get("id")
    параметры = письмо.get("params") or {}

    # Уведомления приходят без id и ответа не требуют. Ответить на них —
    # нарушение JSON-RPC: у клиента такого номера нет, и он не знает,
    # что с этим делать.
    if номер is None:
        log(f"уведомление: {метод}")
        return None

    if метод == "initialize":
        чужая_версия = параметры.get("protocolVersion")
        log(f"рукопожатие, клиент просит протокол {чужая_версия}")
        return ответ(номер, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": "Память агента AI Advent: диалоги, долговременные "
                            "записи, состояние задач и ограничения проекта.",
        })

    if метод == "tools/list":
        log(f"запрошен список инструментов ({len(ИНСТРУМЕНТЫ)})")
        return ответ(номер, {"tools": ИНСТРУМЕНТЫ})

    if метод == "tools/call":
        имя = параметры.get("name", "")
        аргументы = параметры.get("arguments") or {}
        log(f"вызов {имя}({аргументы})")
        try:
            текст = выполнить(имя, аргументы)
        except ValueError as exc:
            # Ошибка инструмента — не ошибка протокола. Правильно вернуть
            # результат с isError, иначе клиент решит, что сломался сервер.
            return ответ(номер, {
                "content": [{"type": "text", "text": f"Ошибка: {exc}"}],
                "isError": True,
            })
        except Exception as exc:                     # noqa: BLE001
            log(f"внутренний сбой: {exc!r}")
            return ответ(номер, {
                "content": [{"type": "text", "text": f"Сбой инструмента: {exc}"}],
                "isError": True,
            })
        return ответ(номер, {"content": [{"type": "text", "text": текст}]})

    if метод == "ping":
        return ответ(номер, {})

    return ошибка(номер, -32601, f"Метод не поддерживается: {метод}")


def main() -> int:
    log(f"{SERVER_NAME} {SERVER_VERSION} запущен, жду JSON-RPC в stdin")
    for строка in sys.stdin:
        строка = строка.strip()
        if not строка:
            continue
        try:
            письмо = json.loads(строка)
        except json.JSONDecodeError as exc:
            print(json.dumps(ошибка(None, -32700, f"Разбор JSON: {exc}"),
                             ensure_ascii=False), flush=True)
            continue

        готовый = обработать(письмо)
        if готовый is not None:
            print(json.dumps(готовый, ensure_ascii=False), flush=True)

    log("stdin закрыт, выходим")
    return 0


if __name__ == "__main__":
    sys.exit(main())
