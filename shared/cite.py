"""Ответы с обязательными источниками и цитатами (день 24).

День 22 проверял номера ссылок: указал модель [4] — значит такая выдержка
существовала. Этого мало. Номер можно поставить правильно, а утверждение
рядом с ним выдумать. Поэтому здесь модель обязана приводить ДОСЛОВНЫЕ
фрагменты, и каждый фрагмент проверяется машинно: он либо есть в выданном
куске, либо выдуман. Третьего нет.

Отказ устроен в два слоя, потому что одного не хватает:

    порог близости   топ-1 ниже порога — отказ без обращения к модели.
                     Отделяет вопросы не по теме; замер показал чистый
                     зазор: у своих вопросов топ-1 не ниже 0.402, у чужих
                     не выше 0.370.
    отказ модели      вопрос по теме, но ответа в документах нет. Близость
                     тут высокая («когда дедлайн 30-го дня» даёт 0.481),
                     и порогом это не поймать — только суждением модели.

    result = answer_with_citations("какой MRR у структурной нарезки")
    print(result.answer, result.quotes, result.verification)
"""

import json
import re
from dataclasses import dataclass, field

from index import STRUCTURAL, Index
from llm import LLMError, ask

# Порог отказа по близости. Выбран по замеру: середина зазора между
# минимумом своих вопросов (0.402) и максимумом чужих (0.370).
ABSTAIN_BELOW = 0.39

# Доля цитаты, которую достаточно найти дословно, чтобы счесть её
# подтверждённой. Не 100%, потому что модель режет края по-своему:
# добавляет точку, глотает кавычку. Но и не 50% — тогда подтверждением
# стало бы любое совпадение половины фразы.
QUOTE_MATCH_RATIO = 0.9

MIN_QUOTE_LENGTH = 12

SYSTEM = """Ты отвечаешь на вопросы о проекте «AI Advent Challenge», опираясь
ТОЛЬКО на выдержки из документации, приведённые ниже.

Отвечай строго в формате JSON:

{{
  "answer": "ответ в два-четыре предложения",
  "sources": [{{"n": 1}}, {{"n": 3}}],
  "quotes": [{{"n": 1, "text": "дословный фрагмент из выдержки 1"}}],
  "confident": true
}}

Правила, обязательные к исполнению:

1. В "quotes" фрагменты копируются из выдержек ЗНАК В ЗНАК. Не пересказывай,
   не исправляй, не сокращай середину. Фрагмент — от 15 до 300 знаков.
2. Каждое утверждение в "answer" должно подтверждаться хотя бы одной
   цитатой. Утверждение без цитаты запрещено.
3. В "sources" перечисли номера выдержек, которыми пользовался.
4. Если в выдержках ответа нет — поставь "confident": false, в "answer"
   напиши, что в документах этого нет, и спроси, что уточнить. "quotes"
   тогда оставь пустым.
5. Ничего, кроме JSON, не выводи.

Выдержки из документации:

{excerpts}"""

REMINDER = """Ты ответил без цитат. Это запрещено: перечитай выдержки
и верни тот же ответ, но с заполненными "sources" и "quotes" — дословными
фрагментами из выдержек. Если подтвердить ответ цитатами нельзя, поставь
"confident": false и скажи, что в документах этого нет."""

CLARIFY_TEMPLATE = (
    "Не знаю — в документации проекта ничего близкого к этому вопросу "
    "не нашлось (лучшее совпадение {score:.2f} при пороге {threshold:.2f}).\n\n"
    "Уточните, пожалуйста: о каком дне или части проекта речь? "
    "Я отвечаю по README дней, документам из docs/ и коду в shared/."
)


class CiteError(LLMError):
    """Ответ с цитатами не получился."""


@dataclass
class Quote:
    """Фрагмент, который модель выдала за дословный."""

    n: int
    text: str
    verified: bool = False
    matched_ratio: float = 0.0
    source: str = ""
    section: str = ""
    chunk_id: str = ""

    def as_dict(self) -> dict:
        return {"n": self.n, "text": self.text, "verified": self.verified,
                "matched_ratio": round(self.matched_ratio, 3),
                "source": self.source, "section": self.section,
                "chunk_id": self.chunk_id}


