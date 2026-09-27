"""Клиент MCP на стандартной библиотеке.

MCP (Model Context Protocol) — способ дать модели инструменты, которые живут
в отдельном процессе. Протокол поверх stdio устроен просто: JSON-RPC 2.0,
по одному JSON-объекту на строку, в stdin сервера и обратно из stdout.

Внешний SDK сюда не тянем по той же причине, по которой в дне 1 не появился
requests: смотреть на протокол интереснее, чем на обёртку над ним. Весь обмен
видно глазами — это и проверяется на видео.

    with MCPClient(["python", "-m", "mcp_server_time"]) as сервер:
        print(сервер.info)
        for инструмент in сервер.list_tools():
            print(инструмент.name, инструмент.description)

Три шага рукопожатия, без которых сервер не отвечает:

    → initialize                 кто мы и какую версию протокола знаем
    ← результат                  кто он и что умеет
    → notifications/initialized  уведомление: готовы работать

Уведомление — не запрос: на него не приходит ответа, и ждать его нельзя,
иначе клиент зависнет навсегда. Это первое, на чём спотыкаются.
"""

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field

# Версия протокола, на которой настаиваем при рукопожатии. Сервер вправе
# ответить другой — свежей или старой. Это нормально и не ошибка: в ответе
# приходит та, на которой он согласен говорить, и её мы и запоминаем.
PROTOCOL_VERSION = "2025-06-18"

CLIENT_NAME = "ai-advent-mcp"
CLIENT_VERSION = "1.0"

DEFAULT_TIMEOUT = 30.0


class MCPError(Exception):
    """Понятная человеку ошибка работы с MCP-сервером."""


@dataclass
class Tool:
    """Один инструмент, как его описал сервер."""

    name: str
    description: str = ""
    schema: dict = field(default_factory=dict)

    @property
    def params(self) -> list[tuple[str, str, bool]]:
        """Параметры из JSON Schema: (имя, тип, обязательный)."""
        свойства = (self.schema or {}).get("properties") or {}
        обязательные = set((self.schema or {}).get("required") or [])
        return [
            (имя, str((описание or {}).get("type", "?")), имя in обязательные)
            for имя, описание in свойства.items()
        ]

    @property
    def short(self) -> str:
        одна_строка = " ".join((self.description or "").split())
        return одна_строка[:76] + "…" if len(одна_строка) > 77 else одна_строка


@dataclass
class ServerInfo:
    """Что сервер рассказал о себе при рукопожатии."""

    name: str = ""
    version: str = ""
    protocol: str = ""
    capabilities: dict = field(default_factory=dict)
    instructions: str = ""

    @property
    def abilities(self) -> list[str]:
        """Крупные разделы возможностей: tools, resources, prompts…"""
        return sorted(self.capabilities or {})

    def __str__(self) -> str:
        хвост = f" {self.version}" if self.version else ""
        return f"{self.name or 'сервер'}{хвост} · протокол {self.protocol}"


