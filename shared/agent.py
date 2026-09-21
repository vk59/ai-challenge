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

import json as _json

from llm import LLMError, Provider, ask, ask_stream
from memory import (DECISION, DEFAULT_SESSION, GUARDS, KNOWLEDGE, LONG,
                    LONG_KINDS, PLANNING, PROFILE, SHORT, STAGE_LABELS, STAGES,
                    WORKING, Fact, Invariant, Memo, Profile, Store, Summary,
                    TaskState, Turn, now_iso)
from tokens import cost, estimate_request, limit_of, money

# Стратегии управления контекстом (день 10).
STRATEGIES = ("window", "facts", "branch")

# Промпт извлечения фактов. Просим вернуть карточку ЦЕЛИКОМ, а не дельту:
# модель сама решает, что обновить, что добавить, а что выбросить. Дельту
# пришлось бы сливать руками, и на конфликтах («бюджет 800» против
# «бюджет подняли до миллиона») это ломалось бы молча.
FACTS_ROLE = (
    "Ты ведёшь карточку фактов о проекте по ходу диалога. "
    "Верни JSON — плоский объект «ключ: значение», значения только строки. "
    "Держи в нём то, что понадобится дальше: цель, ограничения, бюджет, сроки, "
    "принятые решения, предпочтения и договорённости. "
    "Если факт изменился — замени значение, не плоди дубли. "
    "Если факт отменён — убери ключ. Ничего не выдумывай: только то, "
    "что прямо сказано в диалоге. Ключи короткие, по-русски."
)

# У deepseek-v4-flash есть скрытое поле reasoning_content: модель думает
# перед ответом, и это думание тратит бюджет max_tokens. При тесном лимите
# всё уходит в размышление, а content приходит ПУСТЫМ с finish=length.
# Отсюда запас: полторы тысячи там, где видимого текста на две сотни.
FACTS_MAX_TOKENS = 1500

# День 13: модель докладывает, где задача. Обратите внимание, чего здесь
# НЕТ: права переводить этап куда вздумается. Модель лишь предлагает,
# а допустим ли переход — решает автомат в TRANSITIONS. Иначе «конечный
# автомат» выродился бы в ярлык, который модель меняет как хочет.
# День 14: аудит ответа на нарушение инвариантов.
#
# Зачем он нужен, если инварианты уже в системном промпте. Затем, что блок
# в промпте — это просьба, а не гарантия: модель может его не выполнить,
# и без проверки мы об этом не узнаем. Аудит превращает «ассистент должен
# соблюдать» в «видно, соблюдает ли».
#
# Проверяющий намеренно не видит системного промпта с инвариантами в том же
# виде — ему даются только текст ограничений и ответ. Так он не поддаётся
# формулировкам исходной инструкции и судит по тому, что написано.
AUDITOR_ROLE = (
    "Ты проверяешь ответ ассистента на нарушение жёстких ограничений проекта. "
    "Нарушение — это когда ответ ПРЕДЛАГАЕТ, РЕКОМЕНДУЕТ или ОПИСЫВАЕТ КАК "
    "СДЕЛАТЬ то, что ограничение запрещает. "
    "Упоминание запрещённого при объяснении отказа нарушением НЕ является: "
    "фраза «MongoDB использовать нельзя, потому что…» — это соблюдение, "
    "а не нарушение.\n\n"
    "Верни JSON: {\"violations\": [{\"id\": номер_ограничения, "
    "\"quote\": \"цитата из ответа\", \"why\": \"чем нарушает\"}]}. "
    "Если нарушений нет — пустой массив. Ничего не выдумывай."
)

TRACKER_ROLE = (
    "Ты следишь за состоянием рабочей задачи. Верни JSON с ключами:\n"
    '  "stage" — предлагаемый этап: planning, execution, validation или done;\n'
    '  "step" — чем конкретно заняты прямо сейчас, одной строкой;\n'
    '  "expecting" — чего ждём дальше и от кого (от пользователя или от '
    "ассистента), одной строкой;\n"
    '  "goal" — что за задача решается, одной строкой (если уже понятно).\n\n'
    "Этап меняй только когда это видно из разговора: planning — обсуждаем "
    "и решаем, как делать; execution — делаем; validation — проверяем "
    "сделанное; done — задача закрыта и подтверждена. "
    "Если этап не изменился — верни текущий. Ничего не выдумывай."
)

