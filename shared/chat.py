"""Мини-чат с RAG и памятью задачи (день 25).

Собирает вместе то, что делалось по частям: историю диалога (день 7),
состояние задачи (дни 13–15), поиск с реранкингом (дни 21–23) и ответы
с проверенными цитатами (день 24).

Главная производственная проблема здесь не в сборке, а в уточнениях.
Замер на живом диалоге: вопрос «Как устроено сжатие истории?» находит
нужный файл, а следующие за ним «Сколько именно?», «А как вы это
измеряли?», «Что с этим делать?» дают топ-1 близость 0.326, 0.385 и 0.366
— ниже порога отказа 0.39. То есть ассистент ответил бы «не знаю»
на законный вопрос по своей же теме.

Лечится переписыванием запроса с учётом истории И цели диалога. Это не
то же самое переписывание, что в дне 23: там подставлялись термины
предметной области, здесь раскрываются местоимения и восстанавливается
опущенное.

Память задачи хранит три вещи, которые нельзя восстановить из последних
реплик:

    goal         цель диалога — зачем человек пришёл
    clarified    что он уже уточнил, чтобы не спрашивать дважды
    constraints  ограничения и термины, зафиксированные по ходу

    session = ChatSession("разбор-сжатия")
    reply = session.ask("как устроено сжатие истории?")
    print(reply.answer, reply.sources, session.task.goal)
"""

import json
import re
import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from pathlib import Path

from cite import CitedAnswer, answer_with_citations, порог_отказа
from index import STRUCTURAL, Index
from llm import LLMError, ask
from memory import DEFAULT_SESSION, Turn, default_dir, open_store

# Сколько последних пар уходит в переписывание запроса. Четыре: больше
# не помогает, потому что уточнение почти всегда опирается на соседние
# реплики, а не на начало диалога — для начала есть цель в памяти задачи.
HISTORY_FOR_REWRITE = 4

# Сколько пар показываем модели при ответе. Отдельно от предыдущего:
# для ответа контекст нужен шире, чем для переписывания запроса.
HISTORY_FOR_ANSWER = 6

# Пример в этом промпте был конкретным — и маленькие модели списывали его
# дословно вместо того, чтобы применить образец. И 3B, и 7B на уточнение
# «А на сколько именно?» выдавали ровно текст примера, про совсем другую
# тему, и диалог уезжал. Облачная модель пример обобщала, локальные —
# копировали. Поэтому образец теперь схематичный, без настоящих терминов.
RESOLVE_PROMPT = """Перепиши последнюю реплику пользователя в самодостаточный
поисковый запрос по документации проекта «AI Advent Challenge».

Задача: раскрыть местоимения и восстановить опущенное, опираясь на историю
и цель диалога.

Как это работает: если говорили про ПРЕДМЕТ, а потом спросили «а почему?»,
получится «почему ПРЕДМЕТ устроен так». Подставляй предмет из истории,
а не из этого объяснения.

Если реплика и так самодостаточна — верни её почти без изменений.
Верни ТОЛЬКО запрос, одной строкой, без кавычек и пояснений.

{goal_block}История диалога:
{history}

Последняя реплика: {message}"""

TASK_PROMPT = """Ты ведёшь краткую память задачи для диалога по документации
проекта «AI Advent Challenge».

Текущая память:
{current}

Новая пара реплик:
Пользователь: {question}
Ассистент: {answer}

Обнови память и верни ТОЛЬКО JSON:

{{
  "goal": "цель диалога одной фразой — зачем человек пришёл",
  "clarified": ["что пользователь уже уточнил или сообщил о себе"],
  "constraints": ["зафиксированные ограничения, термины, договорённости"]
}}

Правила:
1. Цель меняй только если человек явно сменил тему. Уточняющий вопрос
   по той же теме цель НЕ меняет.
2. В "clarified" и "constraints" держи не больше шести пунктов в каждом,
   самые свежие и важные. Старое вытесняй.
3. Пункты короткие, по делу, без воды.
4. Ничего, кроме JSON."""

SYSTEM_PREFIX = """Ты — ассистент по документации проекта «AI Advent Challenge».
Отвечай только по выдержкам, которые тебе дают, и всегда с цитатами.

{task_block}

{dialogue_block}"""