@dataclass
class Source:
    """Источник так, как требует задание: файл, раздел, идентификатор куска."""

    n: int
    source: str
    section: str
    chunk_id: str
    score: float = 0.0

    def as_dict(self) -> dict:
        return {"n": self.n, "source": self.source, "section": self.section,
                "chunk_id": self.chunk_id, "score": round(self.score, 4)}


@dataclass
class CitedAnswer:
    """Ответ вместе со всем, что позволяет его проверить."""

    question: str
    answer: str
    confident: bool = True
    abstained: bool = False
    abstain_reason: str = ""
    sources: list[Source] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)
    chunks: list[dict] = field(default_factory=list)
    top_score: float = 0.0
    raw: str = ""
    parse_failed: bool = False
    retried: bool = False
    blank: bool = False              # модель вернула одни пробелы
    blank_retries: int = 0
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def verified_quotes(self) -> int:
        return sum(1 for q in self.quotes if q.verified)

    @property
    def fabricated_quotes(self) -> int:
        return sum(1 for q in self.quotes if not q.verified)

    @property
    def has_sources(self) -> bool:
        return bool(self.sources)

    @property
    def has_quotes(self) -> bool:
        return bool(self.quotes)

    @property
    def total_tokens(self) -> int:
        return int(self.usage.get("total_tokens") or 0)

    @property
    def prompt_tokens(self) -> int:
        return int(self.usage.get("prompt_tokens") or 0)

    @property
    def answer_tokens(self) -> int:
        return int(self.usage.get("completion_tokens") or 0)

    def as_dict(self) -> dict:
        return {"question": self.question, "answer": self.answer,
                "confident": self.confident, "abstained": self.abstained,
                "abstain_reason": self.abstain_reason,
                "sources": [s.as_dict() for s in self.sources],
                "quotes": [q.as_dict() for q in self.quotes],
                "chunks": self.chunks, "top_score": round(self.top_score, 4),
                "verified_quotes": self.verified_quotes,
                "fabricated_quotes": self.fabricated_quotes,
                "parse_failed": self.parse_failed,
                "retried": self.retried, "blank": self.blank,
                "blank_retries": self.blank_retries,
                "total_tokens": self.total_tokens,
                "seconds": round(self.seconds, 2)}


def normalize(text: str) -> str:
    """Схлопнуть пробелы и привести к нижнему регистру.

    Больше ничего. Соблазн выкинуть пунктуацию и кавычки велик, но тогда
    проверка начнёт подтверждать пересказы, а в этом весь смысл дня.
    """
    return " ".join((text or "").split()).lower()


def verify_quote(quote: str, haystack: str) -> tuple[bool, float]:
    """Есть ли фрагмент в тексте куска дословно.

    Многоточие внутри цитаты разрешено: модель так показывает пропуск.
    Тогда части проверяются по очереди и должны идти в том же порядке —
    иначе из двух далёких обрывков можно собрать утверждение, которого
    в документе нет.
    """
    needle = normalize(quote)
    hay = normalize(haystack)
    if not needle or not hay:
        return False, 0.0
    # Слишком короткий фрагмент не цитата, даже если он дословный. «TCC»
    # есть в тексте, но такой ссылкой можно прикрыть любое утверждение —
    # подтверждением она не является.
    if len(needle) < MIN_QUOTE_LENGTH:
        return False, 0.0
    if needle in hay:
        return True, 1.0

    parts = [p for p in re.split(r"\s*(?:…|\.\.\.)\s*", needle) if len(p) > 3]
    if len(parts) > 1:
        position, matched = 0, 0
        for part in parts:
            found = hay.find(part, position)
            if found == -1:
                break
            matched += len(part)
            position = found + len(part)
        ratio = matched / sum(len(p) for p in parts)
        return ratio >= QUOTE_MATCH_RATIO, ratio

    # Модель могла прихватить лишний знак с краю. Обрезаем края и пробуем
    # снова: это не поддавка, совпадать всё равно должно почти всё.
    best = 0.0
    for trim in range(1, max(2, int(len(needle) * (1 - QUOTE_MATCH_RATIO)) + 1)):
        for candidate in (needle[trim:], needle[:-trim], needle[trim:-trim]):
            if len(candidate) >= MIN_QUOTE_LENGTH and candidate in hay:
                best = max(best, len(candidate) / len(needle))
    return best >= QUOTE_MATCH_RATIO, best


