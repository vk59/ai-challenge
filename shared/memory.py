"""Хранилище диалога — то, что переживает выключение процесса.

День 6 держал историю в списке внутри объекта: закрыл программу — забыл всё.
Здесь появляется диск, и вместе с ним развилка, которой раньше не было:

    окно контекста  — что уезжает в модель, ограничено memory_turns
    архив           — что лежит на диске, не ограничено ничем

В дне 6 это было одно и то же, и `_trim()` уничтожал старые реплики навсегда.
Теперь подрезка касается только окна: на диске остаётся весь разговор.

Две реализации одного интерфейса — задание разрешает «JSON или SQLite»,
и разница между ними как раз в том, что происходит на каждой реплике:

    JsonStore    прочитать файл целиком → дописать → записать файл целиком
    SqliteStore  INSERT одной строки

Подробный разбор — в README дня 7. Обе используют только стандартную
библиотеку: внешних зависимостей в репозитории по-прежнему нет.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Каталог хранилища задаётся снаружи — тем же приёмом, что и .env в дне 6.
# Нативному приложению это обязательно: macOS не пускает .app в ~/Documents,
# поэтому build_app.sh уводит хранилище в ~/Library/Application Support.
ENV_DIR_VAR = "AI_ADVENT_MEMORY_DIR"

DEFAULT_SESSION = "default"
SQLITE_FILE = "agent.db"


class MemoryError_(Exception):
    """Понятная человеку ошибка работы с хранилищем."""


def now_iso() -> str:
    """Момент времени в UTC, в виде, пригодном и для JSON, и для сортировки."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_time(raw: str) -> str:
    """UTC с диска → местное время для показа человеку.

    Хранить в UTC, показывать в местном — обычное разделение, но забыть перевод
    в одном из интерфейсов легко: тогда список диалогов покажет 08:40, а сама
    переписка 11:40, и это будет одно и то же событие. Поэтому перевод один,
    и пользуются им все.
    """
    try:
        return datetime.fromisoformat(raw).astimezone().strftime("%d.%m %H:%M")
    except (ValueError, TypeError):
        return ""


def default_dir() -> Path:
    """Куда складывать историю, если каталог не указан явно."""
    raw = os.environ.get(ENV_DIR_VAR)
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[1] / "memory"


@dataclass
class Turn:
    """Одна реплика — то, что кладётся на диск.

    Сообщением для API является только пара role/content; остальное —
    наша бухгалтерия, в модель она не уезжает.
    """

    role: str                # user | assistant
    content: str
    at: str                  # когда произошло, ISO-8601 UTC
    tokens: int = 0          # у user — prompt_tokens, у assistant — completion_tokens
    seconds: float = 0.0     # сколько ждали ответ (заполнено у assistant)

    @property
    def message(self) -> dict:
        """Ровно то, что понимает API: лишние поля туда отправлять нельзя."""
        return {"role": self.role, "content": self.content}

    @property
    def when(self) -> str:
        """Время в местной зоне, коротко — для интерфейсов."""
        return local_time(self.at)

    def as_dict(self) -> dict:
        return {"role": self.role, "content": self.content, "at": self.at,
                "tokens": self.tokens, "seconds": self.seconds}

    @classmethod
    def from_dict(cls, raw: dict) -> "Turn":
        return cls(
            role=str(raw.get("role", "user")),
            content=str(raw.get("content", "")),
            at=str(raw.get("at", "")),
            tokens=int(raw.get("tokens", 0) or 0),
            seconds=float(raw.get("seconds", 0.0) or 0.0),
        )


@dataclass
class SessionInfo:
    """Сводка по одному сохранённому диалогу — для списка сессий."""

    name: str
    turns: int               # реплик всего (и вопросы, и ответы)
    tokens: int
    started: str
    updated: str
    # Первая реплика пользователя — из неё делается заголовок в списке чатов.
    # «диалог-2» ни о чём не говорит, а «Собираем ТЗ на складской учёт» —
    # говорит, и искать глазами по такому списку можно.
    first: str = ""

    @property
    def title(self) -> str:
        """Человеческий заголовок чата: по первой реплике, иначе по имени."""
        текст = " ".join((self.first or "").split())
        if not текст:
            return self.name
        return текст[:38] + "…" if len(текст) > 39 else текст

    @property
    def pairs(self) -> int:
        """Пар «вопрос-ответ» — в них удобнее считать глубину диалога."""
        return self.turns // 2

    @property
    def when(self) -> str:
        """Когда в диалог писали последний раз, в местной зоне."""
        return local_time(self.updated)


@dataclass
class Totals:
    """Всё, что накопилось в сессии за всё время, включая прошлые запуски."""

    turns: int = 0           # реплик пользователя, то есть пар
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0


@dataclass
class Summary:
    """Сжатый пересказ вытесненной части диалога (день 9).

    Лежит отдельно от реплик и подставляется в запрос ВМЕСТО них. Хранить
    его обязательно на диске: иначе перезапуск вернёт агента к обрывку
    окна, а всё, что было свёрнуто, пропадёт — то есть сжатие окажется
    способом терять память, а не экономить токены.
    """

    content: str
    at: str = ""
    covered: int = 0         # сколько пар реплик свёрнуто в этот текст
    tokens: int = 0          # во сколько токенов обошлось само сжатие
    rounds: int = 0          # сколько раз пересобирался (сжатие накопительное)

    def as_dict(self) -> dict:
        return {"content": self.content, "at": self.at, "covered": self.covered,
                "tokens": self.tokens, "rounds": self.rounds}

    @classmethod
    def from_dict(cls, raw: dict) -> "Summary":
        return cls(
            content=str(raw.get("content", "")),
            at=str(raw.get("at", "")),
            covered=int(raw.get("covered", 0) or 0),
            tokens=int(raw.get("tokens", 0) or 0),
            rounds=int(raw.get("rounds", 0) or 0),
        )


# Слои памяти (день 11). Отличаются не форматом, а ОБЛАСТЬЮ ЖИЗНИ:
#
#   SHORT   текущий диалог — реплики, живут в сессии, вытесняются окном
#   WORKING данные текущей задачи — факты, живут в сессии, переживают окно
#   LONG    профиль, решения, знания — живут ВНЕ сессий, общие для всех
#
# Граница между WORKING и LONG проходит именно по области: рабочая память
# умирает вместе с задачей, долговременная переезжает в следующий диалог.
# Если бы обе лежали в сессии, разделение было бы косметическим.
SHORT, WORKING, LONG = "short", "working", "long"
LAYERS = (SHORT, WORKING, LONG)

