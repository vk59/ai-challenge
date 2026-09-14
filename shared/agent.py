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

День 7 добавил необязательный параметр store: если его передать, диалог
переживает выключение программы. Интерфейс дня 6, который store не передаёт,
работает ровно как раньше — память живёт в процессе и умирает вместе с ним.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from llm import LLMError, Provider, ask, ask_stream
from memory import DEFAULT_SESSION, Store, Turn, now_iso

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

    С хранилищем (store) этих «памятей» становится две, и путать их нельзя:

        _history  окно контекста — что уезжает в модель, последние
                  memory_turns пар, остальное подрезано
        store     архив — весь диалог на диске, переживает перезапуск

    В дне 6 они совпадали, поэтому подрезка означала потерю. Теперь подрезка
    касается только окна: из архива не пропадает ничего.
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
        store: Store | None = None,
        session: str = DEFAULT_SESSION,
    ) -> None:
        self.name = name
        self.role = role
        self.model = model or DEFAULT_AGENT_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_turns = memory_turns   # сколько пар «вопрос-ответ» помнить
        self.provider = provider
        self.store = store
        self.session = session

        self._history: list[dict] = []
        self.stats = Stats()
        self.journal: list[Call] = []
        self._restore()

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

        try:
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
        finally:
            # finally, а не просто «после цикла»: если ответ оборвали на
            # середине (Ctrl+C, закрытая вкладка, сетевой сбой), сохранить
            # надо то, что успело прийти. Иначе перезапуск покажет диалог
            # с дырой — вопрос есть, ответа нет.
            if chunks:
                usage = stats.get("usage") or {}
                self._remember(
                    message, "".join(chunks),
                    usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                    round(time.monotonic() - started, 1),
                )

    def reset(self) -> None:
        """Забыть диалог — и в окне контекста, и в архиве.

        Со хранилищем «забыть» обязано означать «стереть с диска»: иначе
        после перезапуска забытое вернулось бы, и кнопка врала бы.
        """
        self._history.clear()
        self.journal.clear()
        self.stats = Stats()
        if self.store:
            self.store.clear(self.session)

    def switch(self, session: str) -> None:
        """Перейти в другой диалог. Текущий остаётся на диске нетронутым."""
        self.session = session or DEFAULT_SESSION
        self._restore()

    def transcript(self) -> list[Turn]:
        """Весь архив сессии — для интерфейса, а не для модели.

        Разница принципиальная: интерфейс показывает всё, что было, модель
        получает только окно. Одно и то же на экране и в запросе — это как
        раз то, чего в дне 7 больше нет.
        """
        return self.store.load(self.session) if self.store else []

    @property
    def remembers(self) -> int:
        """Сколько пар реплик уезжает в модель на следующем запросе."""
        return len(self._history) // 2

    @property
    def archived(self) -> int:
        """Сколько пар лежит в архиве — включая забытые окном."""
        return self.stats.turns

    @property
    def persistent(self) -> bool:
        return self.store is not None

    def describe(self) -> str:
        """Человекочитаемая сводка — для строки состояния в интерфейсе."""
        parts = [
            f"{self.name} · {self.model}",
            f"температура {self.temperature}",
            f"помнит {self.remembers} из {self.memory_turns}",
        ]
        if self.store:
            parts.append(f"диалог «{self.session}» · {self.archived} пар в архиве")
            parts.append(str(self.store))
        if self.stats.turns:
            parts.append(f"{self.stats.total_tokens} токенов")
        return " · ".join(parts)

    # ── внутреннее ──────────────────────────────────────────────────────
    def _restore(self) -> None:
        """Поднимает сессию с диска. Без хранилища — просто пустой старт.

        В окно контекста поднимаем только последние memory_turns пар: тащить
        в модель весь архив нельзя ни по деньгам, ни по размеру запроса.
        Счётчики при этом берутся по всему архиву — расход-то был реальный.
        """
        self._history.clear()
        self.journal.clear()
        self.stats = Stats()
        if not self.store:
            return

        window = self.store.load(self.session, limit=self.memory_turns * 2)
        self._history.extend(turn.message for turn in window)

        totals = self.store.totals(self.session)
        self.stats = Stats(totals.turns, totals.prompt_tokens,
                           totals.completion_tokens, totals.seconds)

        # Журнал восстанавливаем по окну: это то, что агент реально помнит.
        for question, answer in zip(window[0::2], window[1::2]):
            self.journal.append(Call(question.content, answer.content,
                                     question.tokens, answer.tokens, answer.seconds))

    def _remember(self, question: str, answer: str,
                  prompt_tokens: int, completion_tokens: int, seconds: float) -> None:
        """Дописывает реплики в окно и в архив, обновляет счётчики."""
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

        if self.store:
            # Пишем сразу, парой. Откладывать до выхода нельзя: программу
            # закрывают не только через пункт меню «Выйти».
            at = now_iso()
            self.store.append(self.session,
                              Turn("user", question, at, prompt_tokens, 0.0))
            self.store.append(self.session,
                              Turn("assistant", answer, at, completion_tokens, seconds))

    def _trim(self) -> None:
        """Держит в окне последние memory_turns пар реплик.

        Без этого разговор растёт бесконечно: каждый запрос тащит с собой
        всю переписку, и счёт за токены разгоняется квадратично. С дня 7
        подрезка перестала быть потерей — подрезанное лежит в архиве.
        """
        limit = self.memory_turns * 2
        if len(self._history) > limit:
            del self._history[:len(self._history) - limit]


__all__ = ["Agent", "Call", "Stats", "LLMError", "DEFAULT_ROLE",
           "DEFAULT_AGENT_MODEL"]
