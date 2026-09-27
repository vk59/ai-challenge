"""Мост между MCP и моделью (день 17).

MCP описывает инструменты по-своему, модель ждёт их в формате OpenAI.
Формы близкие, но не совпадают, и перевод между ними — ровно то, что
делает этот модуль:

    MCP                          OpenAI
    {name, description,    →     {type: "function", function: {
     inputSchema}                   name, description, parameters}}

Плюс он держит подключения к нескольким серверам сразу и разводит имена
по префиксам, иначе два сервера с инструментом `search` передрались бы.

    with MCPToolset({"git": ["python3", "server.py"]}) as инструменты:
        agent.tools = инструменты
        agent.ask("Что я делал в девятый день?")
"""

import json
from dataclasses import dataclass, field

from mcp import MCPClient, MCPError, Tool

# Разделитель в составном имени. Двоеточие и точка в именах функций
# OpenAI не принимает, подчёркивание — безопасный выбор.
РАЗДЕЛИТЕЛЬ = "__"


@dataclass
class ToolCall:
    """Один вызов инструмента — для журнала и показа в интерфейсе."""

    name: str
    arguments: dict
    result: str = ""
    failed: bool = False
    seconds: float = 0.0

    @property
    def short(self) -> str:
        аргументы = ", ".join(f"{к}={v!r}" for к, v in self.arguments.items())
        return f"{self.name}({аргументы})"


class MCPToolset:
    """Набор инструментов с нескольких MCP-серверов сразу."""

    def __init__(self, servers: dict[str, list[str]], *, timeout: float = 60.0):
        self.servers = servers
        self.timeout = timeout
        self._clients: dict[str, MCPClient] = {}
        self._tools: dict[str, tuple[str, Tool]] = {}   # полное имя → (сервер, инструмент)
        self.log: list[ToolCall] = []

    # ── жизненный цикл ──────────────────────────────────────────────────
    def __enter__(self) -> "MCPToolset":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def open(self) -> None:
        for имя, команда in self.servers.items():
            клиент = MCPClient(команда, timeout=self.timeout)
            клиент.start()
            клиент.initialize()
            self._clients[имя] = клиент
            for инструмент in клиент.list_tools():
                self._tools[f"{имя}{РАЗДЕЛИТЕЛЬ}{инструмент.name}"] = (имя, инструмент)

    def close(self) -> None:
        for клиент in self._clients.values():
            клиент.close()
        self._clients.clear()

    # ── то, ради чего всё ───────────────────────────────────────────────
    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        """Инструменты в том виде, в каком их ждёт модель."""
        готово = []
        for полное, (_, инструмент) in sorted(self._tools.items()):
            готово.append({
                "type": "function",
                "function": {
                    "name": полное,
                    "description": инструмент.description,
                    # MCP уже отдаёт JSON Schema — ровно то, что нужно.
                    # Пустую схему подменяем на валидный объект: без
                    # properties некоторые модели капризничают.
                    "parameters": инструмент.schema or {"type": "object",
                                                        "properties": {}},
                },
            })
        return готово

    def describe(self) -> str:
        """Человекочитаемая сводка — для строки состояния."""
        по_серверам: dict[str, int] = {}
        for _, (сервер, _) in self._tools.items():
            по_серверам[сервер] = по_серверам.get(сервер, 0) + 1
        куски = [f"{имя}: {сколько}" for имя, сколько in sorted(по_серверам.items())]
        return f"{len(self._tools)} инструментов ({', '.join(куски)})"

    def call(self, name: str, arguments: dict | str | None = None) -> ToolCall:
        """Выполняет вызов и возвращает запись для журнала.

        Ошибки НЕ поднимаются наружу: модель должна узнать о них текстом
        и попробовать иначе. Уронить разговор из-за того, что инструмент
        отказал, — худшее, что здесь можно сделать.
        """
        import time
        начало = time.monotonic()

        # Модель присылает аргументы строкой JSON, а не объектом.
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                запись = ToolCall(name, {"сырые": arguments[:120]},
                                  "Аргументы не разобрались как JSON", True)
                self.log.append(запись)
                return запись
        arguments = arguments or {}

        если_есть = self._tools.get(name)
        if если_есть is None:
            запись = ToolCall(name, arguments,
                              f"Нет такого инструмента. Доступны: "
                              f"{', '.join(self.names)}", True)
            self.log.append(запись)
            return запись

        сервер, _ = если_есть
        настоящее_имя = name.split(РАЗДЕЛИТЕЛЬ, 1)[1]
        try:
            сырой = self._clients[сервер].call_tool(настоящее_имя, arguments)
        except MCPError as exc:
            запись = ToolCall(name, arguments, f"Сервер не ответил: {exc}", True,
                              round(time.monotonic() - начало, 2))
            self.log.append(запись)
            return запись

        куски = сырой.get("content") or []
        текст = "\n".join(к.get("text", "") for к in куски
                          if к.get("type") == "text").strip()
        запись = ToolCall(name, arguments, текст or "(пустой ответ)",
                          bool(сырой.get("isError")),
                          round(time.monotonic() - начало, 2))
        self.log.append(запись)
        return запись


__all__ = ["MCPToolset", "ToolCall", "MCPError"]