# Подтипы долговременной памяти — три, как просит задание.
PROFILE, DECISION, KNOWLEDGE = "profile", "decision", "knowledge"
LONG_KINDS = (PROFILE, DECISION, KNOWLEDGE)


@dataclass
class Memo:
    """Запись долговременной памяти — живёт вне всякой сессии (день 11)."""

    key: str
    value: str
    kind: str = PROFILE      # profile | decision | knowledge
    at: str = ""
    source: str = ""         # из какого диалога приехало, для проверяемости

    def as_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "kind": self.kind,
                "at": self.at, "source": self.source}

    @classmethod
    def from_dict(cls, raw: dict) -> "Memo":
        return cls(key=str(raw.get("key", "")), value=str(raw.get("value", "")),
                   kind=str(raw.get("kind", PROFILE)), at=str(raw.get("at", "")),
                   source=str(raw.get("source", "")))


# Области инвариантов (день 14) — для группировки в интерфейсе.
ARCH, STACK, RULE, DECISION_KIND = "arch", "stack", "rule", "decision"
INVARIANT_SCOPES = (ARCH, STACK, RULE, DECISION_KIND)
SCOPE_LABELS = {ARCH: "архитектура", STACK: "стек", RULE: "бизнес-правило",
                DECISION_KIND: "принятое решение"}


@dataclass
class Invariant:
    """Ограничение, которое ассистент не имеет права нарушать (день 14).

    Отличие от долговременной памяти принципиальное. Память — это то, что
    агент ЗНАЕТ («база PostgreSQL 16»). Инвариант — то, чего ему НЕЛЬЗЯ
    («предлагать замену СУБД запрещено»). Первое он может учесть или забыть,
    второе обязан соблюдать и обязан отказать, если его просят нарушить.

    Поэтому у инварианта есть rationale: отказ без причины выглядит
    самодурством, а с причиной — это разговор по существу.
    """

    id: int = 0
    text: str = ""           # само ограничение, повелительно
    rationale: str = ""      # почему так решили
    scope: str = STACK
    active: bool = True
    at: str = ""

    @property
    def scope_label(self) -> str:
        return SCOPE_LABELS.get(self.scope, self.scope)

    def as_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "rationale": self.rationale,
                "scope": self.scope, "active": self.active, "at": self.at,
                "scope_label": self.scope_label}

    @classmethod
    def from_dict(cls, raw: dict) -> "Invariant":
        return cls(id=int(raw.get("id", 0) or 0), text=str(raw.get("text", "")),
                   rationale=str(raw.get("rationale", "")),
                   scope=str(raw.get("scope", STACK)),
                   active=bool(raw.get("active", True)),
                   at=str(raw.get("at", "")))


# Состояние задачи как конечный автомат (день 13).
#
# Этапы и — что важнее — РЁБРА между ними. Без рёбер это был бы просто
# ярлык «этап», который можно поставить любой. Автомат начинается там,
# где переход planning → done становится невозможным.
PLANNING, EXECUTION, VALIDATION, DONE = (
    "planning", "execution", "validation", "done")
STAGES = (PLANNING, EXECUTION, VALIDATION, DONE)

TRANSITIONS: dict[str, tuple[str, ...]] = {
    PLANNING: (EXECUTION,),
    # Из работы можно вернуться к планированию: выяснилось, что план не годится.
    EXECUTION: (VALIDATION, PLANNING),
    # Проверка не прошла — обратно в работу. Это главное ребро назад,
    # без него автомат описывал бы только счастливый путь.
    VALIDATION: (DONE, EXECUTION),
    DONE: (),
}

STAGE_LABELS = {PLANNING: "планирование", EXECUTION: "выполнение",
                VALIDATION: "проверка", DONE: "готово"}

# Условия на переходах (день 15). Ребро в TRANSITIONS есть — а пройти по нему
# нельзя, пока не выполнено требование. Это и есть «нельзя делать реализацию
# до утверждённого плана»: ребро planning → execution существует, но заперто.
#
# Ключ отметки ставит ЧЕЛОВЕК, а не модель. Если бы галочку «план утверждён»
# проставляла сама модель, условие не гарантировало бы ничего: она бы
# утвердила собственный план и пошла дальше.
GUARDS: dict[tuple[str, str], tuple[str, str]] = {
    (PLANNING, EXECUTION): ("plan_approved", "план утверждён"),
    (VALIDATION, DONE): ("validation_passed", "проверка пройдена"),
}

# Что агенту можно и чего нельзя на каждом этапе (день 15).
#
# В дне 13 автомат только описывал, где задача. Здесь он управляет поведением:
# на планировании агент не пишет реализацию, даже если прямо просят, и
# объясняет, чего не хватает. Без этого «контролируемый жизненный цикл»
# оставался бы ярлыком в интерфейсе.
STAGE_RIGHTS: dict[str, dict[str, str]] = {
    PLANNING: {
        "можно": "уточнять требования, предлагать варианты, составлять "
                 "и править план, оценивать трудоёмкость",
        "нельзя": "писать реализацию, выдавать готовый код функциональности, "
                  "миграции и конфигурации — пока план не утверждён человеком",
    },
    EXECUTION: {
        "можно": "реализовывать утверждённый план, писать код, разбирать "
                 "возникающие по ходу проблемы",
        "нельзя": "объявлять задачу готовой и подводить итоги в обход проверки",
    },
    VALIDATION: {
        "можно": "проверять сделанное, искать дефекты, предлагать правки "
                 "по найденному",
        "нельзя": "добавлять новую функциональность сверх плана и объявлять "
                  "задачу завершённой, пока проверка не отмечена пройденной",
    },
    DONE: {
        "можно": "отвечать на вопросы по уже сделанному",
        "нельзя": "продолжать доработки — задача закрыта, для нового объёма "
                  "нужна новая задача",
    },
}