# Диалог уезжает в системный промпт ТЕКСТОМ, а не списком сообщений, и это
# не стилистика. Ответ требуется в JSON, а в истории ассистент отвечал
# обычным текстом — модель видела противоречивый пример формата и
# возвращала одни пробелы: четыре реплики из двенадцати в длинном диалоге.
# Переложили историю в промпт как справку — пустые ответы прекратились.
DIALOGUE_BLOCK = """О чём уже говорили (это справка, а не пример формата —
отвечать всё равно строго в JSON):

{history}"""


@dataclass
class TaskMemory:
    """Память задачи: то, что не восстановить из последних реплик."""

    goal: str = ""
    clarified: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    turns: int = 0

    @property
    def is_empty(self) -> bool:
        return not (self.goal or self.clarified or self.constraints)

    def as_prompt(self) -> str:
        if self.is_empty:
            return ""
        lines = []
        if self.goal:
            lines.append(f"Цель диалога: {self.goal}")
        if self.clarified:
            lines.append("Пользователь уже уточнил:")
            lines += [f"  — {item}" for item in self.clarified]
        if self.constraints:
            lines.append("Зафиксировано:")
            lines += [f"  — {item}" for item in self.constraints]
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "TaskMemory":
        return cls(goal=str(raw.get("goal") or ""),
                   clarified=[str(x) for x in (raw.get("clarified") or [])][:6],
                   constraints=[str(x) for x in (raw.get("constraints") or [])][:6],
                   turns=int(raw.get("turns") or 0))