def build_excerpts(chunks: list[dict]) -> str:
    return "\n\n".join(
        f"[{number}] {c['source']} — раздел «{c['section']}» "
        f"(chunk_id {c['chunk_id']})\n{c['text'].strip()}"
        for number, c in enumerate(chunks, 1))


def _parse(raw: str) -> dict | None:
    """JSON из ответа. Модель иногда оборачивает его в ```json."""
    text = (raw or "").strip()
    block = re.search(r"\{.*\}", text, re.S)
    if not block:
        return None
    try:
        data = json.loads(block.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def answer_with_citations(
        question: str, *, index: Index | None = None,
        strategy: str = STRUCTURAL, k: int = 5,
        abstain_below: float | None = ABSTAIN_BELOW,
        retrieval=None, max_tokens: int = 900,
        temperature: float = 0.1,
        system_prefix: str | None = None,
        history: list[dict] | None = None,
        require_citations: bool = False) -> CitedAnswer:
    """Вопрос → ответ с проверенными цитатами, либо честный отказ.

    `retrieval` подставляет готовую выдачу — например из дня 23,
    с реранкингом. Тогда поиск здесь не делается повторно.

    `system_prefix` и `history` добавлены в дне 25: в диалоге ответ зависит
    не только от выдержек, но и от того, о чём уже говорили и какова цель.
    Требования к цитатам при этом не меняются — приставка идёт ПЕРЕД
    правилами, а не вместо них.

    `require_citations` — оттуда же. С длинной историей модель иногда
    отвечает уверенно, но поля "sources" и "quotes" оставляет пустыми:
    контекст разговора перетягивает внимание с формата. Один повторный
    запрос с прямым напоминанием это исправляет. По умолчанию выключено,
    чтобы поведение дня 24 не менялось.
    """
    import time

    question = (question or "").strip()
    if not question:
        raise CiteError("Пустой вопрос")

    index = index or Index()
    if retrieval is not None:
        pairs = [(hit.chunk, hit.score) for hit in retrieval.hits]
    else:
        pairs = index.search(question, strategy, k=k)

    chunks = [{"source": c.source, "section": c.section,
               "chunk_id": c.chunk_id, "text": c.text,
               "score": round(s, 4)} for c, s in pairs]
    top = max((c["score"] for c in chunks), default=0.0)

    # Первый слой отказа: до модели дело не доходит, и это экономит токены.
    if abstain_below is not None and top < abstain_below:
        return CitedAnswer(
            question=question,
            answer=CLARIFY_TEMPLATE.format(score=top, threshold=abstain_below),
            confident=False, abstained=True,
            abstain_reason=f"топ-1 близость {top:.3f} ниже порога "
                           f"{abstain_below:.2f}",
            chunks=chunks, top_score=top)

    system = SYSTEM.format(excerpts=build_excerpts(chunks))
    if system_prefix:
        system = f"{system_prefix.strip()}\n\n{system}"

    started = time.monotonic()
    reply = ask(question, system=system, history=history,
                max_tokens=max_tokens, temperature=temperature, json_mode=True)

    # Пустой ответ. Модель изредка возвращает одни пробелы — попался
    # в дне 25 на трёх репликах из двенадцати в длинном диалоге, причём
    # в двадцати изолированных попытках с тем же промптом, историей
    # и приставкой не воспроизвёлся ни разу. Причину изолировать
    # не удалось, поэтому обнаруживаем и повторяем: пустой ответ
    # пользователю хуже лишнего запроса.
    blank_retries = 0
    while not (reply.text or "").strip() and blank_retries < 2:
        blank_retries += 1
        reply = ask(question, system=system, history=history,
                    max_tokens=max_tokens, temperature=temperature,
                    json_mode=True)
    spent = time.monotonic() - started

    if not (reply.text or "").strip():
        return CitedAnswer(
            question=question,
            answer="Модель вернула пустой ответ — попробуйте спросить ещё раз.",
            confident=False, chunks=chunks, top_score=top,
            raw=reply.text or "", parse_failed=True, blank=True,
            blank_retries=blank_retries, usage=reply.usage, seconds=spent)

    data = _parse(reply.text)
    if data is None:
        # Не падаем: ответ модели есть, он просто не разобрался. Показать
        # сырой текст честнее, чем изобразить пустой результат.
        return CitedAnswer(question=question, answer=(reply.text or "").strip(),
                           confident=False, chunks=chunks, top_score=top,
                           raw=reply.text or "", parse_failed=True,
                           usage=reply.usage, seconds=spent)

    sources: list[Source] = []
    for item in data.get("sources") or []:
        number = int(item.get("n", 0)) if isinstance(item, dict) else int(item or 0)
        if 1 <= number <= len(chunks):
            c = chunks[number - 1]
            sources.append(Source(n=number, source=c["source"],
                                  section=c["section"], chunk_id=c["chunk_id"],
                                  score=c["score"]))

    quotes: list[Quote] = []
    for item in data.get("quotes") or []:
        if not isinstance(item, dict):
            continue
        number = int(item.get("n", 0))
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        quote = Quote(n=number, text=text)
        if 1 <= number <= len(chunks):
            c = chunks[number - 1]
            quote.source, quote.section = c["source"], c["section"]
            quote.chunk_id = c["chunk_id"]
            quote.verified, quote.matched_ratio = verify_quote(text, c["text"])
        else:
            # Номер вне диапазона — цитата заведомо ни на что не опирается.
            quote.verified, quote.matched_ratio = False, 0.0
        quotes.append(quote)

    # Источники, упомянутые только в цитатах, тоже источники: модель иногда
    # заполняет "sources" небрежно, а цитату ставит правильно.
    known = {s.n for s in sources}
    for quote in quotes:
        if quote.n not in known and 1 <= quote.n <= len(chunks):
            c = chunks[quote.n - 1]
            sources.append(Source(n=quote.n, source=c["source"],
                                  section=c["section"], chunk_id=c["chunk_id"],
                                  score=c["score"]))
            known.add(quote.n)
    sources.sort(key=lambda s: s.n)

    result = CitedAnswer(
        question=question, answer=str(data.get("answer") or "").strip(),
        confident=bool(data.get("confident", True)),
        sources=sources, quotes=quotes, chunks=chunks, top_score=top,
        raw=reply.text or "", blank_retries=blank_retries,
        usage=reply.usage, seconds=spent)

    # Уверенный ответ без цитат — нарушение договора. Просим один раз
    # исправиться, рекурсией с выключенным флагом, чтобы не зациклиться.
    if require_citations and result.confident and not result.quotes:
        again = answer_with_citations(
            question, index=index, strategy=strategy, k=k,
            abstain_below=None, retrieval=retrieval, max_tokens=max_tokens,
            temperature=temperature,
            system_prefix=f"{system_prefix or ''}\n\n{REMINDER}".strip(),
            history=history, require_citations=False)
        again.retried = True
        again.usage = {
            key: int(result.usage.get(key) or 0) + int(again.usage.get(key) or 0)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        again.seconds += result.seconds
        # Если и со второго раза цитат нет — отдаём вторую попытку как есть:
        # врать про успех нельзя, зато видно, что попытка была.
        return again

    return result


__all__ = ["answer_with_citations", "CitedAnswer", "Quote", "Source",
           "verify_quote", "normalize", "build_excerpts", "CiteError",
           "ABSTAIN_BELOW", "QUOTE_MATCH_RATIO"]
