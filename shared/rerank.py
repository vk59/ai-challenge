"""Второй этап после поиска: фильтр, реранкинг, переписывание запроса (день 23).

День 22 показал предел простого поиска: нужный кусок иногда стоит восьмым,
а в промпт уходит пять, и модель честно отказывается отвечать. Лечится это
не увеличением K — тогда растут токены и шум, — а вторым этапом.

Три приёма, каждый со своей областью применения:

    threshold  отсечь по косинусной близости. Дёшево, мгновенно, но тупо:
               на нашем корпусе золотые куски начинаются с 0.393, а мусор
               доходит до 0.637 — распределения перекрываются, и одним
               порогом их не разделить. Годится отрезать явный шум, а не
               повышать точность.
    rerank     переупорядочить моделью. Берём широкую выдачу и просим
               оценить каждый кусок по отношению к вопросу. Дорого (ещё
               один запрос), зато разбирается в смысле, а не в геометрии.
    rewrite    переписать вопрос перед поиском. «Связь по стандартному
               вводу-выводу» эмбеддинг не связывает с `stdio`; модель,
               знающая предметную область, подставит нужные термины.

Порядок важен: сначала rewrite (меняет саму выдачу), потом threshold
(убирает дешёвый мусор, чтобы не платить за его реранкинг), потом rerank.
"""

import json
import re
from dataclasses import dataclass, field

from index import STRUCTURAL, Chunk, Index
from llm import LLMError, ask

# Сколько берём из индекса до фильтрации и сколько оставляем после.
WIDE_K = 20
FINAL_K = 5

# Порог подобран по замеру, а не на глаз: ниже 0.33 на нашем корпусе
# не попадал ни один золотой кусок, а мусора отсекается заметная часть.
# Выше ставить нельзя — потеряем ответы на вопросы 5, 6 и 7.
THRESHOLD = 0.33

RERANK_PROMPT = """Ты оцениваешь, насколько выдержка из документации помогает
ответить на вопрос.

Вопрос: {question}

Для каждой выдержки поставь оценку от 0 до 10:
  10 — содержит прямой ответ
   7 — содержит важную часть ответа
   4 — про ту же тему, но ответа нет
   0 — не по теме

Верни ТОЛЬКО JSON вида {{"scores": [{{"n": 1, "s": 7}}, {{"n": 2, "s": 0}}]}}
по одной записи на каждую выдержку, без пояснений.

Выдержки:
{excerpts}"""

REWRITE_PROMPT = """Перепиши вопрос в поисковый запрос для векторного поиска
по документации проекта «AI Advent Challenge» — репозиторий с ежедневными
заданиями по разработке ИИ-агентов на Python.

Задача: подставить термины, которыми это записано в документации, вместо
бытовых формулировок. Например «связь по стандартному вводу-выводу» →
«stdio MCP»; «компьютер спал и задача не выполнилась» → «наверстывание
пропущенных запусков».

Верни ТОЛЬКО переписанный запрос, одной строкой, без кавычек и пояснений.
Сохрани ключевые слова исходного вопроса, добавив к ним термины.

Вопрос: {question}"""


class RerankError(LLMError):
    """Второй этап не отработал."""


@dataclass
class Hit:
    """Кусок с оценками обоих этапов."""

    chunk: Chunk
    score: float                 # косинусная близость
    rank: int = 0                # место в исходной выдаче
    rerank_score: float | None = None    # оценка модели, если был реранкинг
    dropped_by: str = ""         # чем отсечён: threshold | rerank | ""

    @property
    def final_score(self) -> float:
        return self.score if self.rerank_score is None else self.rerank_score

    def as_dict(self, *, full: bool = False) -> dict:
        return {**self.chunk.as_dict(full=full), "score": round(self.score, 4),
                "rank": self.rank, "rerank_score": self.rerank_score,
                "dropped_by": self.dropped_by}


@dataclass
class Retrieval:
    """Всё, что произошло между вопросом и промптом."""

    question: str
    query: str                   # что на самом деле ушло в поиск
    rewritten: bool = False
    hits: list[Hit] = field(default_factory=list)       # финальные
    considered: list[Hit] = field(default_factory=list)  # все до отсечения
    stages: list[dict] = field(default_factory=list)    # журнал этапов
    usage: dict = field(default_factory=dict)           # токены второго этапа
    seconds: float = 0.0

    @property
    def wide_count(self) -> int:
        return len(self.considered)

    @property
    def final_count(self) -> int:
        return len(self.hits)

    def as_dict(self, *, full: bool = False) -> dict:
        return {"question": self.question, "query": self.query,
                "rewritten": self.rewritten,
                "hits": [h.as_dict(full=full) for h in self.hits],
                "considered": [h.as_dict() for h in self.considered],
                "stages": self.stages,
                "wide_count": self.wide_count,
                "final_count": self.final_count,
                "tokens": int(self.usage.get("total_tokens") or 0),
                "seconds": round(self.seconds, 2)}


def rewrite_query(question: str, *, max_tokens: int = 120,
                  provider=None, model: str | None = None
                  ) -> tuple[str, dict]:
    """Вопрос → поисковый запрос с терминами предметной области."""
    answer = ask(REWRITE_PROMPT.format(question=question),
                 provider=provider, model=model,
                 max_tokens=max_tokens, temperature=0.0)
    query = " ".join((answer.text or "").split()).strip().strip('"«»')
    # Пустой или подозрительно длинный ответ — повод не трогать вопрос:
    # плохой перепис хуже отсутствия переписа.
    if not query or len(query) > len(question) * 3:
        return question, answer.usage
    return query, answer.usage


