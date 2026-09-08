"""Агент — сущность, которая ведёт диалог.

Отличие от llm.py: тот отвечает на вопрос «как отправить HTTP-запрос»,
а этот — «кто отвечает, что он помнит и по каким правилам работает».

Вся возня с API остаётся внутри. Интерфейс (CLI, веб, что угодно) знает
про агента ровно четыре вещи:

    agent.ask("привет")      получить ответ целиком
    agent.stream("привет")   получить ответ кусками
    agent.reset()            забыть диалог
    agent.stats              сколько всего потрачено

Ни HTTP, ни JSON, ни списка messages, ни токенов наружу не торчит.
Проверить легко: в cli.py и web.py нет ни одного импорта из llm.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from llm import LLMError, Provider, ask, ask_stream

# Явно, а не через DEFAULT_MODEL из llm: там стоит алиас deepseek-chat,
# который на самом деле routes на v4-flash (выяснилось в дне 5). Агент
# показывает модель в строке состояния, и врать там не хочется.
DEFAULT_AGENT_MODEL = "deepseek-v4-flash"

DEFAULT_ROLE = (
    "Ты — дружелюбный ассистент-программист. Отвечай по делу и без воды, "
    "код показывай короткими примерами. Если вопрос неоднозначный — "
    "скажи об этом прямо и уточни, а не угадывай."
)


@dataclass
class Stats:
    """Что агент израсходовал за сессию."""

    turns: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Call:
    """Запись в журнале — один поход к модели."""

    question: str
    answer: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float

    @property
    def short(self) -> str:
        head = self.question.replace("\n", " ")
        return head[:47] + "…" if len(head) > 48 else head


class Agent:
    """Собеседник с ролью, памятью и учётом расходов.

    Память — это не магия: модель между запросами не помнит ничего, и весь
    диалог каждый раз отправляется заново. Агент просто хранит список реплик
    и подставляет его сам, чтобы интерфейсу об этом думать не приходилось.
    """

    def __init__(
        self,
        name: str = "Ассистент",
        role: str = DEFAULT_ROLE,
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        memory_turns: int = 10,
        provider: Provider | None = None,
    ) -> None:
        self.name = name
        self.role = role
        self.model = model or DEFAULT_AGENT_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_turns = memory_turns   # сколько пар «вопрос-ответ» помнить
        self.provider = provider

        self._history: list[dict] = []
        self.stats = Stats()
        self.journal: list[Call] = []

    # ── публичный интерфейс ─────────────────────────────────────────────
    def ask(self, message: str) -> str:
        """Задать вопрос и дождаться ответа целиком."""
        message = message.strip()
        if not message:
            raise ValueError("Пустой вопрос")

        answer = ask(
            message,
            system=self.role,
            model=self.model,
            history=self._history,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            provider=self.provider,
        )
        self._remember(message, answer.text,
                       answer.prompt_tokens, answer.completion_tokens, answer.seconds)
        return answer.text

    def stream(self, message: str) -> Iterator[str]:
        """То же самое, но текст отдаётся кусками по мере генерации.

        Память пополняется в конце: пока ответ не дописан, запоминать нечего.
        """
        message = message.strip()
        if not message:
            raise ValueError("Пустой вопрос")

        stats: dict = {}
        chunks: list[str] = []
        started = time.monotonic()

        for delta in ask_stream(
            message,
            system=self.role,
            model=self.model,
            history=self._history,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            provider=self.provider,
            stats=stats,
        ):
            chunks.append(delta)
            yield delta

        usage = stats.get("usage") or {}
        self._remember(
            message, "".join(chunks),
            usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
            round(time.monotonic() - started, 1),
        )

    def reset(self) -> None:
        """Забыть диалог. Роль, настройки и общая статистика остаются."""
        self._history.clear()

    @property
    def remembers(self) -> int:
        """Сколько пар реплик агент сейчас держит в памяти."""
        return len(self._history) // 2

    def describe(self) -> str:
        """Человекочитаемая сводка — для строки состояния в интерфейсе."""
        parts = [
            f"{self.name} · {self.model}",
            f"температура {self.temperature}",
            f"помнит {self.remembers} из {self.memory_turns}",
        ]
        if self.stats.turns:
            parts.append(f"{self.stats.total_tokens} токенов за сессию")
        return " · ".join(parts)

    # ── внутреннее ──────────────────────────────────────────────────────
    def _remember(self, question: str, answer: str,
                  prompt_tokens: int, completion_tokens: int, seconds: float) -> None:
        """Дописывает реплики в память, обновляет счётчики, подрезает старое."""
        self._history.append({"role": "user", "content": question})
        self._history.append({"role": "assistant", "content": answer})
        self._trim()

        self.stats.turns += 1
        self.stats.prompt_tokens += prompt_tokens
        self.stats.completion_tokens += completion_tokens
        self.stats.seconds = round(self.stats.seconds + seconds, 1)
        self.journal.append(
            Call(question, answer, prompt_tokens, completion_tokens, seconds)
        )

    def _trim(self) -> None:
        """Держит в памяти последние memory_turns пар реплик.

        Без этого разговор растёт бесконечно: каждый запрос тащит с собой
        всю переписку, и счёт за токены разгоняется квадратично.
        """
        limit = self.memory_turns * 2
        if len(self._history) > limit:
            del self._history[:len(self._history) - limit]


__all__ = ["Agent", "Call", "Stats", "LLMError", "DEFAULT_ROLE",
           "DEFAULT_AGENT_MODEL"]