# День 11: разбор реплики по слоям памяти. Модель не просто извлекает факты,
# а СРАЗУ говорит, куда их класть, — это и есть «явно выбирать, что и куда
# сохраняется». Решение принимается по одному признаку: переживёт ли факт
# текущую задачу.
ROUTER_ROLE = (
    "Ты раскладываешь информацию из диалога по слоям памяти ассистента. "
    "Верни JSON с двумя ключами: \"working\" и \"long\".\n\n"
    "working — плоский объект «ключ: значение» с данными ТЕКУЩЕЙ задачи: "
    "требования, цифры, сроки, статус. Всё, что перестанет быть нужным, "
    "когда задача закончится.\n\n"
    "long — массив объектов {\"key\", \"value\", \"kind\"}, где kind это "
    "profile, decision или knowledge. Сюда идёт только то, что пригодится "
    "и в ДРУГИХ разговорах: profile — про самого человека (имя, роль, "
    "предпочтения, как с ним общаться); decision — принятое решение, "
    "которое действует дальше; knowledge — устойчивый факт о мире или "
    "продукте.\n\n"
    "Если для слоя ничего нет — верни пустой объект или пустой массив. "
    "Ничего не выдумывай. Ключи короткие, по-русски.\n\n"
    "ЗАПРЕЩЕНО записывать:\n"
    "- что-либо про самого ассистента, его устройство, память или возможности "
    "(«ассистент не умеет…», «сохранение возможно только…») — это не факт "
    "о задаче и не факт о человеке;\n"
    "- значения-заглушки: «не указано», «неизвестно», «уточняется», «нет данных» "
    "— отсутствие факта не факт, просто не создавай ключ;\n"
    "- служебную мету о ходе разговора: «текущий вопрос», «статус», «ждём "
    "ответа», «пользователь спросил»;\n"
    "- один и тот же смысл под разными ключами: «стек» и «язык» — это один "
    "ключ, выбери его и держись."
)

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

    # День 10: извлечение фактов — тоже отдельный запрос на каждую реплику,
    # и тоже за деньги. Считается своей строкой по той же причине.
    extractions: int = 0
    extraction_prompt_tokens: int = 0
    extraction_completion_tokens: int = 0

    @property
    def compression_tokens(self) -> int:
        return self.compression_prompt_tokens + self.compression_completion_tokens

    @property
    def extraction_tokens(self) -> int:
        return self.extraction_prompt_tokens + self.extraction_completion_tokens

    @property
    def overhead_tokens(self) -> int:
        """Всё, что потрачено НЕ на сам диалог: свёртки и извлечение фактов."""
        return self.compression_tokens + self.extraction_tokens

    @property
    def dialogue_tokens(self) -> int:
        """Только сам диалог, без служебных запросов."""
        return self.prompt_tokens + self.completion_tokens

    @property
    def total_tokens(self) -> int:
        return self.dialogue_tokens + self.overhead_tokens

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
        strategy: str = "window",
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

        # День 10. Стратегия решает, ЧТО именно уезжает в модель вместо
        # выброшенной истории: ничего (window), карточка фактов (facts)
        # или — при ветвлении — история той ветки, в которой мы сейчас.
        if strategy not in STRATEGIES:
            raise ValueError(f"Неизвестная стратегия: {strategy!r}. "
                             f"Ожидалось одно из {STRATEGIES}")
        self.strategy = strategy
        self.facts: list[Fact] = []

        # День 11: третий слой. facts (рабочая память) живут в сессии
        # и умирают вместе с задачей, memos (долговременная) — вне сессий
        # и переезжают в следующий диалог. Это не формат, это область жизни.
        self.memos: list[Memo] = []
        self.layered = False        # включает раскладку по слоям при ответе

        # День 12: профиль — это не память, а настройка. Память агент
        # набирает сам, профиль задаётся сознательно и меняется одним
        # переключением, в том числе посреди разговора.
        self.profile: Profile | None = None

        # День 13: где сейчас задача. Хранится отдельно от диалога, поэтому
        # переживает и вытеснение окна, и перезапуск — на этом держится
        # «пауза на любом этапе и продолжение без повторных объяснений».
        self.task: TaskState | None = None
        self.tracking = False       # включает автослежение за этапом

        # День 15: условия на рёбрах и права этапа. Выключены по умолчанию
        # НАМЕРЕННО. День 13 — уже сданная работа с тем же автоматом, и если
        # включить условия глобально, его переход «планирование → выполнение»
        # окажется заперт навсегда: команды утверждения там нет. Новый
        # механизм не должен ломать старый день.
        self.guards = False

        # День 14: чего агенту нельзя. Инварианты живут вне сессий —
        # архитектура и стек одни на весь проект.
        self.invariants: list[Invariant] = []
        self.auditing = False       # включает проверку ответов на нарушения
        self.last_audit: dict = {}  # результат последней проверки

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
        self.facts = []
        # Задача стирается вместе с чатом: чат и есть задача.
        self.task = None
        # memos НЕ трогаем: «стереть диалог» не должно означать «забыть
        # человека». Для долговременной памяти есть отдельный forget().
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
        facts = sum(estimate_text(f"- {f.key}: {f.value}") for f in self.facts)
        memos = sum(estimate_text(f"- {m.key}: {m.value}") for m in self.memos)
        profile = estimate_text(self.profile.as_prompt()) if self.profile else 0
        task = estimate_text(self.task.as_prompt()) if self.task else 0
        invariants = estimate_text(self._invariants_prompt())
        history = sum(estimate_text(item.get("content", "")) + MESSAGE_OVERHEAD
                      for item in self._history)
        question = estimate_text(message)
        total = (REQUEST_OVERHEAD + role + invariants + profile + task + summary
                 + facts + memos + history + question)

        limit = limit_of(self.model)
        return {
            "overhead": REQUEST_OVERHEAD,
            "role": role,
            "invariants": invariants,
            "profile": profile,
            "task": task,
            "summary": summary,
            "facts": facts,
            "memos": memos,
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
        куски = [self.role]
        # Инварианты идут ПЕРВЫМИ, вперёд всего остального. Это не справка
        # и не пожелание: если они окажутся в конце длинного промпта, между
        # пересказом и списком фактов, шансов, что модель их соблюдёт,
        # заметно меньше.
        блок = self._invariants_prompt()
        if блок:
            куски.append(блок)
        # Профиль идёт сразу за ролью, ПЕРЕД всякой памятью: это указания
        # о форме ответа, и они должны действовать независимо от того,
        # что агент успел запомнить.
        if self.profile:
            блок = self.profile.as_prompt()
            if блок:
                куски.append(блок)
        # Состояние задачи идёт перед памятью: для текущего ответа важнее
        # знать, на каком мы этапе, чем что обсуждали сорок реплик назад.
        if self.task:
            куски.append(self.task.as_prompt(with_rights=self.guards))
        if self.summary and self.summary.content:
            куски.append("[Ранее в этом диалоге, сжатый пересказ]\n"
                         + self.summary.content)
        # Долговременная память идёт ПЕРЕД рабочей: она про человека и про
        # действующие решения, и на неё агент должен опираться, даже когда
        # задача сменилась.
        if self.memos:
            по_видам: dict[str, list[Memo]] = {}
            for m in self.memos:
                по_видам.setdefault(m.kind, []).append(m)
            подписи = {PROFILE: "О собеседнике", DECISION: "Действующие решения",
                       KNOWLEDGE: "Что известно"}
            for вид in LONG_KINDS:
                if вид in по_видам:
                    строки = "\n".join(f"- {m.key}: {m.value}" for m in по_видам[вид])
                    куски.append(f"[{подписи[вид]}]\n{строки}")
        if self.facts:
            # Факты идут туда же, куда пересказ, и по той же причине: это
            # справка о диалоге, а не реплика в нём.
            карточка = "\n".join(f"- {f.key}: {f.value}" for f in self.facts)
            куски.append(f"[Текущая задача]\n{карточка}")
        return "\n\n".join(куски)

    # ── слои памяти (день 11) ───────────────────────────────────────────
    def remember(self, key: str, value: str, *, layer: str = LONG,
                 kind: str = PROFILE) -> None:
        """Положить что-то в память ЯВНО, указав слой своими руками.

        Автоматическая раскладка ошибается, и должен быть способ поправить
        её руками — иначе «модель памяти» превращается в чёрный ящик.
        """
        if layer == WORKING:
            карта = {f.key: f for f in self.facts}
            карта[key] = Fact(key, value, now_iso())
            self.facts = sorted(карта.values(), key=lambda f: f.key)
            if self.store:
                self.store.save_facts(self.session, self.facts)
            return
        if layer == LONG:
            memo = Memo(key, value, kind, now_iso(), self.session)
            карта = {m.key: m for m in self.memos}
            карта[key] = memo
            self.memos = sorted(карта.values(), key=lambda m: (m.kind, m.key))
            if self.store:
                self.store.remember(memo)
            return
        raise ValueError(f"В слой {layer!r} писать напрямую нельзя: "
                         f"краткосрочная память — это сами реплики")

    def forget(self, key: str) -> bool:
        """Забыть запись долговременной памяти навсегда."""
        было = len(self.memos)
        self.memos = [m for m in self.memos if m.key != key]
        стёрли = len(self.memos) != было
        if self.store and self.store.forget(key):
            стёрли = True
        return стёрли

    def layers(self) -> dict:
        """Снимок всех трёх слоёв — для интерфейса и для проверки.

        Главное, что здесь видно: у слоёв разная ОБЛАСТЬ. Краткосрочная
        и рабочая привязаны к сессии, долговременная — нет.
        """
        return {
            SHORT: {
                "область": f"диалог «{self.session}»",
                "в окне": self.remembers,
                "потолок": self.memory_turns,
                "в архиве": self.archived,
                "реплики": [
                    {"role": m["role"], "content": m["content"]}
                    for m in self._history
                ],
            },
            WORKING: {
                "область": f"диалог «{self.session}»",
                "записей": len(self.facts),
                "items": [{"key": f.key, "value": f.value} for f in self.facts],
            },
            LONG: {
                "область": "все диалоги",
                "записей": len(self.memos),
                "items": [{"key": m.key, "value": m.value, "kind": m.kind,
                           "source": m.source} for m in self.memos],
            },
        }

    def _route(self, question: str, answer: str) -> dict:
        """Раскладывает свежую реплику по слоям и возвращает, что куда легло.

        Один запрос к модели на обе цели сразу: и рабочая память, и
        долговременная. Два отдельных запроса стоили бы вдвое дороже,
        а решение принимается по одному и тому же признаку — переживёт ли
        факт текущую задачу.
        """
        рабочее = "\n".join(f"{f.key}: {f.value}" for f in self.facts) or "(пусто)"
        долгое = "\n".join(f"{m.kind}/{m.key}: {m.value}" for m in self.memos) or "(пусто)"
        запрос = (f"Рабочая память (текущая задача):\n{рабочее}\n\n"
                  f"Долговременная память:\n{долгое}\n\n"
                  f"Новая реплика пользователя:\n{question}\n\n"
                  f"Ответ ассистента:\n{answer}\n\n"
                  f"Разложи по слоям и верни JSON.")

        ответ = ask(запрос, system=ROUTER_ROLE, model=self.model, json_mode=True,
                    temperature=0.1, max_tokens=FACTS_MAX_TOKENS,
                    provider=self.provider)

        self.stats.extractions += 1
        self.stats.extraction_prompt_tokens += ответ.prompt_tokens
        self.stats.extraction_completion_tokens += ответ.completion_tokens

        сырой = (ответ.text or "").strip()
        if not сырой:
            return {"working": [], "long": []}
        try:
            разобрано = _json.loads(сырой)
        except _json.JSONDecodeError:
            return {"working": [], "long": []}
        if not isinstance(разобрано, dict):
            return {"working": [], "long": []}

        сейчас = now_iso()
        новое = {"working": [], "long": []}

        рабочие = разобрано.get("working")
        if isinstance(рабочие, dict) and рабочие:
            self.facts = [Fact(str(k), str(v), сейчас)
                          for k, v in рабочие.items() if str(v).strip()]
            новое["working"] = [f.key for f in self.facts]
            if self.store:
                self.store.save_facts(self.session, self.facts)

        долгие = разобрано.get("long")
        if isinstance(долгие, list):
            карта = {m.key: m for m in self.memos}
            for запись in долгие:
                if not isinstance(запись, dict):
                    continue
                ключ = str(запись.get("key", "")).strip()
                значение = str(запись.get("value", "")).strip()
                вид = str(запись.get("kind", PROFILE)).strip()
                if not ключ or not значение:
                    continue
                if вид not in LONG_KINDS:
                    вид = KNOWLEDGE
                memo = Memo(ключ, значение, вид, сейчас, self.session)
                карта[ключ] = memo
                новое["long"].append(f"{вид}/{ключ}")
                if self.store:
                    self.store.remember(memo)
            self.memos = sorted(карта.values(), key=lambda m: (m.kind, m.key))

        return новое

    # ── факты, стратегия «Sticky Facts» (день 10) ───────────────────────
    def _extract_facts(self, question: str, answer: str) -> None:
        """Обновляет карточку фактов после реплики пользователя.

        Отдельный запрос к модели на каждую реплику — да, это дорого, и
        в статистике оно лежит отдельной строкой. Зато карточка обновляется
        по ключу: «бюджет 800 тысяч» сменяется на «бюджет миллион», а не
        соседствует с ним, как было бы в пересказе дня 9.
        """
        текущие = ("\n".join(f"{f.key}: {f.value}" for f in self.facts)
                   or "(пока пусто)")
        запрос = (f"Текущая карточка фактов:\n{текущие}\n\n"
                  f"Новая реплика пользователя:\n{question}\n\n"
                  f"Ответ ассистента:\n{answer}\n\n"
                  f"Верни обновлённую карточку целиком, в JSON.")

        answer_obj = ask(
            запрос,
            system=FACTS_ROLE,
            model=self.model,
            json_mode=True,
            temperature=0.1,      # карточка фактов должна быть скучной
            max_tokens=FACTS_MAX_TOKENS,
            provider=self.provider,
        )

        self.stats.extractions += 1
        self.stats.extraction_prompt_tokens += answer_obj.prompt_tokens
        self.stats.extraction_completion_tokens += answer_obj.completion_tokens

        сырой = (answer_obj.text or "").strip()
        if not сырой:
            # Пустой content при finish=length — размышление съело бюджет.
            # Это не повод терять уже набранные факты: оставляем как было.
            return
        try:
            разобрано = _json.loads(сырой)
        except _json.JSONDecodeError:
            return
        if not isinstance(разобрано, dict):
            return

        сейчас = now_iso()
        self.facts = [Fact(str(k), str(v), сейчас) for k, v in разобрано.items()
                      if str(v).strip()]
        if self.store:
            self.store.save_facts(self.session, self.facts)

    # ── ветвление, стратегия «Branching» (день 10) ──────────────────────
    def checkpoint(self) -> int:
        """Текущая точка диалога — номер реплики, от которой можно ветвиться."""
        return len(self.transcript())

    def fork(self, branch: str, upto: int | None = None) -> int:
        """Создать ветку от точки upto (по умолчанию — от текущего места).

        Ветка получает копию истории до точки развилки и наследует факты
        с пересказом. Дальше живёт сама: дописывание в одну ветку не видно
        в другой. Сам агент при этом НЕ переключается — для этого switch().
        """
        if not self.store:
            raise LLMError("Ветвление требует хранилища: агент создан без store")
        точка = self.checkpoint() if upto is None else upto
        return self.store.fork(self.session, branch, точка)

    def branches(self) -> list[str]:
        """Все сохранённые диалоги — ветки среди них живут на равных."""
        return [s.name for s in self.store.sessions()] if self.store else []

    def chats(self) -> list[dict]:
        """Список диалогов для интерфейса: с заголовком и размером.

        Заголовок берётся из первой реплики пользователя. Имя сессии —
        это идентификатор, а человеку нужен смысл: «диалог-3» ничего
        не говорит, «Собираем ТЗ на склад» — говорит.
        """
        if not self.store:
            return []
        найдено = [
            {"name": s.name, "title": s.title, "pairs": s.pairs,
             "updated": s.when, "active": s.name == self.session}
            for s in self.store.sessions()
        ]
        # Текущий диалог может быть ещё пустым — в sessions() его нет,
        # потому что там нет ни одной реплики. Но в списке он обязан быть,
        # иначе после создания чат пропадает с глаз до первой реплики.
        if not any(c["name"] == self.session for c in найдено):
            найдено.insert(0, {"name": self.session, "title": self.session,
                               "pairs": 0, "updated": "", "active": True})
        return найдено

    # ── инварианты (день 14) ────────────────────────────────────────────
    def _invariants_prompt(self) -> str:
        """Блок ограничений. Формулировка намеренно жёсткая и с инструкцией.

        Просто перечислить ограничения мало: модель воспримет их как
        справку и будет «учитывать». Нужно прямо сказать, что делать
        при конфликте, — иначе она вежливо предложит запрещённое
        с оговоркой «хотя у вас вроде нельзя».
        """
        живые = [i for i in self.invariants if i.active]
        if not живые:
            return ""
        строки = []
        for i in живые:
            строка = f"{i.id}. [{i.scope_label}] {i.text}"
            if i.rationale:
                строка += f"\n   причина: {i.rationale}"
            строки.append(строка)
        return (
            "[НЕРУШИМЫЕ ОГРАНИЧЕНИЯ ПРОЕКТА]\n"
            "Это уже принятые решения. Они не обсуждаются и не пересматриваются "
            "в ответе. Твои обязанности:\n"
            "1. Учитывать их в каждом ответе.\n"
            "2. Если просьба требует нарушить ограничение — ОТКАЗАТЬСЯ "
            "предлагать такое решение.\n"
            "3. В отказе назвать номер и текст ограничения, объяснить причину "
            "и предложить вариант В ЕГО РАМКАХ.\n"
            "4. Не предлагать обходные пути, которые нарушают ограничение "
            "по сути.\n\n"
            + "\n".join(строки)
        )

    def add_invariant(self, text: str, *, rationale: str = "",
                      scope: str = "stack") -> Invariant | None:
        """Завести ограничение."""
        text = text.strip()
        if not text or not self.store:
            return None
        inv = self.store.save_invariant(
            Invariant(text=text, rationale=rationale.strip(), scope=scope,
                      active=True, at=now_iso()))
        self.invariants = self.store.invariants()
        return inv

    def toggle_invariant(self, inv_id: int, active: bool) -> bool:
        """Включить или отключить ограничение, не удаляя его.

        Отключение нужно для демонстрации: один и тот же вопрос с живым
        инвариантом и без него — самый наглядный способ показать, что он
        вообще работает.
        """
        if not self.store:
            return False
        найден = next((i for i in self.store.invariants() if i.id == inv_id), None)
        if найден is None:
            return False
        найден.active = active
        self.store.save_invariant(найден)
        self.invariants = self.store.invariants()
        return True

    def drop_invariant(self, inv_id: int) -> bool:
        if not self.store:
            return False
        получилось = self.store.delete_invariant(inv_id)
        self.invariants = self.store.invariants()
        return получилось

    def audit(self, answer: str) -> dict:
        """Проверяет ответ на нарушение инвариантов.

        Возвращает {"ok": bool, "violations": [...]}. Пустой список нарушений
        при живых инвариантах — это и есть «соблюдает»; непустой — пойманное
        нарушение, которое надо показать, а не замолчать.
        """
        живые = [i for i in self.invariants if i.active]
        if not живые or not answer.strip():
            return {"ok": True, "violations": [], "checked": len(живые)}

        перечень = "\n".join(f"{i.id}. {i.text}" for i in живые)
        ответ = ask(
            f"Ограничения:\n{перечень}\n\nОтвет ассистента:\n{answer}\n\n"
            f"Есть ли нарушения? Верни JSON.",
            system=AUDITOR_ROLE, model=self.model, json_mode=True,
            temperature=0.0, max_tokens=FACTS_MAX_TOKENS, provider=self.provider)

        self.stats.extractions += 1
        self.stats.extraction_prompt_tokens += ответ.prompt_tokens
        self.stats.extraction_completion_tokens += ответ.completion_tokens

        сырой = (ответ.text or "").strip()
        итог = {"ok": True, "violations": [], "checked": len(живые)}
        if not сырой:
            return итог
        try:
            разобрано = _json.loads(сырой)
        except _json.JSONDecodeError:
            return итог
        нарушения = разобрано.get("violations") if isinstance(разобрано, dict) else None
        if isinstance(нарушения, list) and нарушения:
            по_номеру = {i.id: i for i in живые}
            собрано = []
            for n in нарушения:
                if not isinstance(n, dict):
                    continue
                номер = int(n.get("id", 0) or 0)
                собрано.append({
                    "id": номер,
                    "text": по_номеру[номер].text if номер in по_номеру else "",
                    "quote": str(n.get("quote", ""))[:200],
                    "why": str(n.get("why", ""))[:300],
                })
            итог = {"ok": not собрано, "violations": собрано, "checked": len(живые)}
        self.last_audit = итог
        return итог

    # ── состояние задачи, конечный автомат (день 13) ────────────────────
    def start_task(self, goal: str = "", *, step: str = "",
                   expecting: str = "") -> TaskState:
        """Завести задачу. Новая задача всегда начинается с планирования."""
        self.task = TaskState(stage=PLANNING, goal=goal.strip(), step=step.strip(),
                              expecting=expecting.strip(), updated=now_iso(),
                              log=[{"at": now_iso(), "to": PLANNING,
                                    "note": "задача заведена"}])
        self._save_task()
        return self.task

    def advance(self, to: str, note: str = "") -> tuple[bool, str]:
        """Перевести задачу на другой этап. Возвращает (получилось, объяснение).

        Здесь и живёт автомат. Переход разрешён, только если такое ребро
        есть в TRANSITIONS; иначе отказ с внятной причиной, а не молчаливое
        присваивание. Именно эта проверка отличает конечный автомат от поля
        «этап», в которое можно записать что угодно.
        """
        if to not in STAGES:
            return False, f"Нет такого этапа: {to!r}"
        if self.task is None:
            return False, "Задача ещё не заведена"
        if to == self.task.stage:
            return False, f"Задача уже на этапе «{self.task.label}»"
        if not self.task.can_go(to):
            куда = (", ".join(f"«{STAGE_LABELS[s]}»" for s in self.task.allowed)
                    or "никуда, задача завершена")
            return False, (f"Из «{self.task.label}» нельзя сразу в "
                           f"«{STAGE_LABELS[to]}». Доступно: {куда}")

        # День 15: ребро есть — но оно может быть заперто условием.
        if self.guards:
            заперто = self.task.blocked(to)
            if заперто:
                return False, (f"Переход «{self.task.label}» → "
                               f"«{STAGE_LABELS[to]}» требует, чтобы {заперто}. "
                               f"Отметка не поставлена.")

        откуда = self.task.stage
        self.task.stage = to
        self.task.updated = now_iso()

        # Возврат назад снимает отметку на том ребре, куда мы возвращаемся:
        # план будут переделывать, значит прежнее утверждение недействительно.
        # Без этого можно было бы утвердить план один раз и потом бесконечно
        # прыгать туда-сюда, обходя условие.
        снято = ""
        условие = GUARDS.get((to, откуда))
        if условие and self.task.approved.pop(условие[0], None):
            снято = f"; отметка «{условие[1]}» снята"

        self.task.log.append({"at": self.task.updated, "from": откуда, "to": to,
                              "note": (note.strip() + снято).strip("; ")})
        self._save_task()
        return True, f"{STAGE_LABELS[откуда]} → {STAGE_LABELS[to]}{снято}"

    def approve(self, key: str, *, by: str = "человек") -> tuple[bool, str]:
        """Поставить отметку-условие: «план утверждён», «проверка пройдена».

        Ставит именно человек. Если бы это мог сделать агент, условие ничего
        не гарантировало бы: он утвердил бы собственный план и пошёл дальше.
        """
        if self.task is None:
            return False, "Задача ещё не заведена"
        известные = {k for k, _ in GUARDS.values()}
        if key not in известные:
            return False, f"Нет такого условия: {key!r}"
        if self.task.is_met(key):
            return False, "Отметка уже стоит"
        self.task.approved[key] = now_iso()
        подпись = next(п for k, п in GUARDS.values() if k == key)
        self.task.log.append({"at": self.task.approved[key], "to": self.task.stage,
                              "note": f"отмечено: {подпись} ({by})"})
        self._save_task()
        return True, f"Отмечено: {подпись}"

    def revoke(self, key: str) -> bool:
        """Снять отметку — например, если план переделали."""
        if self.task is None or not self.task.approved.pop(key, None):
            return False
        self._save_task()
        return True

    def update_task(self, *, step: str | None = None, expecting: str | None = None,
                    goal: str | None = None) -> None:
        """Поправить шаг, ожидание или цель, не трогая этап."""
        if self.task is None:
            self.task = TaskState(updated=now_iso())
        if step is not None:
            self.task.step = step.strip()
        if expecting is not None:
            self.task.expecting = expecting.strip()
        if goal is not None:
            self.task.goal = goal.strip()
        self.task.updated = now_iso()
        self._save_task()

    def task_view(self) -> dict | None:
        """Снимок состояния для интерфейса."""
        if self.task is None:
            return None
        t = self.task
        return {
            "stage": t.stage, "label": t.label, "step": t.step,
            "expecting": t.expecting, "goal": t.goal, "updated": t.updated,
            # Без guards все рёбра открыты: день 13 не знает про условия
            # и не должен видеть замков.
            "allowed": (t.gates if self.guards else
                        [{"stage": s, "label": STAGE_LABELS[s], "guard": "",
                          "key": "", "met": True} for s in t.allowed]),
            "guards_on": self.guards,
            "approved": dict(t.approved),
            "rights": t.rights if self.guards else {},
            "guards": [{"from": a, "to": b, "key": k, "label": п}
                       for (a, b), (k, п) in GUARDS.items()],
            "stages": [{"stage": s, "label": STAGE_LABELS[s],
                        "passed": any(z.get("to") == s for z in t.log),
                        "current": s == t.stage} for s in STAGES],
            "log": t.log[-8:],
        }

    def _save_task(self) -> None:
        if self.store and self.task:
            self.store.save_task(self.session, self.task)

    def _track_task(self, question: str, answer: str) -> dict:
        """Спрашивает модель, где задача, и проверяет её предложение автоматом.

        Возвращает, что изменилось, — в том числе ОТКЛОНЁННЫЙ переход.
        Отклонения показываются в интерфейсе намеренно: это единственное
        наглядное доказательство, что автомат работает, а не просто
        записывает то, что сказала модель.
        """
        текущее = (self.task.as_prompt() if self.task
                   else "[Состояние задачи]\n- задача ещё не заведена")
        запрос = (f"{текущее}\n\nРеплика пользователя:\n{question}\n\n"
                  f"Ответ ассистента:\n{answer}\n\n"
                  f"Где сейчас задача? Верни JSON.")

        ответ = ask(запрос, system=TRACKER_ROLE, model=self.model, json_mode=True,
                    temperature=0.1, max_tokens=FACTS_MAX_TOKENS,
                    provider=self.provider)

        self.stats.extractions += 1
        self.stats.extraction_prompt_tokens += ответ.prompt_tokens
        self.stats.extraction_completion_tokens += ответ.completion_tokens

        сырой = (ответ.text or "").strip()
        итог: dict = {"moved": "", "rejected": "", "step": "", "expecting": ""}
        if not сырой:
            return итог
        try:
            разобрано = _json.loads(сырой)
        except _json.JSONDecodeError:
            return итог
        if not isinstance(разобрано, dict):
            return итог

        if self.task is None:
            self.start_task(str(разобрано.get("goal", "")).strip())

        шаг = str(разобрано.get("step", "")).strip()
        ждём = str(разобрано.get("expecting", "")).strip()
        цель = str(разобрано.get("goal", "")).strip()
        if шаг or ждём or (цель and not self.task.goal):
            self.update_task(step=шаг or None, expecting=ждём or None,
                             goal=цель if цель and not self.task.goal else None)
            итог["step"], итог["expecting"] = self.task.step, self.task.expecting

        предложен = str(разобрано.get("stage", "")).strip()
        if предложен and предложен != self.task.stage:
            получилось, почему = self.advance(предложен, "предложено по ходу разговора")
            итог["moved" if получилось else "rejected"] = почему
        return итог

    # ── персонализация (день 12) ────────────────────────────────────────
    def use_profile(self, name: str | None) -> bool:
        """Включить профиль по имени; None — снять персонализацию."""
        if name is None:
            self.profile = None
            return True
        if not self.store:
            return False
        найден = self.store.load_profile(name)
        if найден is None:
            return False
        self.profile = найден
        return True

    def save_profile(self, profile: Profile, *, activate: bool = True) -> None:
        """Создать или обновить профиль."""
        if self.store:
            self.store.save_profile(profile)
        if activate:
            self.profile = profile

    def profiles(self) -> list[Profile]:
        return self.store.profiles() if self.store else []

    def rename(self, new_name: str) -> bool:
        """Переименовать текущий диалог и остаться в нём."""
        new_name = (new_name or "").strip()
        if not self.store or not new_name or new_name == self.session:
            return False
        if not self.store.rename(self.session, new_name):
            return False
        self.session = new_name
        return True

    def drop(self, session: str) -> None:
        """Удалить диалог целиком. Если удаляем текущий — уходим в другой."""
        if not self.store:
            return
        self.store.clear(session)
        if session == self.session:
            остальные = [s.name for s in self.store.sessions()]
            self.switch(остальные[0] if остальные else DEFAULT_SESSION)

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
        self.facts = []
        self.task = None
        if not self.store:
            return

        # Пересказ и факты поднимаем первыми: без них агент после перезапуска
        # оказался бы с обрывком окна и без всего, что было свёрнуто.
        self.summary = self.store.load_summary(self.session)
        self.facts = self.store.load_facts(self.session)
        # Инварианты не привязаны к чату: они одни на проект.
        self.invariants = self.store.invariants()
        # Состояние задачи поднимается вместе с чатом — это и есть
        # «продолжение без повторных объяснений».
        self.task = self.store.load_task(self.session)
        # Долговременная память грузится независимо от сессии — в этом
        # и смысл слоя: она уже была здесь до этого диалога.
        self.memos = self.store.recall()

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

        # Карточка фактов обновляется после каждой реплики — как требует
        # задание. Сбой извлечения не должен ронять диалог: факты останутся
        # прежними, разговор продолжится.
        if self.tracking:
            # День 13: слежение за этапом. Сбой не должен ронять разговор —
            # состояние просто останется прежним.
            try:
                self._track_task(question, answer)
            except LLMError:
                pass

        if self.layered:
            # День 11: раскладка по слоям заменяет простое извлечение фактов —
            # она делает то же самое и вдобавок решает, что достойно
            # долговременной памяти.
            try:
                self._route(question, answer)
            except LLMError:
                pass
        elif self.strategy == "facts":
            try:
                self._extract_facts(question, answer)
            except LLMError:
                pass

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