def filter_by_threshold(hits: list[Hit], threshold: float) -> list[Hit]:
    """Отсечь по близости. Помечает отсечённые, а не выбрасывает молча."""
    kept = []
    for hit in hits:
        if hit.score < threshold:
            hit.dropped_by = "threshold"
        else:
            kept.append(hit)
    return kept


def _parse_scores(text: str, how_many: int) -> dict[int, float]:
    """Оценки из ответа модели. JSON приходит то в блоке, то с болтовнёй."""
    raw = text or ""
    block = re.search(r"\{.*\}", raw, re.S)
    if block:
        try:
            data = json.loads(block.group(0))
            scores = {}
            for item in data.get("scores") or []:
                number = int(item.get("n", 0))
                if 1 <= number <= how_many:
                    scores[number] = float(item.get("s", 0))
            if scores:
                return scores
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    # Падать нельзя: без оценок просто остаётся порядок поиска.
    pairs = re.findall(r'"?n"?\s*[:=]\s*(\d+)\D+"?s"?\s*[:=]\s*([\d.]+)', raw)
    return {int(n): float(s) for n, s in pairs if 1 <= int(n) <= how_many}


def rerank_with_llm(question: str, hits: list[Hit], *, keep: int = FINAL_K,
                    max_tokens: int = 700, provider=None,
                    model: str | None = None) -> tuple[list[Hit], dict]:
    """Переупорядочить куски моделью и оставить лучшие.

    Куски даются сокращёнными: для оценки релевантности хватает первых
    строк, а полный текст двадцати кусков — это лишние тысячи токенов
    на каждый вопрос.
    """
    if not hits:
        return [], {}

    excerpts = "\n\n".join(
        f"[{number}] {hit.chunk.source} — «{hit.chunk.section}»\n"
        f"{' '.join(hit.chunk.text.split())[:420]}"
        for number, hit in enumerate(hits, 1))

    answer = ask(RERANK_PROMPT.format(question=question, excerpts=excerpts),
                 provider=provider, model=model,
                 max_tokens=max_tokens, temperature=0.0, json_mode=True)
    scores = _parse_scores(answer.text, len(hits))

    for number, hit in enumerate(hits, 1):
        hit.rerank_score = scores.get(number)

    # Куски без оценки не выбрасываем: это молчание модели, а не ноль.
    # Ставим их по исходному порядку поиска, после оценённых.
    def sort_key(pair):
        position, hit = pair
        has_score = hit.rerank_score is not None
        return (0 if has_score else 1,
                -(hit.rerank_score or 0.0),
                position)

    ordered = [hit for _, hit in sorted(enumerate(hits), key=sort_key)]
    for hit in ordered[keep:]:
        hit.dropped_by = "rerank"
    return ordered[:keep], answer.usage


def retrieve(question: str, *, index: Index | None = None,
             strategy: str = STRUCTURAL, wide_k: int = WIDE_K,
             final_k: int = FINAL_K, threshold: float | None = None,
             rerank: bool = False, rewrite: bool = False,
             provider=None, model: str | None = None) -> Retrieval:
    """Поиск со вторым этапом. Все приёмы отключаемы по отдельности.

    Отключаемость не роскошь: сравнивать режимы можно только тогда, когда
    между ними отличается ровно один приём.

    `provider` и `model` добавлены в дне 26. Без них второй этап уходил
    в облако, даже когда весь остальной чат работал на локальной модели —
    и утверждение «всё локально» было бы неправдой.
    """
    import time

    index = index or Index()
    started = time.monotonic()
    usage: dict = {"total_tokens": 0}
    stages: list[dict] = []

    query, rewritten = question, False
    if rewrite:
        query, rewrite_usage = rewrite_query(question, provider=provider,
                                             model=model)
        rewritten = query != question
        usage["total_tokens"] += int(rewrite_usage.get("total_tokens") or 0)
        stages.append({"stage": "rewrite", "done": rewritten,
                       "query": query,
                       "tokens": int(rewrite_usage.get("total_tokens") or 0)})

    # Широкая выдача нужна, только если дальше есть чем сужать.
    take = wide_k if (rerank or threshold) else final_k
    found = index.search(query, strategy, k=take)
    hits = [Hit(chunk=chunk, score=score, rank=position)
            for position, (chunk, score) in enumerate(found, 1)]
    considered = list(hits)
    stages.append({"stage": "search", "got": len(hits), "k": take})

    if threshold is not None:
        before = len(hits)
        hits = filter_by_threshold(hits, threshold)
        stages.append({"stage": "threshold", "value": threshold,
                       "before": before, "after": len(hits)})

    if rerank:
        before = len(hits)
        hits, rerank_usage = rerank_with_llm(question, hits, keep=final_k,
                                             provider=provider, model=model)
        usage["total_tokens"] += int(rerank_usage.get("total_tokens") or 0)
        stages.append({"stage": "rerank", "before": before,
                       "after": len(hits),
                       "tokens": int(rerank_usage.get("total_tokens") or 0)})
    else:
        if len(hits) > final_k:
            for hit in hits[final_k:]:
                hit.dropped_by = "top_k"
        hits = hits[:final_k]
        stages.append({"stage": "top_k", "after": len(hits)})

    return Retrieval(question=question, query=query, rewritten=rewritten,
                     hits=hits, considered=considered, stages=stages,
                     usage=usage, seconds=time.monotonic() - started)


__all__ = ["retrieve", "rewrite_query", "filter_by_threshold",
           "rerank_with_llm", "Hit", "Retrieval", "RerankError",
           "WIDE_K", "FINAL_K", "THRESHOLD"]