@dataclass
class TaskState:
    """Где сейчас задача: этап, шаг и чьего действия ждём.

    Лежит отдельно от диалога и переживает перезапуск — в этом весь смысл.
    Пауза на любом этапе и продолжение без повторных объяснений работают
    не потому, что агент «помнит разговор», а потому, что состояние задачи
    записано явно и не зависит от того, влезла история в окно контекста
    или нет.
    """

    stage: str = PLANNING
    step: str = ""           # текущий шаг внутри этапа
    expecting: str = ""      # чего ждём и от кого
    goal: str = ""           # что вообще делаем
    updated: str = ""
    # История переходов — короткий список, запросов по нему не делаем,
    # поэтому хранится одним JSON-полем, а не отдельной таблицей.
    log: list = field(default_factory=list)
    # Отметки-условия (день 15): {"plan_approved": "2026-09-22T10:00:00+00:00"}.
    # Ставит человек, снимаются автоматически при возврате назад.
    approved: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return STAGE_LABELS.get(self.stage, self.stage)

    @property
    def allowed(self) -> tuple[str, ...]:
        """Куда отсюда ведут рёбра. Пройти по ребру можно не всегда — см. guard_for."""
        return TRANSITIONS.get(self.stage, ())

    def can_go(self, to: str) -> bool:
        return to in self.allowed

    # ── условия переходов (день 15) ─────────────────────────────────────
    def guard_for(self, to: str) -> tuple[str, str] | None:
        """Требование на ребре отсюда в to, если оно есть."""
        return GUARDS.get((self.stage, to))

    def is_met(self, key: str) -> bool:
        return bool(self.approved.get(key))

    def blocked(self, to: str) -> str:
        """Чем заперт переход. Пустая строка — не заперт."""
        условие = self.guard_for(to)
        if условие is None:
            return ""
        ключ, подпись = условие
        return "" if self.is_met(ключ) else подпись

    @property
    def gates(self) -> list[dict]:
        """Все условия на исходящих рёбрах — для интерфейса."""
        собрано = []
        for цель in self.allowed:
            условие = self.guard_for(цель)
            собрано.append({
                "stage": цель, "label": STAGE_LABELS[цель],
                "guard": условие[1] if условие else "",
                "key": условие[0] if условие else "",
                "met": self.is_met(условие[0]) if условие else True,
            })
        return собрано

    @property
    def rights(self) -> dict[str, str]:
        return STAGE_RIGHTS.get(self.stage, {})

    def as_prompt(self, *, with_rights: bool = False) -> str:
        """Состояние в виде куска системного промпта.

        with_rights — день 15: добавляет блок прав этапа и отметки о том,
        какие переходы заперты. По умолчанию выключено, чтобы день 13
        с тем же автоматом остался ровно таким, каким его записали.
        """
        строки = [f"- этап: {self.label}"]
        if self.goal:
            строки.insert(0, f"- задача: {self.goal}")
        if self.step:
            строки.append(f"- текущий шаг: {self.step}")
        if self.expecting:
            строки.append(f"- ждём: {self.expecting}")
        if self.allowed:
            if with_rights:
                куски = []
                for g in self.gates:
                    если = "" if g["met"] else f" (заперто: нужно чтобы {g['guard']})"
                    куски.append(f"{g['label']}{если}")
                строки.append("- дальше возможно: " + ", ".join(куски))
            else:
                строки.append("- дальше возможно: "
                              + ", ".join(STAGE_LABELS[s] for s in self.allowed))
        else:
            строки.append("- задача завершена")
        пройдено = [z for z in self.log if z.get("to")]
        if пройдено:
            путь = " → ".join(STAGE_LABELS.get(z["to"], z["to"]) for z in пройдено[-4:])
            строки.append(f"- уже пройдено: {путь}")

        # Права этапа — жёстким блоком, отдельно от справки о состоянии.
        # Просто сказать «ты на этапе планирования» мало: модель воспримет
        # это как контекст и всё равно выдаст код, если попросить.
        права = self.rights if with_rights else {}
        хвост = ""
        if права:
            хвост = ("\n\n[ЧТО РАЗРЕШЕНО НА ЭТОМ ЭТАПЕ]\n"
                     f"Можно: {права.get('можно', '')}\n"
                     f"НЕЛЬЗЯ: {права.get('нельзя', '')}\n"
                     "Если просьба требует того, что на этом этапе нельзя — "
                     "откажись, назови этап, объясни, какого условия не хватает, "
                     "и предложи то, что на этом этапе уместно.")
        return "[Состояние задачи]\n" + "\n".join(строки) + хвост

    def as_dict(self) -> dict:
        return {"stage": self.stage, "step": self.step, "expecting": self.expecting,
                "goal": self.goal, "updated": self.updated, "log": self.log,
                "approved": self.approved}

    @classmethod
    def from_dict(cls, raw: dict) -> "TaskState":
        return cls(stage=str(raw.get("stage", PLANNING)),
                   step=str(raw.get("step", "")),
                   expecting=str(raw.get("expecting", "")),
                   goal=str(raw.get("goal", "")),
                   updated=str(raw.get("updated", "")),
                   log=list(raw.get("log") or []),
                   approved=dict(raw.get("approved") or {}))


@dataclass
class Profile:
    """Профиль пользователя — предпочтения, а не факты (день 12).

    Отличие от PROFILE-записей долговременной памяти принципиальное:
    там факты О человеке («имя: Иван»), здесь указания КАК с ним говорить
    («отвечай кратко, без кода, по-русски»). Первое агент узнаёт сам из
    диалога, второе задаётся сознательно и меняется одним переключением.

    Профилей может быть несколько — иначе нельзя сравнить, как один
    и тот же вопрос звучит для новичка и для эксперта.
    """

    name: str
    tone: str = ""           # как разговаривать: кратко, подробно, по-дружески
    format: str = ""         # чем отвечать: код, списки, проза, таблицы
    level: str = ""          # уровень собеседника: новичок, senior
    constraints: str = ""    # чего НЕ делать
    language: str = ""       # язык ответа
    extra: str = ""          # всё, что не легло в поля выше

    ПОЛЯ = (("tone", "тон"), ("format", "формат"), ("level", "уровень"),
            ("language", "язык"), ("constraints", "ограничения"),
            ("extra", "дополнительно"))

    def as_dict(self) -> dict:
        return {"name": self.name, "tone": self.tone, "format": self.format,
                "level": self.level, "constraints": self.constraints,
                "language": self.language, "extra": self.extra}

    @classmethod
    def from_dict(cls, raw: dict) -> "Profile":
        return cls(name=str(raw.get("name", "")), tone=str(raw.get("tone", "")),
                   format=str(raw.get("format", "")), level=str(raw.get("level", "")),
                   constraints=str(raw.get("constraints", "")),
                   language=str(raw.get("language", "")),
                   extra=str(raw.get("extra", "")))

    @property
    def filled(self) -> list[tuple[str, str]]:
        """Только заполненные предпочтения, с русскими подписями."""
        return [(подпись, getattr(self, поле))
                for поле, подпись in self.ПОЛЯ if getattr(self, поле).strip()]

    def as_prompt(self) -> str:
        """Профиль в виде куска системного промпта."""
        строки = self.filled
        if not строки:
            return ""
        тело = "\n".join(f"- {подпись}: {значение}" for подпись, значение in строки)
        return f"[Как отвечать этому собеседнику]\n{тело}"


