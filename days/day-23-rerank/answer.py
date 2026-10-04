#!/usr/bin/env python3
"""День 23: ответ с управляемым вторым этапом.

    python3 answer.py "почему приложение из Finder не видит репозиторий"
    python3 answer.py --mode full "то же самое с полным конвейером"
    python3 answer.py --all "все четыре режима подряд"
    python3 answer.py --show "плюс что попало в промпт и что отсеклось"

Промпт и правила ссылок взяты из дня 22 без изменений: меняется только то,
КАКИЕ куски в этот промпт попадают. Иначе нельзя было бы утверждать, что
разница от второго этапа, а не от новой формулировки задания модели.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from index import Index  # noqa: E402
from llm import LLMError, ask  # noqa: E402
from modes import MODES, ORDER, options_for  # noqa: E402
from rag import С_RAG as RAG_SYSTEM  # noqa: E402
from rag import ОТКАЗ as REFUSAL_RE  # noqa: E402
from rag import собрать_выдержки as build_excerpts  # noqa: E402
from rerank import Retrieval, retrieve  # noqa: E402

CITATION_RE = re.compile(r"\[(\d{1,2})\]")


@dataclass
class Answer:
    """Ответ вместе с тем, как добывался контекст."""

    text: str
    mode: str
    retrieval: Retrieval
    sources: list[str] = field(default_factory=list)
    cited: list[int] = field(default_factory=list)
    invented: list[int] = field(default_factory=list)
    refused: bool = False
    answer_tokens: int = 0
    seconds: float = 0.0

    @property
    def stage_tokens(self) -> int:
        """Токены, съеденные вторым этапом: переписом и реранкингом."""
        return int(self.retrieval.usage.get("total_tokens") or 0)

    @property
    def total_tokens(self) -> int:
        return self.answer_tokens + self.stage_tokens

    def as_dict(self, *, full: bool = False) -> dict:
        return {"text": self.text, "mode": self.mode,
                "sources": self.sources, "cited": self.cited,
                "invented": self.invented, "refused": self.refused,
                "answer_tokens": self.answer_tokens,
                "stage_tokens": self.stage_tokens,
                "total_tokens": self.total_tokens,
                "seconds": round(self.seconds, 2),
                "retrieval": self.retrieval.as_dict(full=full)}


def answer_question(question: str, *, mode: str = "full",
                    index: Index | None = None, max_tokens: int = 600,
                    temperature: float = 0.2) -> Answer:
    """Вопрос → второй этап → ответ со ссылками."""
    import time

    question = (question or "").strip()
    if not question:
        raise LLMError("Пустой вопрос")

    result = retrieve(question, index=index or Index(), **options_for(mode))
    chunks = [hit.chunk for hit in result.hits]
    excerpts = build_excerpts([
        {"source": c.source, "section": c.section, "text": c.text}
        for c in chunks])

    started = time.monotonic()
    reply = ask(question, system=RAG_SYSTEM.format(выдержки=excerpts),
                max_tokens=max_tokens, temperature=temperature)
    spent = time.monotonic() - started

    named = sorted({int(n) for n in CITATION_RE.findall(reply.text or "")})
    real = [n for n in named if 1 <= n <= len(chunks)]
    invented = [n for n in named if not 1 <= n <= len(chunks)]

    return Answer(
        text=reply.text, mode=mode, retrieval=result,
        sources=sorted({chunks[n - 1].source for n in real}),
        cited=real, invented=invented,
        refused=bool(REFUSAL_RE.search(reply.text or "")),
        answer_tokens=int(reply.usage.get("total_tokens") or 0),
        seconds=spent)


def show(result: Answer, with_context: bool) -> None:
    info = MODES[result.mode]
    print(f"\n{'═' * 74}")
    print(f"{result.mode} — {info['title']}")
    print(f"  {info['caption']}")
    print(f"{'─' * 74}")
    print((result.text or "(пусто)").strip())
    print(f"{'─' * 74}")

    stages = " · ".join(
        f"{s['stage']}"
        + (f" {s.get('before', s.get('got', ''))}→{s['after']}"
           if "after" in s else "")
        for s in result.retrieval.stages)
    print(f"  этапы: {stages}")
    if result.retrieval.rewritten:
        print(f"  запрос: {result.retrieval.query}")
    print(f"  ссылки: {result.cited or 'ни одной'}"
          + (f"  ⚠ выдуманные: {result.invented}" if result.invented else ""))
    for source in result.sources:
        print(f"    {source}")
    if result.refused:
        print("  модель сказала, что в документах этого нет")
    print(f"  токенов: второй этап {result.stage_tokens}, "
          f"ответ {result.answer_tokens}, всего {result.total_tokens}")

    if not with_context:
        return
    print(f"\n  попало в промпт ({result.retrieval.final_count}):")
    for number, hit in enumerate(result.retrieval.hits, 1):
        mark = "→" if number in result.cited else " "
        score = (f"реранк {hit.rerank_score:>4}" if hit.rerank_score is not None
                 else f"близость {hit.score:.3f}")
        print(f"  {mark} [{number}] место в поиске {hit.rank:>2}  {score}  "
              f"{hit.chunk.source}")
        print(f"        «{hit.chunk.section}»")
    dropped = [h for h in result.retrieval.considered if h.dropped_by]
    if dropped:
        print(f"\n  отсеклось ({len(dropped)}):")
        for hit in dropped[:8]:
            print(f"    {hit.dropped_by:9} близость {hit.score:.3f}  "
                  f"{hit.chunk.source} «{hit.chunk.section[:34]}»")
        if len(dropped) > 8:
            print(f"    … и ещё {len(dropped) - 8}")


def main() -> None:
    argv = sys.argv[1:]
    modes, with_context, words = ["full"], False, []

    skip = False
    for position, token in enumerate(argv):
        if skip:
            skip = False
            continue
        if token == "--mode" and position + 1 < len(argv):
            if argv[position + 1] in MODES:
                modes = [argv[position + 1]]
            skip = True
        elif token == "--all":
            modes = list(ORDER)
        elif token == "--show":
            with_context = True
        else:
            words.append(token)

    question = " ".join(words).strip()
    if not question:
        print(__doc__)
        sys.exit(1)

    print(f"Вопрос: {question}")
    index = Index()
    for mode in modes:
        try:
            show(answer_question(question, mode=mode, index=index), with_context)
        except LLMError as failure:
            print(f"\n{mode}: сбой — {failure}")


if __name__ == "__main__":
    main()
