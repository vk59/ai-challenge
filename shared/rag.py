"""RAG: ответ по найденным кускам (день 22).

Вся суть в одной функции: вопрос → поиск по индексу → куски в промпт →
ответ модели. Без поиска модель отвечает из того, что помнит о мире;
с поиском — из наших документов.

Два решения, которые определяют качество:

**Куски нумеруются, и модель обязана ссылаться номерами.** Иначе
проверить ответ нельзя: непонятно, взято утверждение из документа или
придумано. С номерами проверка механическая — ссылка либо указывает
на выданный кусок, либо выдумана.

**Модели прямо разрешено сказать «в документах этого нет».** Без такого
разрешения она заполняет пробел правдоподобной выдумкой: это для неё
естественнее, чем признать нехватку данных.

    ответ = спросить("почему сжатие бывает дороже", режим="rag")
    print(ответ.text, ответ.sources)
"""

import re
from dataclasses import dataclass, field

from index import STRUCTURAL, Index, IndexError_
from llm import LLMError, ask
from tokens import money

# Сколько кусков кладём в промпт. Пять — компромисс: меньше трёх и нужное
# не попадает, больше семи и ответ размывается, а токены растут линейно.
КУСКОВ = 5

БЕЗ_RAG = ("Ты отвечаешь на вопросы про проект «AI Advent Challenge» — "
           "репозиторий с ежедневными заданиями по разработке ИИ-агентов. "
           "Отвечай кратко и по делу. Если не знаешь — так и скажи, "
           "не придумывай.")

С_RAG = """Ты отвечаешь на вопросы про проект «AI Advent Challenge»,
опираясь ТОЛЬКО на выдержки из документации проекта, приведённые ниже.

Правила, обязательные к исполнению:

1. Каждое утверждение подкрепляй ссылкой на номер выдержки: [1], [2].
   Утверждение без ссылки считается выдумкой.
2. Если в выдержках ответа нет — напиши прямо: «В документах этого нет».
   Не достраивай правдоподобное: лучше признать нехватку, чем угадать.
3. Не пересказывай выдержки целиком, отвечай на заданный вопрос.
4. Отвечай кратко: два-четыре предложения.

Выдержки из документации:

{выдержки}"""


@dataclass
class Ответ:
    """Ответ вместе со всем, что нужно для проверки."""

    text: str
    mode: str                           # plain | rag
    sources: list[str] = field(default_factory=list)      # на что ссылался
    chunks: list[dict] = field(default_factory=list)      # что было выдано
    cited: list[int] = field(default_factory=list)        # какие номера назвал
    invented: list[int] = field(default_factory=list)     # номера, которых не было
    refused: bool = False               # сказал «в документах этого нет»
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0
    search_seconds: float = 0.0

    @property
    def prompt_tokens(self) -> int:
        return int(self.usage.get("prompt_tokens") or 0)

    @property
    def total_tokens(self) -> int:
        return int(self.usage.get("total_tokens") or 0)

    @property
    def money(self) -> float:
        return money(self.usage, self.usage.get("model", ""))

    def as_dict(self) -> dict:
        return {"text": self.text, "mode": self.mode, "sources": self.sources,
                "chunks": self.chunks, "cited": self.cited,
                "invented": self.invented, "refused": self.refused,
                "prompt_tokens": self.prompt_tokens,
                "total_tokens": self.total_tokens,
                "seconds": round(self.seconds, 2),
                "search_seconds": round(self.search_seconds, 2)}


ССЫЛКА = re.compile(r"\[(\d{1,2})\]")
ОТКАЗ = re.compile(r"в документах (этого|ничего) нет|нет (этого|такого) "
                   r"в документах|в выдержках этого нет", re.I)


def собрать_выдержки(куски: list[dict]) -> str:
    """Нумерованные выдержки для промпта.

    Источник и раздел идут в шапке каждой выдержки не для красоты: без них
    модель не может сослаться осмысленно, а человек — проверить ссылку.
    """
    части = []
    for номер, к in enumerate(куски, 1):
        части.append(f"[{номер}] {к['source']} — раздел «{к['section']}»\n"
                     f"{к['text'].strip()}")
    return "\n\n".join(части)


def спросить(вопрос: str, *, режим: str = "rag", индекс: Index | None = None,
             стратегия: str = STRUCTURAL, кусков: int = КУСКОВ,
             max_tokens: int = 600, temperature: float = 0.2) -> Ответ:
    """Вопрос → ответ. При режиме «rag» сначала ищет по индексу.

    Режим «plain» намеренно оставлен максимально близким: та же модель,
    та же температура, тот же лимит. Отличается только наличие выдержек,
    иначе сравнение сравнивало бы не то.
    """
    вопрос = (вопрос or "").strip()
    if not вопрос:
        raise LLMError("Пустой вопрос")

    if режим == "plain":
        ответ = ask(вопрос, system=БЕЗ_RAG, max_tokens=max_tokens,
                    temperature=temperature)
        return Ответ(text=ответ.text, mode="plain", usage=ответ.usage,
                     seconds=ответ.seconds)

    if режим != "rag":
        raise LLMError(f"Неизвестный режим: {режим}")

    import time
    индекс = индекс or Index()
    начало = time.monotonic()
    найденное = индекс.search(вопрос, стратегия, k=кусков)
    поиск = time.monotonic() - начало

    куски = [{**кусок.as_dict(full=True), "score": round(оценка, 4)}
             for кусок, оценка in найденное]
    система = С_RAG.format(выдержки=собрать_выдержки(куски))

    ответ = ask(вопрос, system=система, max_tokens=max_tokens,
                temperature=temperature)

    # Разбираем ссылки: какие номера модель назвала и все ли они настоящие.
    названные = sorted({int(н) for н in ССЫЛКА.findall(ответ.text or "")})
    настоящие = [н for н in названные if 1 <= н <= len(куски)]
    выдуманные = [н for н in названные if not 1 <= н <= len(куски)]
    источники = sorted({куски[н - 1]["source"] for н in настоящие})

    return Ответ(text=ответ.text, mode="rag", sources=источники, chunks=куски,
                 cited=настоящие, invented=выдуманные,
                 refused=bool(ОТКАЗ.search(ответ.text or "")),
                 usage=ответ.usage, seconds=ответ.seconds,
                 search_seconds=поиск)


def оба_режима(вопрос: str, **настройки) -> dict[str, Ответ]:
    """Один вопрос в двух режимах — для сравнения бок о бок."""
    итог = {}
    for режим in ("plain", "rag"):
        свои = dict(настройки)
        if режим == "plain":
            # Поисковые настройки к режиму без RAG неприменимы.
            for лишнее in ("индекс", "стратегия", "кусков"):
                свои.pop(лишнее, None)
        итог[режим] = спросить(вопрос, режим=режим, **свои)
    return итог


__all__ = ["спросить", "оба_режима", "Ответ", "собрать_выдержки",
           "КУСКОВ", "БЕЗ_RAG", "С_RAG", "IndexError_", "LLMError"]