@dataclass
class Fact:
    """Один факт из диалога — ключ и значение (день 10).

    Отдельная сущность, а не строка в пересказе: факты перезаписываются
    по ключу. Сказали «бюджет 800 тысяч», потом «бюджет подняли до миллиона» —
    в памяти должно остаться второе, а не оба подряд. Пересказ дня 9 так
    не умеет: он только прирастает.
    """

    key: str
    value: str
    at: str = ""

    def as_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "at": self.at}

    @classmethod
    def from_dict(cls, raw: dict) -> "Fact":
        return cls(key=str(raw.get("key", "")), value=str(raw.get("value", "")),
                   at=str(raw.get("at", "")))


class Store:
    """Общий интерфейс хранилища. Агент знает только его, не реализацию."""

    kind = "?"
    label = "?"

    def append(self, session: str, turn: Turn) -> None:
        """Дописать одну реплику. Вызывается сразу, а не при выходе."""
        raise NotImplementedError

    def load(self, session: str, limit: int | None = None) -> list[Turn]:
        """Достать реплики; limit — сколько последних (None — все)."""
        raise NotImplementedError

    def totals(self, session: str) -> Totals:
        """Суммарный расход по сессии за всю её историю."""
        raise NotImplementedError

    def sessions(self) -> list[SessionInfo]:
        """Все сохранённые диалоги, свежие сверху."""
        raise NotImplementedError

    def clear(self, session: str) -> None:
        """Стереть сессию. Забыть — значит забыть и на диске тоже."""
        raise NotImplementedError

    def rename(self, session: str, new_name: str) -> bool:
        """Переименовать диалог. False — если имя занято или переименовывать нечего.

        Переносить надо ВСЕ следы сессии разом: реплики, факты и пересказ.
        Забыть про один из них — значит оставить осиротевшие данные под
        старым именем, которые потом всплывут в чужом диалоге.
        """
        raise NotImplementedError

    # ── сжатая история (день 9) ─────────────────────────────────────────
    def save_summary(self, session: str, summary: Summary) -> None:
        """Сохранить пересказ. Один на сессию: он накопительный, не список."""
        raise NotImplementedError

    def load_summary(self, session: str) -> Summary | None:
        """Достать пересказ; None — если сессию ещё ни разу не сжимали."""
        raise NotImplementedError

    # ── факты и ветки (день 10) ─────────────────────────────────────────
    def save_facts(self, session: str, facts: list[Fact]) -> None:
        """Переписать набор фактов целиком: они живут как единая карточка."""
        raise NotImplementedError

    def load_facts(self, session: str) -> list[Fact]:
        """Факты сессии; пустой список — если их ещё не извлекали."""
        raise NotImplementedError

    # ── долговременная память, вне сессий (день 11) ─────────────────────
    def remember(self, memo: Memo) -> None:
        """Записать в долговременную память. Ключ уникален глобально."""
        raise NotImplementedError

    def recall(self, kind: str | None = None) -> list[Memo]:
        """Достать долговременную память; kind — отфильтровать по подтипу."""
        raise NotImplementedError

    def forget(self, key: str) -> bool:
        """Забыть одну запись навсегда. True — если было что забывать."""
        raise NotImplementedError

    # ── инварианты (день 14) ────────────────────────────────────────────
    def save_invariant(self, inv: Invariant) -> Invariant:
        """Создать или обновить ограничение. Вернуть с проставленным id."""
        raise NotImplementedError

    def invariants(self, *, only_active: bool = False) -> list[Invariant]:
        raise NotImplementedError

    def delete_invariant(self, inv_id: int) -> bool:
        raise NotImplementedError

    # ── состояние задачи (день 13) ──────────────────────────────────────
    def save_task(self, session: str, task: TaskState) -> None:
        """Состояние задачи привязано к чату: чат и есть задача."""
        raise NotImplementedError

    def load_task(self, session: str) -> TaskState | None:
        raise NotImplementedError

    # ── профили пользователя (день 12) ──────────────────────────────────
    def save_profile(self, profile: Profile) -> None:
        """Сохранить профиль. Как и долговременная память — вне сессий."""
        raise NotImplementedError

    def load_profile(self, name: str) -> Profile | None:
        raise NotImplementedError

    def profiles(self) -> list[Profile]:
        raise NotImplementedError

    def delete_profile(self, name: str) -> bool:
        raise NotImplementedError

    def fork(self, session: str, branch: str, upto: int) -> int:
        """Ответвить новый диалог от первых upto реплик исходного.

        Реплики копируются, а не связываются ссылкой на родителя. Ссылка
        экономнее, но тогда каждая загрузка ветки означала бы рекурсивный
        обход предков, а удаление родителя ломало бы потомков. Копия —
        это чуть больше байт на диске и полная независимость веток,
        что здесь и требуется: ветки должны расходиться и жить сами.

        Возвращает, сколько реплик скопировано.
        """
        источник = self.load(session)[:upto]
        for turn in источник:
            self.append(branch, turn)
        # Факты и пересказ наследуются: ветка начинается с того же состояния
        # памяти, иначе развилка была бы нечестной.
        факты = self.load_facts(session)
        if факты:
            self.save_facts(branch, факты)
        задача = self.load_task(session)
        if задача:
            self.save_task(branch, задача)
        пересказ = self.load_summary(session)
        if пересказ:
            self.save_summary(branch, пересказ)
        return len(источник)

    # ── общее для реализаций ────────────────────────────────────────────
    @staticmethod
    def _aligned(turns: list[Turn]) -> list[Turn]:
        """Окно не должно начинаться с ответа модели.

        Срез последних N реплик может разрезать пару пополам, и тогда диалог
        для API начинается с assistant — формально допустимо, но выглядит так,
        будто модель заговорила первой. Отрезаем висящий хвост.
        """
        if turns and turns[0].role == "assistant":
            return turns[1:]
        return turns

    @staticmethod
    def _totals_of(turns: list[Turn]) -> Totals:
        totals = Totals()
        for turn in turns:
            if turn.role == "user":
                totals.turns += 1
                totals.prompt_tokens += turn.tokens
            else:
                totals.completion_tokens += turn.tokens
                totals.seconds += turn.seconds
        totals.seconds = round(totals.seconds, 1)
        return totals


