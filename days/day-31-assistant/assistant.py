#!/usr/bin/env python3
"""День 31: ассистент разработчика — документы плюс состояние проекта.

    python3 assistant.py                      # диалог
    python3 assistant.py /help "вопрос"       # разовый вопрос
    python3 assistant.py --local              # на локальной модели
    python3 assistant.py --check              # всё ли на месте

Собирает два источника, которые до сих пор жили порознь:

    документы   README, docs/, сам код — через поиск по индексу.
                Отвечают на «как устроено» и «почему так сделано»
    состояние   git прямо сейчас: ветка, незакоммиченное, последние
                коммиты, структура файлов — через MCP

Разделение не формальное. Документация описывает замысел, git —
то, что происходит сию минуту. На вопрос «в какой я ветке» в документах
ответа нет и быть не может; на вопрос «почему сжатие бывает дороже»
его нет в git. Ассистент должен понимать, куда идти, и это главное,
что здесь проверяется.

Команда /help отвечает на вопрос о проекте, пользуясь обоими источниками.
"""

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

os.environ.setdefault("AI_ADVENT_REPO", str(HERE.parents[1]))

from index import STRUCTURAL, Index  # noqa: E402
from llm import DEFAULT_LOCAL_MODEL, LOCAL, LLMError, ask  # noqa: E402
from tools import MCPToolset  # noqa: E402

# Два сервера: история из дня 17 и состояние отсюда. Разводим по
# префиксам, как в дне 20 — иначе одинаковые по смыслу инструменты
# путались бы между собой.
def _hist_server() -> Path:
    """Сервер истории из дня 17. Внутри бандла всё лежит плоско, и он
    скопирован туда под именем server_hist.py — иначе затёр бы наш."""
    nearby = HERE / "server_hist.py"
    return (nearby if nearby.exists()
            else HERE.parents[0] / "day-17-mcp-tool" / "server.py")


SERVERS = {
    "state": [sys.executable, str(HERE / "server.py")],
    "hist": [sys.executable, str(_hist_server())],
}

CHUNKS = 4
MAX_ROUNDS = 4

ROLE = """Ты — ассистент разработчика по проекту «AI Advent Challenge».
У тебя два источника, и выбор между ними — твоя главная работа.

ДОКУМЕНТЫ (они уже найдены и приведены ниже). Отвечают на «как устроено»,
«почему так сделано», «что показал замер». Это замысел и объяснения.

ИНСТРУМЕНТЫ git. Отвечают на «что сейчас». Документы этого не знают:

  state__current_branch  в какой ветке, что последнее закоммичено
  state__working_state   что не закоммичено прямо сейчас
  state__diff            что именно изменилось
  state__list_files      структура: какие файлы есть, где лежат
  state__recent_commits  чем занимались недавно
  hist__*                история: статистика, поиск по коммитам, файл во времени

Правила:

1. Спрашивают про текущее состояние — зови инструмент. В документах
   этого нет, и выдумывать нельзя.
2. Спрашивают, как что-то устроено — отвечай по выдержкам.
3. Вопрос требует обоих — сделай и то и другое. «Что я сейчас делаю
   и как оно устроено» — это сначала working_state, потом выдержки.
4. Если ответа нет ни там ни там — скажи прямо.
5. Отвечай кратко, по делу. Ссылайся на файл, откуда взял.

{excerpts}"""


@dataclass
class Answer:
    question: str
    text: str
    used_docs: list[dict] = field(default_factory=list)
    used_tools: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    tokens: int = 0
    rounds: int = 0

    @property
    def sources(self) -> list[str]:
        out = [d["chunk_id"] for d in self.used_docs]
        out += [f"git:{t['name']}" for t in self.used_tools]
        return out

    def as_dict(self) -> dict:
        return {"question": self.question, "text": self.text,
                "docs": self.used_docs, "tools": self.used_tools,
                "seconds": round(self.seconds, 2), "tokens": self.tokens,
                "rounds": self.rounds}


