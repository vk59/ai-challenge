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
from memory import DEFAULT_SESSION, Store, Summary, Turn, now_iso
from tokens import cost, estimate_request, limit_of, money

# Промпт сжатия. Требования к нему жёсткие и неочевидные: пересказ должен
# сохранять то, о чём агента потом спросят, — имена, числа, договорённости,
# решения. Красивый связный текст, из которого вынуты факты, здесь хуже
# сухого списка: диалог продолжится, и по этому тексту придётся отвечать.
COMPRESS_ROLE = (
    "Ты сжимаешь историю диалога, чтобы она заняла меньше места, но осталась "
    "пригодной для продолжения разговора. Сохрани обязательно: имена, названия, "
    "числа, даты, принятые решения, задачи и предпочтения собеседника. "
    "Выброси приветствия, вежливость, рассуждения и повторы. "
    "Пиши сухими короткими пунктами от третьего лица, без вступления и выводов. "
    "Если в старом пересказе уже есть факты — перенеси их в новый, не потеряв."
)

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

    # День 9: сжатие не бесплатно. Каждая свёртка — отдельный запрос
    # к модели, и считать его надо отдельно, иначе «экономия» окажется
    # бухгалтерией, в которой спрятаны расходы.
    #
    # Вход и выход раздельно не для красоты: они стоят разных денег
    # (у flash выход вдвое дороже), и одним числом цену не посчитать.
    compressions: int = 0
    compression_prompt_tokens: int = 0
    compression_completion_tokens: int = 0

    @property
    def compression_tokens(self) -> int:
        return self.compression_prompt_tokens + self.compression_completion_tokens

    @property
    def dialogue_tokens(self) -> int:
        """Только сам диалог, без накладных расходов на сжатие."""
        return self.prompt_tokens + self.completion_tokens

    @property
    def total_tokens(self) -> int:
        return self.dialogue_tokens + self.compression_tokens

    @property
    def average_prompt(self) -> float:
        """Средний вес запроса. Растёт по мере диалога — в этом вся суть."""
        return self.prompt_tokens / self.turns if self.turns else 0.0


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
        compress: bool = False,
        keep_last: int = 3,
        summarize_every: int = 5,
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

        # День 9: вместо того чтобы выбрасывать вытесненные пары, сворачиваем
        # их в пересказ. keep_last пар всегда живут «как есть», а как только
        # сверх них накопится summarize_every — эта пачка уходит на сжатие.
        # Пачкой, а не по одной: иначе на каждую реплику приходился бы лишний
        # запрос к модели, и лечение вышло бы дороже болезни.
        self.compress = compress
        self.keep_last = keep_last
        self.summarize_every = summarize_every
        self.summary: Summary | None = None

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
            system=self._system(),
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
                system=self._system(),
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
        self.summary = None
        if self.store:
            self.store.clear(self.session)

    def switch(self, session: str) -> None:
        """Перейти в другой диалог. Текущий остаётся на диске нетронутым."""
        self.session = session or DEFAULT_SESSION
        self._restore()

    def weigh(self, message: str = "") -> dict:
        """Сколько будет стоить следующий запрос — ДО того, как он отправлен.

        Точных чисел до отправки взять неоткуда, это оценка (см. tokens.py).
        Нужна она затем, чтобы узнать цену заранее: влезет ли в окно модели,
        во что обойдётся реплика, не пора ли подрезать историю.

        Разложено по частям намеренно. Обычно удивляет не размер вопроса,
        а то, что роль и вся история оплачиваются заново в каждом запросе.
        """
        from tokens import MESSAGE_OVERHEAD, REQUEST_OVERHEAD, estimate_text

        role = estimate_text(self.role) + MESSAGE_OVERHEAD if self.role else 0
        # Пересказ уезжает рядом с ролью и place в счёте занимает наравне
        # с ней — но ВМЕСТО свёрнутых реплик, которых здесь уже нет.
        summary = (estimate_text(self.summary.content)
                   if self.summary and self.summary.content else 0)
        history = sum(estimate_text(item.get("content", "")) + MESSAGE_OVERHEAD
                      for item in self._history)
        question = estimate_text(message)
        total = REQUEST_OVERHEAD + role + summary + history + question

        limit = limit_of(self.model)
        return {
            "overhead": REQUEST_OVERHEAD,
            "role": role,
            "summary": summary,
            "history": history,
            "question": question,
            "total": total,
            "limit": limit,
            "share": (total / limit) if limit else 0.0,
            "cost": cost(self.model, total, 0),
        }

    @property
    def spent(self) -> float:
        """Сколько денег утекло за диалог, в долларах — вместе со сжатием."""
        return cost(self.model,
                    self.stats.prompt_tokens + self.stats.compression_prompt_tokens,
                    self.stats.completion_tokens
                    + self.stats.compression_completion_tokens)

    @property
    def spent_on_compression(self) -> float:
        """Во что обошлось само сжатие. Показывать обязательно.

        Экономия, из которой не вычтены накладные расходы, — это не экономия,
        а способ их спрятать. На коротком диалоге сжатие вполне может стоить
        дороже, чем сберегает.
        """
        return cost(self.model, self.stats.compression_prompt_tokens,
                    self.stats.compression_completion_tokens)

    @property
    def spent_pretty(self) -> str:
        return money(self.spent)

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
        if self.compress:
            # Берём числа из пересказа, а не из stats: stats.compressions —
            # счётчик текущего сеанса, он обнуляется при перезапуске, а
            # summary.rounds лежит на диске и считает за всё время. Иначе
            # после перезапуска «0 свёрток» соседствовало бы с пересказом
            # из шести свёрнутых пар.
            свёрнуто = self.summary.covered if self.summary else 0
            свёрток = self.summary.rounds if self.summary else 0
            parts.append(f"сжатие вкл · свёрнуто {свёрнуто} пар "
                         f"за {свёрток} свёрток")
        if self.store:
            parts.append(f"диалог «{self.session}» · {self.archived} пар в архиве")
            parts.append(str(self.store))
        if self.stats.turns:
            parts.append(f"{self.stats.total_tokens} токенов")
        return " · ".join(parts)

    # ── сжатие истории (день 9) ─────────────────────────────────────────
    def _system(self) -> str:
        """Системный промпт: роль, а с дня 9 — ещё и пересказ свёрнутого.

        Пересказ идёт именно сюда, а не в список сообщений. Он не реплика
        диалога: непонятно, от чьего имени он был бы сказан, и модель начала
        бы считать его частью разговора. Место ему рядом с ролью — это
        справка о том, что было раньше.
        """
        if not self.summary or not self.summary.content:
            return self.role
        return (f"{self.role}\n\n"
                f"[Ранее в этом диалоге, сжатый пересказ]\n{self.summary.content}")

    def _compress(self, doomed: list[dict]) -> None:
        """Сворачивает пачку вытесненных пар в пересказ.

        Накопительно: старый пересказ идёт в запрос вместе с новыми репликами.
        Без этого второе сжатие потеряло бы всё, что запомнило первое, —
        и агент забывал бы начало разговора ровно так же, как без сжатия,
        только ещё и за деньги.
        """
        if not doomed:
            return

        куски = []
        if self.summary and self.summary.content:
            куски.append(f"Пересказ более раннего:\n{self.summary.content}")
        реплики = "\n".join(
            f"{'Пользователь' if m['role'] == 'user' else 'Ассистент'}: {m['content']}"
            for m in doomed
        )
        куски.append(f"Новые реплики, которые надо добавить к пересказу:\n{реплики}")

        answer = ask(
            "\n\n".join(куски),
            system=COMPRESS_ROLE,
            model=self.model,
            # Низкая температура намеренно: пересказ должен быть скучным
            # и точным. Разнообразие здесь — это выдуманные подробности.
            temperature=0.2,
            provider=self.provider,
        )

        было = self.summary
        self.summary = Summary(
            content=answer.text.strip(),
            at=now_iso(),
            covered=(было.covered if было else 0) + len(doomed) // 2,
            tokens=(было.tokens if было else 0) + answer.prompt_tokens
            + answer.completion_tokens,
            rounds=(было.rounds if было else 0) + 1,
        )

        self.stats.compressions += 1
        self.stats.compression_prompt_tokens += answer.prompt_tokens
        self.stats.compression_completion_tokens += answer.completion_tokens

        if self.store:
            self.store.save_summary(self.session, self.summary)

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
        self.summary = None
        if not self.store:
            return

        # Пересказ поднимаем первым: без него агент после перезапуска
        # оказался бы с обрывком окна и без всего, что было свёрнуто.
        self.summary = self.store.load_summary(self.session)

        # В режиме сжатия размер окна задаётся не memory_turns, а парой
        # keep_last + summarize_every — иначе после перезапуска окно
        # оказалось бы другого размера, чем до него.
        глубина = ((self.keep_last + self.summarize_every) if self.compress
                   else self.memory_turns)
        window = self.store.load(self.session, limit=глубина * 2)
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
        """Держит окно в рамках. Как именно — зависит от режима.

        Без сжатия (дни 6–8): лишнее просто выпадает из окна, оставаясь
        в архиве на диске.

        Со сжатием (день 9): выпадающее сначала сворачивается в пересказ
        и только потом покидает окно. Держим keep_last пар «как есть»,
        а накопившуюся сверх них пачку в summarize_every пар отправляем
        на свёртку — пачкой, а не по одной, иначе на каждую реплику
        приходился бы лишний запрос к модели.
        """
        if not self.compress:
            limit = self.memory_turns * 2
            if len(self._history) > limit:
                del self._history[:len(self._history) - limit]
            return

        порог = (self.keep_last + self.summarize_every) * 2
        if len(self._history) <= порог:
            return

        сколько = self.summarize_every * 2
        try:
            self._compress(self._history[:сколько])
        except LLMError:
            # Сжатие не удалось — сеть, лимит, что угодно. Реплики НЕ трогаем:
            # иначе сетевой сбой означал бы потерю памяти. Окно временно
            # побудет шире, зато ничего не пропадёт, и на следующей реплике
            # попробуем снова.
            return

        del self._history[:сколько]


__all__ = ["Agent", "Call", "Stats", "LLMError", "DEFAULT_ROLE",
           "DEFAULT_AGENT_MODEL"]