class JsonStore(Store):
    """Диалог в JSON-файле: по файлу на сессию, целиком читаемый глазами.

    Главный плюс — прозрачность: `cat memory/default.json` показывает всё,
    что помнит агент. Главный минус — цена дозаписи: JSON не умеет «дописать
    в конец», поэтому каждая реплика переписывает файл целиком. На сотне
    реплик незаметно, на десятках тысяч — уже нет.

    Запись атомарная: сначала во временный файл рядом, потом os.replace.
    Без этого обрыв на середине записи (Ctrl+C, разряженный ноутбук) оставил
    бы обрезанный JSON, то есть потерю всего диалога, а не последней реплики.
    """

    kind = "json"
    label = "JSON-файл"

    def __init__(self, directory: Path | str | None = None) -> None:
        self.dir = Path(directory) if directory else default_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        # Читаем-меняем-пишем неатомарно по своей природе, а веб-сервер
        # многопоточный: без замка две одновременные реплики затрут друг друга.
        self._lock = threading.Lock()

    def __str__(self) -> str:
        return f"{self.label} · {self.dir}"

    # ── интерфейс ───────────────────────────────────────────────────────
    def append(self, session: str, turn: Turn) -> None:
        with self._lock:
            data = self._read(session)
            data["turns"].append(turn.as_dict())
            data["updated"] = turn.at
            data.setdefault("started", turn.at)
            self._write(session, data)

    def load(self, session: str, limit: int | None = None) -> list[Turn]:
        turns = [Turn.from_dict(raw) for raw in self._read(session)["turns"]]
        if limit is not None and len(turns) > limit:
            turns = self._aligned(turns[-limit:])
        return turns

    def totals(self, session: str) -> Totals:
        return self._totals_of(self.load(session))

    def sessions(self) -> list[SessionInfo]:
        found = []
        for path in self.dir.glob("*.json"):
            # Служебные файлы — не диалоги. Долговременная память и профили
            # лежат в том же каталоге (_longterm.json, _profiles.json), и без
            # этой проверки они показывались бы в списке чатов как «_profiles».
            if path.name.startswith("_"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            turns = data.get("turns") or []
            первая = next((str(t.get("content", "")) for t in turns
                           if t.get("role") == "user"), "")
            found.append(SessionInfo(
                name=str(data.get("session") or path.stem),
                turns=len(turns),
                tokens=sum(int(t.get("tokens", 0) or 0) for t in turns),
                started=str(data.get("started") or ""),
                updated=str(data.get("updated") or ""),
                first=первая,
            ))
        return sorted(found, key=lambda s: s.updated, reverse=True)

    def rename(self, session: str, new_name: str) -> bool:
        with self._lock:
            источник = self._path(session)
            цель = self._path(new_name)
            if not источник.exists() or цель.exists() or источник == цель:
                return False
            data = self._read(session)
            data["session"] = new_name
            # Пишем под новым именем и только потом сносим старый файл:
            # если что-то упадёт посередине, диалог останется хотя бы в одном
            # из двух мест, а не исчезнет.
            self._write(new_name, data)
            источник.unlink(missing_ok=True)
            return True

    def clear(self, session: str) -> None:
        with self._lock:
            self._path(session).unlink(missing_ok=True)

    def save_summary(self, session: str, summary: Summary) -> None:
        with self._lock:
            data = self._read(session)
            data["summary"] = summary.as_dict()
            self._write(session, data)

    def load_summary(self, session: str) -> Summary | None:
        raw = self._read(session).get("summary")
        return Summary.from_dict(raw) if raw else None

    def save_facts(self, session: str, facts: list[Fact]) -> None:
        with self._lock:
            data = self._read(session)
            data["facts"] = [f.as_dict() for f in facts]
            self._write(session, data)

    def save_task(self, session: str, task: TaskState) -> None:
        with self._lock:
            data = self._read(session)
            data["task"] = task.as_dict()
            self._write(session, data)

    def load_task(self, session: str) -> TaskState | None:
        raw = self._read(session).get("task")
        return TaskState.from_dict(raw) if raw else None

    def load_facts(self, session: str) -> list[Fact]:
        return [Fact.from_dict(r) for r in self._read(session).get("facts") or []]

    # Долговременная память в отдельном файле, а не в файле сессии — она
    # и по смыслу вне сессий, и физически должна лежать отдельно, иначе
    # удаление диалога унесло бы с собой профиль пользователя.
    def _long_path(self) -> Path:
        return self.dir / "_longterm.json"

    def remember(self, memo: Memo) -> None:
        with self._lock:
            записи = {m.key: m for m in self._read_long()}
            записи[memo.key] = memo
            self._write_long(list(записи.values()))

    def recall(self, kind: str | None = None) -> list[Memo]:
        записи = self._read_long()
        if kind:
            записи = [m for m in записи if m.kind == kind]
        return sorted(записи, key=lambda m: (m.kind, m.key))

    def forget(self, key: str) -> bool:
        with self._lock:
            записи = self._read_long()
            осталось = [m for m in записи if m.key != key]
            if len(осталось) == len(записи):
                return False
            self._write_long(осталось)
            return True

    # Инварианты — вне сессий, как и долговременная память: архитектура
    # и стек одни на весь проект, а не на отдельный чат.
    def _inv_path(self) -> Path:
        return self.dir / "_invariants.json"

    def save_invariant(self, inv: Invariant) -> Invariant:
        with self._lock:
            все = self._read_invariants()
            if inv.id:
                все = [inv if x.id == inv.id else x for x in все]
            else:
                inv.id = max((x.id for x in все), default=0) + 1
                все.append(inv)
            self._write_invariants(все)
            return inv

    def invariants(self, *, only_active: bool = False) -> list[Invariant]:
        найдено = self._read_invariants()
        if only_active:
            найдено = [x for x in найдено if x.active]
        return sorted(найдено, key=lambda x: x.id)

    def delete_invariant(self, inv_id: int) -> bool:
        with self._lock:
            все = self._read_invariants()
            осталось = [x for x in все if x.id != inv_id]
            if len(осталось) == len(все):
                return False
            self._write_invariants(осталось)
            return True

    def _read_invariants(self) -> list[Invariant]:
        try:
            raw = json.loads(self._inv_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as exc:
            raise MemoryError_(f"Инварианты повреждены: {exc}") from exc
        return [Invariant.from_dict(r) for r in raw.get("invariants") or []]

    def _write_invariants(self, список: list[Invariant]) -> None:
        путь = self._inv_path()
        tmp = путь.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(
                {"invariants": [x.as_dict() for x in список]},
                ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, путь)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise MemoryError_(f"Не пишутся инварианты: {exc}") from exc

    def _profiles_path(self) -> Path:
        return self.dir / "_profiles.json"

    def save_profile(self, profile: Profile) -> None:
        with self._lock:
            все = {p.name: p for p in self._read_profiles()}
            все[profile.name] = profile
            self._write_profiles(list(все.values()))

    def load_profile(self, name: str) -> Profile | None:
        return next((p for p in self._read_profiles() if p.name == name), None)

    def profiles(self) -> list[Profile]:
        return sorted(self._read_profiles(), key=lambda p: p.name)

    def delete_profile(self, name: str) -> bool:
        with self._lock:
            все = self._read_profiles()
            осталось = [p for p in все if p.name != name]
            if len(осталось) == len(все):
                return False
            self._write_profiles(осталось)
            return True

    def _read_profiles(self) -> list[Profile]:
        try:
            raw = json.loads(self._profiles_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as exc:
            raise MemoryError_(f"Профили повреждены: {exc}") from exc
        return [Profile.from_dict(r) for r in raw.get("profiles") or []]

    def _write_profiles(self, профили: list[Profile]) -> None:
        путь = self._profiles_path()
        tmp = путь.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps({"profiles": [p.as_dict() for p in профили]},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, путь)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise MemoryError_(f"Не пишутся профили: {exc}") from exc

    def _read_long(self) -> list[Memo]:
        try:
            raw = json.loads(self._long_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as exc:
            raise MemoryError_(f"Долговременная память повреждена: {exc}") from exc
        return [Memo.from_dict(r) for r in raw.get("memos") or []]

    def _write_long(self, записи: list[Memo]) -> None:
        путь = self._long_path()
        tmp = путь.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps({"memos": [m.as_dict() for m in записи]},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, путь)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise MemoryError_(f"Не пишется долговременная память: {exc}") from exc

    # ── внутреннее ──────────────────────────────────────────────────────
    def _path(self, session: str) -> Path:
        return self.dir / f"{_safe_name(session)}.json"

    def _read(self, session: str) -> dict:
        path = self._path(session)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"session": session, "started": "", "updated": "", "turns": []}
        except json.JSONDecodeError as exc:
            raise MemoryError_(f"Файл истории повреждён: {path} ({exc})") from exc
        except OSError as exc:
            raise MemoryError_(f"Не читается файл истории {path}: {exc}") from exc

        data.setdefault("session", session)
        data.setdefault("turns", [])
        return data

    def _write(self, session: str, data: dict) -> None:
        path = self._path(session)
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, path)          # атомарная подмена в пределах ФС
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise MemoryError_(f"Не пишется файл истории {path}: {exc}") from exc


class SqliteStore(Store):
    """Диалог в SQLite: все сессии в одной таблице, дозапись — один INSERT.

    Файл перестаёт читаться глазами, зато исчезают ровно те проблемы, которые
    у JSON приходится решать руками: запись одной реплики не трогает остальные,
    блокировки и атомарность транзакции — забота движка, а выборка последних N
    реплик делается запросом, а не чтением всего диалога в память.

    sqlite3 — часть стандартной библиотеки, ставить ничего не нужно.
    """

    kind = "sqlite"
    label = "SQLite"

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS turns (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        session  TEXT    NOT NULL,
        role     TEXT    NOT NULL,
        content  TEXT    NOT NULL,
        at       TEXT    NOT NULL,
        tokens   INTEGER NOT NULL DEFAULT 0,
        seconds  REAL    NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS turns_by_session ON turns (session, id);

    -- Сжатая история (день 9). Отдельная таблица, а не строка в turns:
    -- пересказ не является репликой диалога и в модель уезжает иначе —
    -- рядом с ролью, а не в списке сообщений.
    -- IF NOT EXISTS делает миграцию баз дней 7-8 бесплатной.
    CREATE TABLE IF NOT EXISTS summaries (
        session  TEXT    PRIMARY KEY,
        content  TEXT    NOT NULL,
        at       TEXT    NOT NULL DEFAULT '',
        covered  INTEGER NOT NULL DEFAULT 0,
        tokens   INTEGER NOT NULL DEFAULT 0,
        rounds   INTEGER NOT NULL DEFAULT 0
    );

    -- Факты «ключ-значение» (день 10). Ключ уникален в пределах сессии:
    -- новое значение вытесняет старое, в этом весь смысл — память должна
    -- обновляться, а не прирастать.
    CREATE TABLE IF NOT EXISTS facts (
        session  TEXT    NOT NULL,
        key      TEXT    NOT NULL,
        value    TEXT    NOT NULL,
        at       TEXT    NOT NULL DEFAULT '',
        PRIMARY KEY (session, key)
    );

    -- Долговременная память (день 11). Обратите внимание: колонки session
    -- здесь НЕТ, и это главное отличие слоя. Профиль, решения и знания
    -- общие для всех диалогов и переезжают из одного в другой.
    CREATE TABLE IF NOT EXISTS longterm (
        key      TEXT    PRIMARY KEY,
        value    TEXT    NOT NULL,
        kind     TEXT    NOT NULL DEFAULT 'profile',
        at       TEXT    NOT NULL DEFAULT '',
        source   TEXT    NOT NULL DEFAULT ''
    );

    -- Профили пользователя (день 12): не факты О человеке, а указания,
    -- КАК с ним говорить. Тоже вне сессий, и их может быть несколько —
    -- иначе не сравнить, как один вопрос звучит для новичка и для senior.
    -- Инварианты (день 14). Колонки session тут НЕТ: архитектура, стек
    -- и бизнес-правила одни на весь проект, а не на отдельный чат.
    CREATE TABLE IF NOT EXISTS invariants (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        text      TEXT    NOT NULL,
        rationale TEXT    NOT NULL DEFAULT '',
        scope     TEXT    NOT NULL DEFAULT 'stack',
        active    INTEGER NOT NULL DEFAULT 1,
        at        TEXT    NOT NULL DEFAULT ''
    );

    -- Состояние задачи (день 13). По одному на чат: чат и есть задача.
    -- Лежит отдельно от реплик, поэтому переживает и вытеснение окна,
    -- и перезапуск — на этом держится «пауза и продолжение».
    CREATE TABLE IF NOT EXISTS task_state (
        session   TEXT PRIMARY KEY,
        stage     TEXT NOT NULL DEFAULT 'planning',
        step      TEXT NOT NULL DEFAULT '',
        expecting TEXT NOT NULL DEFAULT '',
        goal      TEXT NOT NULL DEFAULT '',
        updated   TEXT NOT NULL DEFAULT '',
        log       TEXT NOT NULL DEFAULT '[]'
    );
    -- Отметки-условия (день 15) добавляются отдельно: базы дней 13-14 уже
    -- созданы без этой колонки, а ALTER TABLE в executescript упал бы
    -- на второй раз. Поэтому добавление вынесено в _migrate().

    CREATE TABLE IF NOT EXISTS profiles (
        name        TEXT PRIMARY KEY,
        tone        TEXT NOT NULL DEFAULT '',
        format      TEXT NOT NULL DEFAULT '',
        level       TEXT NOT NULL DEFAULT '',
        constraints TEXT NOT NULL DEFAULT '',
        language    TEXT NOT NULL DEFAULT '',
        extra       TEXT NOT NULL DEFAULT ''
    );
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_dir() / SQLITE_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(self.SCHEMA)
            self._migrate(db)

    @staticmethod
    def _migrate(db) -> None:
        """Досыпает колонки, появившиеся позже создания таблиц.

        CREATE TABLE IF NOT EXISTS не добавляет колонки в уже существующую
        таблицу, а ALTER TABLE ADD COLUMN падает, если колонка уже есть.
        Поэтому смотрим, что реально лежит в базе, и добавляем недостающее.
        """
        колонки = {r[1] for r in db.execute("PRAGMA table_info(task_state)")}
        if колонки and "approved" not in колонки:
            db.execute("ALTER TABLE task_state ADD COLUMN approved TEXT "
                       "NOT NULL DEFAULT '{}'")

    def __str__(self) -> str:
        return f"{self.label} · {self.path}"

    # ── интерфейс ───────────────────────────────────────────────────────
    def append(self, session: str, turn: Turn) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO turns (session, role, content, at, tokens, seconds)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session, turn.role, turn.content, turn.at, turn.tokens, turn.seconds),
            )

    def load(self, session: str, limit: int | None = None) -> list[Turn]:
        with self._connect() as db:
            if limit is None:
                rows = db.execute(
                    "SELECT role, content, at, tokens, seconds FROM turns"
                    " WHERE session = ? ORDER BY id",
                    (session,),
                ).fetchall()
                return [Turn(*row) for row in rows]

            # Последние N — выбираем с конца и разворачиваем: так в память
            # не поднимается весь диалог ради десяти реплик.
            rows = db.execute(
                "SELECT role, content, at, tokens, seconds FROM turns"
                " WHERE session = ? ORDER BY id DESC LIMIT ?",
                (session, limit),
            ).fetchall()
        return self._aligned([Turn(*row) for row in reversed(rows)])

    def totals(self, session: str) -> Totals:
        with self._connect() as db:
            row = db.execute(
                "SELECT"
                "  SUM(role = 'user'),"
                "  SUM(CASE WHEN role = 'user' THEN tokens ELSE 0 END),"
                "  SUM(CASE WHEN role = 'assistant' THEN tokens ELSE 0 END),"
                "  SUM(seconds)"
                " FROM turns WHERE session = ?",
                (session,),
            ).fetchone()
        return Totals(
            turns=int(row[0] or 0),
            prompt_tokens=int(row[1] or 0),
            completion_tokens=int(row[2] or 0),
            seconds=round(float(row[3] or 0.0), 1),
        )

    def sessions(self) -> list[SessionInfo]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT t.session, COUNT(*), SUM(t.tokens), MIN(t.at), MAX(t.at),"
                "  (SELECT content FROM turns f WHERE f.session = t.session"
                "     AND f.role = 'user' ORDER BY f.id LIMIT 1)"
                " FROM turns t GROUP BY t.session ORDER BY MAX(t.at) DESC"
            ).fetchall()
        return [
            SessionInfo(name=row[0], turns=int(row[1] or 0), tokens=int(row[2] or 0),
                        started=row[3] or "", updated=row[4] or "", first=row[5] or "")
            for row in rows
        ]

    def rename(self, session: str, new_name: str) -> bool:
        if not new_name or new_name == session:
            return False
        with self._connect() as db:
            занято = db.execute(
                "SELECT 1 FROM turns WHERE session = ? LIMIT 1", (new_name,)
            ).fetchone()
            если_есть = db.execute(
                "SELECT 1 FROM turns WHERE session = ? LIMIT 1", (session,)
            ).fetchone()
            if занято or not если_есть:
                return False
            # Три таблицы одной транзакцией: реплики, факты, пересказ.
            db.execute("UPDATE turns SET session = ? WHERE session = ?",
                       (new_name, session))
            db.execute("UPDATE facts SET session = ? WHERE session = ?",
                       (new_name, session))
            db.execute("UPDATE summaries SET session = ? WHERE session = ?",
                       (new_name, session))
            db.execute("UPDATE task_state SET session = ? WHERE session = ?",
                       (new_name, session))
        return True

    def clear(self, session: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM turns WHERE session = ?", (session,))
            # Пересказ тоже: иначе «забыть» оставило бы на диске выжимку
            # из стёртого диалога, и она всплыла бы при следующем запуске.
            db.execute("DELETE FROM summaries WHERE session = ?", (session,))
            db.execute("DELETE FROM facts WHERE session = ?", (session,))
            # И состояние задачи: иначе стёртый чат оставил бы после себя
            # этап и шаг, которые всплыли бы при следующем чате с тем же именем.
            db.execute("DELETE FROM task_state WHERE session = ?", (session,))

    def save_summary(self, session: str, summary: Summary) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO summaries (session, content, at, covered, tokens, rounds)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(session) DO UPDATE SET"
                "   content = excluded.content, at = excluded.at,"
                "   covered = excluded.covered, tokens = excluded.tokens,"
                "   rounds = excluded.rounds",
                (session, summary.content, summary.at, summary.covered,
                 summary.tokens, summary.rounds),
            )

    def load_summary(self, session: str) -> Summary | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT content, at, covered, tokens, rounds FROM summaries"
                " WHERE session = ?",
                (session,),
            ).fetchone()
        return Summary(*row) if row else None

    def save_facts(self, session: str, facts: list[Fact]) -> None:
        with self._connect() as db:
            # Карточка фактов переписывается целиком: так исчезнувший из
            # диалога факт не остаётся висеть на диске навсегда.
            db.execute("DELETE FROM facts WHERE session = ?", (session,))
            db.executemany(
                "INSERT INTO facts (session, key, value, at) VALUES (?, ?, ?, ?)",
                [(session, f.key, f.value, f.at) for f in facts],
            )

    def load_facts(self, session: str) -> list[Fact]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT key, value, at FROM facts WHERE session = ? ORDER BY key",
                (session,),
            ).fetchall()
        return [Fact(*row) for row in rows]

    def remember(self, memo: Memo) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO longterm (key, value, kind, at, source)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                "   kind = excluded.kind, at = excluded.at, source = excluded.source",
                (memo.key, memo.value, memo.kind, memo.at, memo.source),
            )

    def recall(self, kind: str | None = None) -> list[Memo]:
        with self._connect() as db:
            if kind:
                rows = db.execute(
                    "SELECT key, value, kind, at, source FROM longterm"
                    " WHERE kind = ? ORDER BY key", (kind,)).fetchall()
            else:
                rows = db.execute(
                    "SELECT key, value, kind, at, source FROM longterm"
                    " ORDER BY kind, key").fetchall()
        return [Memo(*row) for row in rows]

    def forget(self, key: str) -> bool:
        with self._connect() as db:
            cur = db.execute("DELETE FROM longterm WHERE key = ?", (key,))
            return cur.rowcount > 0

    def save_invariant(self, inv: Invariant) -> Invariant:
        with self._connect() as db:
            if inv.id:
                db.execute(
                    "UPDATE invariants SET text = ?, rationale = ?, scope = ?,"
                    " active = ?, at = ? WHERE id = ?",
                    (inv.text, inv.rationale, inv.scope, int(inv.active),
                     inv.at, inv.id))
            else:
                cur = db.execute(
                    "INSERT INTO invariants (text, rationale, scope, active, at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (inv.text, inv.rationale, inv.scope, int(inv.active), inv.at))
                inv.id = int(cur.lastrowid)
        return inv

    def invariants(self, *, only_active: bool = False) -> list[Invariant]:
        запрос = ("SELECT id, text, rationale, scope, active, at FROM invariants"
                  + (" WHERE active = 1" if only_active else "") + " ORDER BY id")
        with self._connect() as db:
            rows = db.execute(запрос).fetchall()
        return [Invariant(id=r[0], text=r[1], rationale=r[2], scope=r[3],
                          active=bool(r[4]), at=r[5]) for r in rows]

    def delete_invariant(self, inv_id: int) -> bool:
        with self._connect() as db:
            return db.execute("DELETE FROM invariants WHERE id = ?",
                              (inv_id,)).rowcount > 0

    def save_task(self, session: str, task: TaskState) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO task_state (session, stage, step, expecting, goal,"
                " updated, log, approved) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(session) DO UPDATE SET stage = excluded.stage,"
                "   step = excluded.step, expecting = excluded.expecting,"
                "   goal = excluded.goal, updated = excluded.updated,"
                "   log = excluded.log, approved = excluded.approved",
                (session, task.stage, task.step, task.expecting, task.goal,
                 task.updated, json.dumps(task.log, ensure_ascii=False),
                 json.dumps(task.approved, ensure_ascii=False)),
            )

    def load_task(self, session: str) -> TaskState | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT stage, step, expecting, goal, updated, log, approved"
                " FROM task_state WHERE session = ?", (session,)).fetchone()
        if not row:
            return None

        def разобрать(сырое, пусто):
            try:
                return json.loads(сырое or пусто)
            except json.JSONDecodeError:
                return json.loads(пусто)

        return TaskState(stage=row[0], step=row[1], expecting=row[2], goal=row[3],
                         updated=row[4], log=разобрать(row[5], "[]"),
                         approved=разобрать(row[6], "{}"))

    ПРОФИЛЬ_ПОЛЯ = ("name", "tone", "format", "level", "constraints",
                    "language", "extra")

    def save_profile(self, profile: Profile) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO profiles (name, tone, format, level, constraints,"
                " language, extra) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET tone = excluded.tone,"
                "   format = excluded.format, level = excluded.level,"
                "   constraints = excluded.constraints,"
                "   language = excluded.language, extra = excluded.extra",
                tuple(getattr(profile, поле) for поле in self.ПРОФИЛЬ_ПОЛЯ),
            )

    def load_profile(self, name: str) -> Profile | None:
        with self._connect() as db:
            row = db.execute(
                f"SELECT {', '.join(self.ПРОФИЛЬ_ПОЛЯ)} FROM profiles WHERE name = ?",
                (name,)).fetchone()
        return Profile(*row) if row else None

    def profiles(self) -> list[Profile]:
        with self._connect() as db:
            rows = db.execute(
                f"SELECT {', '.join(self.ПРОФИЛЬ_ПОЛЯ)} FROM profiles ORDER BY name"
            ).fetchall()
        return [Profile(*row) for row in rows]

    def delete_profile(self, name: str) -> bool:
        with self._connect() as db:
            return db.execute("DELETE FROM profiles WHERE name = ?",
                              (name,)).rowcount > 0

    # ── внутреннее ──────────────────────────────────────────────────────
    @contextmanager
    def _connect(self):
        """Соединение на операцию: так хранилище безопасно из любого потока.

        Держать одно соединение на весь процесс было бы чуть быстрее, но
        потребовало бы check_same_thread=False и своего замка — а веб-сервер
        здесь многопоточный. Открытие файла стоит микросекунды, не тот случай,
        где имеет смысл экономить.
        """
        try:
            db = sqlite3.connect(self.path, timeout=10)
        except sqlite3.Error as exc:
            raise MemoryError_(f"Не открывается база {self.path}: {exc}") from exc
        try:
            with db:                      # коммит на выходе, откат при ошибке
                yield db
        except sqlite3.Error as exc:
            raise MemoryError_(f"Ошибка базы {self.path}: {exc}") from exc
        finally:
            db.close()