class MCPClient:
    """Подключение к MCP-серверу, запущенному как дочерний процесс."""

    def __init__(self, command: list[str], *, env: dict | None = None,
                 cwd: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        if not command:
            raise MCPError("Не указана команда запуска сервера")
        self.command = list(command)
        self.timeout = timeout
        self._env = {**os.environ, **(env or {})}
        self._cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._stderr: list[str] = []
        self.info = ServerInfo()

    # ── жизненный цикл ──────────────────────────────────────────────────
    def __enter__(self) -> "MCPClient":
        self.start()
        self.initialize()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def start(self) -> None:
        """Поднимает процесс сервера и читалку его stderr."""
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1,
                env=self._env, cwd=self._cwd,
            )
        except FileNotFoundError as exc:
            raise MCPError(f"Не найдена команда: {self.command[0]}") from exc
        except OSError as exc:
            raise MCPError(f"Не запускается сервер: {exc}") from exc

        # stderr читаем в отдельном потоке и складываем в список. Сервера
        # любят писать туда логи; если его не вычитывать, буфер однажды
        # заполнится и сервер встанет намертво посреди работы.
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            self._proc.kill()
        finally:
            self._proc = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def stderr(self) -> str:
        return "".join(self._stderr).strip()

    # ── протокол ────────────────────────────────────────────────────────
    def initialize(self) -> ServerInfo:
        """Рукопожатие: три шага из докстринга модуля."""
        ответ = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
        })
        сведения = ответ.get("serverInfo") or {}
        self.info = ServerInfo(
            name=str(сведения.get("name", "")),
            version=str(сведения.get("version", "")),
            protocol=str(ответ.get("protocolVersion", "")),
            capabilities=ответ.get("capabilities") or {},
            instructions=str(ответ.get("instructions", "")),
        )
        # Уведомление, а не запрос: ответа не будет, ждать его нельзя.
        self._notify("notifications/initialized")
        return self.info

    def list_tools(self) -> list[Tool]:
        """Список инструментов. Приходит страницами, если их много."""
        собрано: list[Tool] = []
        курсор: str | None = None
        while True:
            параметры = {"cursor": курсор} if курсор else {}
            ответ = self._request("tools/list", параметры)
            for сырой in ответ.get("tools") or []:
                собрано.append(Tool(
                    name=str(сырой.get("name", "")),
                    description=str(сырой.get("description", "")),
                    schema=сырой.get("inputSchema") or {},
                ))
            курсор = ответ.get("nextCursor")
            if not курсор:
                return собрано

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Вызвать инструмент и вернуть сырой результат."""
        return self._request("tools/call",
                             {"name": name, "arguments": arguments or {}})

    # ── транспорт ───────────────────────────────────────────────────────
    def _request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        номер = self._next_id
        письмо = {"jsonrpc": "2.0", "id": номер, "method": method}
        if params:
            письмо["params"] = params
        self._write(письмо)

        # Читаем, пока не придёт ответ с НАШИМ номером: сервер вправе
        # прислать по дороге свои уведомления и встречные запросы,
        # и принимать их за ответ нельзя.
        предел = time.monotonic() + self.timeout
        while True:
            письмо = self._read(предел - time.monotonic())
            if письмо.get("id") != номер:
                continue
            if "error" in письмо:
                ошибка = письмо["error"] or {}
                raise MCPError(
                    f"{method}: {ошибка.get('message', 'ошибка')} "
                    f"(код {ошибка.get('code', '?')})")
            return письмо.get("result") or {}

    def _notify(self, method: str, params: dict | None = None) -> None:
        письмо = {"jsonrpc": "2.0", "method": method}
        if params:
            письмо["params"] = params
        self._write(письмо)

    def _write(self, письмо: dict) -> None:
        if not self.alive:
            raise MCPError(f"Сервер не запущен или уже завершился."
                           + (f"\nstderr: {self.stderr[-400:]}" if self.stderr else ""))
        строка = json.dumps(письмо, ensure_ascii=False) + "\n"
        try:
            self._proc.stdin.write(строка)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPError(f"Сервер закрыл соединение: {exc}") from exc

    def _read(self, остаток: float) -> dict:
        """Одна строка ответа. Пустые строки и мусор пропускаем."""
        if остаток <= 0:
            raise MCPError(f"Сервер молчит дольше {self.timeout} с")
        while True:
            строка = self._proc.stdout.readline()
            if строка == "":
                хвост = f"\nstderr: {self.stderr[-400:]}" if self.stderr else ""
                raise MCPError(f"Сервер закрыл вывод (код "
                               f"{self._proc.poll()}).{хвост}")
            строка = строка.strip()
            if not строка:
                continue
            try:
                разобрано = json.loads(строка)
            except json.JSONDecodeError:
                # Не JSON — значит сервер что-то напечатал в stdout мимо
                # протокола. Это его беда, а не наша: пропускаем.
                continue
            if isinstance(разобрано, dict):
                return разобрано

    def _drain_stderr(self) -> None:
        поток = self._proc.stderr if self._proc else None
        if поток is None:
            return
        for строка in поток:
            self._stderr.append(строка)


__all__ = ["MCPClient", "Tool", "ServerInfo", "MCPError", "PROTOCOL_VERSION"]