class TaskStore:
    """Память задачи по сессиям. Отдельная таблица, а не свалка в истории:
    историю при сжатии вытесняют, а цель диалога терять нельзя."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_dir() / "chat_tasks.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                            session TEXT PRIMARY KEY,
                            goal TEXT NOT NULL DEFAULT '',
                            clarified TEXT NOT NULL DEFAULT '[]',
                            constraints TEXT NOT NULL DEFAULT '[]',
                            turns INTEGER NOT NULL DEFAULT 0,
                            at TEXT NOT NULL DEFAULT (datetime('now')))""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def load(self, session: str) -> TaskMemory:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE session = ?",
                             (session,)).fetchone()
        if not row:
            return TaskMemory()
        return TaskMemory(goal=row["goal"],
                          clarified=json.loads(row["clarified"] or "[]"),
                          constraints=json.loads(row["constraints"] or "[]"),
                          turns=row["turns"])

    def save(self, session: str, task: TaskMemory) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO tasks (session, goal, clarified, constraints, "
                "turns, at) VALUES (?, ?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(session) DO UPDATE SET goal=excluded.goal, "
                "clarified=excluded.clarified, constraints=excluded.constraints, "
                "turns=excluded.turns, at=excluded.at",
                (session, task.goal, json.dumps(task.clarified,
                                                ensure_ascii=False),
                 json.dumps(task.constraints, ensure_ascii=False), task.turns))

    def drop(self, session: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM tasks WHERE session = ?", (session,))


@dataclass
class Reply:
    """Ответ чата со всем, что нужно показать и проверить."""

    message: str
    query: str                      # что ушло в поиск
    rewritten: bool
    answer: str
    cited: CitedAnswer
    task: TaskMemory
    task_changed: bool = False
    rewrite_tokens: int = 0
    task_tokens: int = 0
    # Токены второго этапа. Раньше они терялись: Reply считал только ответ,
    # переписывание и память задачи, а реранкинг — нет, и в окне стояла
    # одна и та же цифра независимо от того, включён он или выключен.
    stage_tokens: int = 0

    @property
    def sources(self) -> list[dict]:
        return [s.as_dict() for s in self.cited.sources]

    @property
    def quotes(self) -> list[dict]:
        return [q.as_dict() for q in self.cited.quotes]

    @property
    def has_sources(self) -> bool:
        return bool(self.cited.sources)

    @property
    def abstained(self) -> bool:
        return self.cited.abstained

    @property
    def total_tokens(self) -> int:
        return (self.cited.total_tokens + self.rewrite_tokens
                + self.task_tokens + self.stage_tokens)

    def as_dict(self) -> dict:
        return {"message": self.message, "query": self.query,
                "rewritten": self.rewritten, "answer": self.answer,
                "cited": self.cited.as_dict(), "task": self.task.as_dict(),
                "task_changed": self.task_changed,
                "rewrite_tokens": self.rewrite_tokens,
                "task_tokens": self.task_tokens,
                "stage_tokens": self.stage_tokens,
                "total_tokens": self.total_tokens}


def _history_lines(turns: list[Turn], limit: int) -> str:
    recent = turns[-limit * 2:] if limit else turns
    if not recent:
        return "(диалог только начался)"
    names = {"user": "Пользователь", "assistant": "Ассистент"}
    return "\n".join(
        f"{names.get(t.role, t.role)}: {' '.join(t.content.split())[:260]}"
        for t in recent)


def resolve_query(message: str, turns: list[Turn], task: TaskMemory,
                  *, provider=None, model: str | None = None
                  ) -> tuple[str, bool, int]:
    """Уточнение → самодостаточный поисковый запрос.

    Возвращает (запрос, переписан ли, токены). Если история пуста, в сеть
    не ходим: переписывать нечего, а запрос лишний.
    """
    if not turns:
        return message, False, 0

    goal_block = f"Цель диалога: {task.goal}\n\n" if task.goal else ""
    prompt = RESOLVE_PROMPT.format(
        goal_block=goal_block,
        history=_history_lines(turns, HISTORY_FOR_REWRITE),
        message=message)
    try:
        reply = ask(prompt, provider=provider, model=model,
                    max_tokens=140, temperature=0.0)
    except LLMError:
        # Отказ переписывания не должен ломать диалог: ищем как есть.
        return message, False, 0

    query = " ".join((reply.text or "").split()).strip().strip('"«»')
    tokens = int(reply.usage.get("total_tokens") or 0)
    if not query or len(query) > len(message) * 8 + 120:
        return message, False, tokens
    return query, query != message, tokens


def update_task(task: TaskMemory, question: str, answer: str,
                *, provider=None, model: str | None = None
                ) -> tuple[TaskMemory, bool, int]:
    """Обновить память задачи по свежей паре реплик."""
    current = json.dumps(task.as_dict(), ensure_ascii=False, indent=2)
    try:
        reply = ask(TASK_PROMPT.format(current=current, question=question,
                                       answer=" ".join(answer.split())[:700]),
                    provider=provider, model=model,
                    max_tokens=400, temperature=0.0, json_mode=True)
    except LLMError:
        return task, False, 0

    tokens = int(reply.usage.get("total_tokens") or 0)
    block = re.search(r"\{.*\}", reply.text or "", re.S)
    if not block:
        return task, False, tokens
    try:
        data = json.loads(block.group(0))
    except json.JSONDecodeError:
        return task, False, tokens

    fresh = TaskMemory.from_dict(data)
    fresh.turns = task.turns + 1
    # Пустую цель от модели игнорируем: молчание не повод забыть, зачем
    # человек пришёл. Это и есть защита от потери цели.
    if not fresh.goal:
        fresh.goal = task.goal
    changed = (fresh.goal != task.goal
               or fresh.clarified != task.clarified
               or fresh.constraints != task.constraints)
    return fresh, changed, tokens


class ChatSession:
    """Диалог: история, память задачи, поиск, ответ с цитатами."""

    def __init__(self, session: str = DEFAULT_SESSION, *,
                 store=None, index: Index | None = None,
                 tasks: TaskStore | None = None,
                 strategy: str = STRUCTURAL, chunks: int = 5,
                 rerank: bool = True,
                 abstain_below: float | None = -1.0,
                 track_task: bool = True,
                 provider=None, model: str | None = None):
        self.session = session or DEFAULT_SESSION
        self.store = store if store is not None else open_store("sqlite")
        self.index = index or Index()
        self.tasks = tasks or TaskStore()
        self.strategy = strategy
        self.chunks = chunks
        self.rerank = rerank
        self.abstain_below = (порог_отказа() if abstain_below == -1.0
                              else abstain_below)
        self.track_task = track_task
        # День 26: тот же чат целиком на локальной модели. Провайдер
        # прокидывается во все три обращения — раскрытие уточнения, ответ
        # и обновление памяти задачи, — иначе половина диалога уходила бы
        # в сеть, и «локально» было бы неправдой.
        self.provider = provider
        self.model = model
        self.task = self.tasks.load(self.session)

    # ── история ─────────────────────────────────────────────────────────
    def transcript(self) -> list[Turn]:
        return self.store.load(self.session)

    def chats(self) -> list[dict]:
        active = self.session
        rows = []
        for info in self.store.sessions():
            task = self.tasks.load(info.name)
            rows.append({"name": info.name, "title": info.title,
                         "pairs": info.pairs, "active": info.name == active,
                         "goal": task.goal})
        if not any(r["active"] for r in rows):
            rows.insert(0, {"name": active, "title": active, "pairs": 0,
                            "active": True, "goal": self.task.goal})
        return rows

    def switch(self, session: str) -> None:
        self.session = session or DEFAULT_SESSION
        self.task = self.tasks.load(self.session)

    def drop(self, session: str) -> None:
        self.store.clear(session)
        self.tasks.drop(session)
        if session == self.session:
            others = [s.name for s in self.store.sessions()]
            self.switch(others[0] if others else DEFAULT_SESSION)

    def rename(self, new_name: str) -> bool:
        new_name = (new_name or "").strip()
        if not new_name or new_name == self.session:
            return False
        if not self.store.rename(self.session, new_name):
            return False
        task = self.tasks.load(self.session)
        self.tasks.drop(self.session)
        self.tasks.save(new_name, task)
        self.session = new_name
        self.task = task
        return True

    def forget_task(self) -> None:
        self.tasks.drop(self.session)
        self.task = TaskMemory()

    def _prefix(self, turns: list[Turn]) -> str | None:
        """Память задачи и ход диалога — одним блоком в системный промпт."""
        parts = []
        if not self.task.is_empty:
            parts.append(self.task.as_prompt())
        if turns:
            parts.append(DIALOGUE_BLOCK.format(
                history=_history_lines(turns, HISTORY_FOR_ANSWER)))
        if not parts:
            return None
        return SYSTEM_PREFIX.format(task_block=parts[0],
                                    dialogue_block=parts[1] if len(parts) > 1
                                    else "").strip()

    # ── главное ─────────────────────────────────────────────────────────
    def ask(self, message: str) -> Reply:
        """Реплика → поиск с учётом контекста → ответ с источниками."""
        message = (message or "").strip()
        if not message:
            raise LLMError("Пустая реплика")

        turns = self.transcript()
        query, rewritten, rewrite_tokens = resolve_query(
            message, turns, self.task, provider=self.provider,
            model=self.model)

        retrieval = None
        if self.rerank:
            # Настройки дня 23 прописаны здесь, а не импортированы из папки
            # дня: shared не должен зависеть от days.
            from rerank import FINAL_K, THRESHOLD, WIDE_K, retrieve

            retrieval = retrieve(query, index=self.index, wide_k=WIDE_K,
                                 final_k=max(FINAL_K, self.chunks),
                                 threshold=THRESHOLD, rerank=True,
                                 provider=self.provider, model=self.model)

        cited = answer_with_citations(
            query, index=self.index, strategy=self.strategy, k=self.chunks,
            abstain_below=self.abstain_below, retrieval=retrieval,
            system_prefix=self._prefix(turns),
            # Источники обязаны быть в каждом уверенном ответе.
            require_citations=True,
            provider=self.provider, model=self.model)

        task, changed, task_tokens = (self.task, False, 0)
        if self.track_task and not cited.abstained:
            task, changed, task_tokens = update_task(
                self.task, message, cited.answer, provider=self.provider,
                model=self.model)
            self.task = task
            self.tasks.save(self.session, task)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.store.append(self.session, Turn("user", message, now,
                                             tokens=cited.prompt_tokens))
        self.store.append(self.session, Turn("assistant", cited.answer, now,
                                             tokens=cited.answer_tokens,
                                             seconds=cited.seconds))

        return Reply(message=message, query=query, rewritten=rewritten,
                     answer=cited.answer, cited=cited, task=task,
                     task_changed=changed, rewrite_tokens=rewrite_tokens,
                     task_tokens=task_tokens,
                     stage_tokens=int((retrieval.usage.get("total_tokens") or 0)
                                      if retrieval else 0))


__all__ = ["ChatSession", "TaskMemory", "TaskStore", "Reply",
           "resolve_query", "update_task", "HISTORY_FOR_REWRITE",
           "HISTORY_FOR_ANSWER"]