def open_store(kind: str = "sqlite", location: Path | str | None = None) -> Store:
    """Фабрика: `--store json` или `--store sqlite` из интерфейсов."""
    kind = (kind or "").strip().lower()
    if kind == "json":
        return JsonStore(location)
    if kind in ("sqlite", "sql", "db"):
        return SqliteStore(location)
    raise MemoryError_(f"Неизвестное хранилище: {kind!r}. Ожидалось json или sqlite")


def _safe_name(session: str) -> str:
    """Имя сессии → имя файла. Кириллица проходит как есть, мусор — нет.

    Ведущее подчёркивание срезается: файлы с ним считаются служебными
    (_longterm.json, _profiles.json) и в список диалогов не попадают.
    """
    cleaned = "".join(
        char if (char.isalnum() or char in "-_") else "-"
        for char in session.strip()
    ).strip("-").lstrip("_")
    return cleaned or DEFAULT_SESSION


__all__ = ["Store", "JsonStore", "SqliteStore", "Turn", "SessionInfo", "Totals",
           "Summary", "Fact", "Memo", "Profile", "TaskState", "Invariant",
           "INVARIANT_SCOPES", "SCOPE_LABELS", "ARCH", "STACK", "RULE",
           "STAGES", "TRANSITIONS", "STAGE_LABELS", "GUARDS", "STAGE_RIGHTS",
           "PLANNING", "EXECUTION", "VALIDATION", "DONE",
           "open_store", "default_dir", "now_iso",
           "local_time", "DEFAULT_SESSION", "ENV_DIR_VAR", "MemoryError_",
           "SHORT", "WORKING", "LONG", "LAYERS",
           "PROFILE", "DECISION", "KNOWLEDGE", "LONG_KINDS"]