class Assistant:
    """Документы и состояние проекта в одном месте."""

    def __init__(self, *, local: bool = False, model: str | None = None,
                 chunks: int = CHUNKS):
        self.local = local
        self.model = (model or DEFAULT_LOCAL_MODEL) if local else model
        self.provider = LOCAL if local else None
        self.chunks = chunks
        self.index = Index()
        self.tools = MCPToolset(SERVERS)
        self.tools.open()
        self.history: list[dict] = []

    def close(self) -> None:
        self.tools.close()

    def __enter__(self) -> "Assistant":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def ready(self) -> list[str]:
        """Чего не хватает для работы. Пустой список — всё на месте."""
        troubles = []
        stats = self.index.stats(STRUCTURAL)
        if not stats["chunks"]:
            troubles.append("индекс пуст → python3 ../day-21-indexing/build.py")
        if not self.tools.names:
            troubles.append("MCP-серверы не поднялись")
        else:
            missing = [n for n in ("state__current_branch", "hist__repo_stats")
                       if n not in self.tools.names]
            if missing:
                troubles.append(f"нет инструментов: {', '.join(missing)}")
        return troubles

    def search(self, question: str) -> list[dict]:
        found = self.index.search(question, STRUCTURAL, k=self.chunks)
        return [{"source": c.source, "section": c.section,
                 "chunk_id": c.chunk_id, "text": c.text,
                 "score": round(s, 3)} for c, s in found]

    def help(self, question: str) -> Answer:
        """Ответ на вопрос о проекте: документы плюс инструменты."""
        question = (question or "").strip()
        if not question:
            raise LLMError("Пустой вопрос")

        started = time.monotonic()
        docs = self.search(question)
        excerpts = "\n\n".join(
            f"[{n}] {d['source']} — «{d['section']}»\n{d['text'].strip()}"
            for n, d in enumerate(docs, 1))
        system = ROLE.format(
            excerpts=f"\nВЫДЕРЖКИ ИЗ ДОКУМЕНТАЦИИ:\n\n{excerpts}"
            if docs else "\n(документы ничего похожего не вернули)")

        conversation = list(self.history)
        conversation.append({"role": "user", "content": question})
        used_tools: list[dict] = []
        tokens = 0
        text = ""

        for round_number in range(1, MAX_ROUNDS + 1):
            reply = ask(None, system=system, history=conversation,
                        tools=self.tools.schemas(), provider=self.provider,
                        model=self.model, max_tokens=900, temperature=0.2)
            tokens += int(reply.usage.get("total_tokens") or 0)

            if not reply.wants_tools:
                text = (reply.text or "").strip()
                break

            conversation.append({"role": "assistant",
                                 "content": reply.text or None,
                                 "tool_calls": reply.tool_calls})
            for call in reply.tool_calls:
                function = call.get("function") or {}
                record = self.tools.call(function.get("name", ""),
                                         function.get("arguments"))
                used_tools.append({
                    "name": record.name,
                    "args": record.arguments,
                    "preview": (record.result or "").strip().splitlines()[:3],
                    "failed": record.failed,
                    "seconds": record.seconds})
                conversation.append({"role": "tool",
                                     "tool_call_id": call.get("id", ""),
                                     "content": record.result})
        else:
            round_number = MAX_ROUNDS
            text = ("Не уложился в отведённые круги вызовов. "
                    "Задайте вопрос поуже.")

        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": text})
        del self.history[:-12]

        return Answer(question=question, text=text, used_docs=docs,
                      used_tools=used_tools,
                      seconds=time.monotonic() - started,
                      tokens=tokens, rounds=round_number)


HELP = ("команды: /help <вопрос>   /ветка   /состояние   /файлы [папка]   "
        "/очистить   /выход")


def show(answer: Answer, verbose: bool = False) -> None:
    print(f"\n{answer.text.strip()}\n")
    if answer.used_tools:
        print("  git:")
        for tool in answer.used_tools:
            args = ", ".join(f"{k}={v!r}" for k, v in (tool["args"] or {}).items())
            print(f"    {tool['name']}({args})"
                  + ("  ← ошибка" if tool["failed"] else ""))
            if verbose:
                for line in tool["preview"]:
                    print(f"      {line[:90]}")
    if answer.used_docs:
        print("  документы:")
        for doc in answer.used_docs[:3]:
            print(f"    {doc['chunk_id']}  ({doc['score']})")
    print(f"  {answer.tokens} токенов · {answer.seconds:.1f}с · "
          f"кругов {answer.rounds}")


def main() -> None:
    argv = sys.argv[1:]
    local = "--local" in argv
    verbose = "-v" in argv or "--verbose" in argv
    model = None
    if "--model" in argv:
        position = argv.index("--model")
        if position + 1 < len(argv):
            model = argv[position + 1]
            argv.pop(position + 1)

    words = [a for a in argv if not a.startswith("-")]
    if words and words[0] == "/help":
        words = words[1:]

    with Assistant(local=local, model=model) as assistant:
        troubles = assistant.ready()
        if "--check" in argv:
            where = (f"локально ({assistant.model})" if local else "облако")
            stats = assistant.index.stats(STRUCTURAL)
            print(f"Ассистент разработчика · {where}")
            print(f"  индекс: {stats['chunks']} кусков из "
                  f"{stats['documents']} документов")
            print(f"  MCP: {assistant.tools.describe()}")
            for name in assistant.tools.names:
                print(f"    {name}")
            if troubles:
                print("\nНе хватает:")
                for trouble in troubles:
                    print(f"  ✗ {trouble}")
                sys.exit(1)
            print("\nВсё на месте.")
            return

        if troubles:
            print("Не готово:")
            for trouble in troubles:
                print(f"  {trouble}")
            sys.exit(1)

        if words:
            try:
                show(assistant.help(" ".join(words)), verbose)
            except LLMError as failure:
                print(f"сбой: {failure}")
                sys.exit(1)
            return

        stats = assistant.index.stats(STRUCTURAL)
        where = f"локально ({assistant.model})" if local else "облако"
        print(f"Ассистент разработчика · {where} · "
              f"{stats['chunks']} кусков, {len(assistant.tools.names)} "
              f"инструментов")
        print(HELP)

        while True:
            try:
                line = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nпока")
                return
            if not line:
                continue
            if line in ("/выход", "/exit", "/quit"):
                print("пока")
                return
            if line == "/очистить":
                assistant.history.clear()
                print("  история очищена")
                continue
            # Короткие команды — прямые вызовы, без модели: быстрее
            # и дешевле, когда ответ и так однозначен.
            if line == "/ветка":
                print("\n" + assistant.tools.call("state__current_branch").result)
                continue
            if line == "/состояние":
                print("\n" + assistant.tools.call("state__working_state").result)
                continue
            if line.startswith("/файлы"):
                folder = line[6:].strip()
                print("\n" + assistant.tools.call(
                    "state__list_files", {"folder": folder}).result)
                continue
            if line.startswith("/help"):
                line = line[5:].strip()
                if not line:
                    print(f"  {HELP}")
                    continue
            elif line.startswith("/"):
                print(f"  {HELP}")
                continue

            print("  думаю…", end="\r", flush=True)
            try:
                answer = assistant.help(line)
            except LLMError as failure:
                print(f"  сбой: {failure}      ")
                continue
            print(" " * 20, end="\r")
            show(answer, verbose)


if __name__ == "__main__":
    main()
