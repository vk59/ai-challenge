"""Эмбеддинги через OpenRouter и кэш к ним (день 21).

Зависимостей по-прежнему нет: HTTP через urllib, векторы лежат в SQLite
как BLOB из float32, похожесть считается вручную.

Кэш не украшение. Корпус индексируется двумя стратегиями, у стратегий
много общих кусков, да и перестраивать индекс приходится десятки раз на
отладке. Без кэша каждый прогон — новые деньги и новая минута ожидания;
с кэшем повторная сборка идёт на диске и бесплатно.

    векторы = embed(["текст один", "текст два"])
    print(similarity(векторы[0], векторы[1]))
"""

import array
import hashlib
import json
import math
import os
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from llm import LLMError, load_env

МОДЕЛЬ = "openai/text-embedding-3-small"
РАЗМЕРНОСТЬ = 1536
АДРЕС = "https://openrouter.ai/api/v1/embeddings"

# ── день 27: те же векторы, но на этой машине ────────────────────────
# Пока эмбеддинги считает OpenRouter, разговор с «локальной» моделью
# всё равно ходит в сеть: на каждый вопрос уходит один запрос за вектором.
# Ollama умеет считать их сам, и тогда офлайн становится настоящим.
# Взята bge-m3, а не nomic-embed-text, и это решение по замеру, а не
# по вкусу. На паре «нужный кусок против чужого вопроса» nomic даёт
# разрыв 0.107 (0.831 против 0.723) — он всему ставит высокую похожесть,
# и порог отказа провести негде. bge-m3 даёт 0.278 (0.491 против 0.213),
# и шкала похожа на облачную. Префиксы search_query/search_document,
# которых требует nomic, положения не исправили: разрыв стал 0.100.
LOCAL_МОДЕЛЬ = os.environ.get("AI_ADVENT_LOCAL_EMBED", "").strip() or "bge-m3"
LOCAL_РАЗМЕРНОСТЬ = 1024
LOCAL_АДРЕС = "http://127.0.0.1:11434/api/embed"

# Что использовать по умолчанию. Переключается переменной окружения,
# потому что индексы несовместимы: 1536 измерений против 768, и
# сравнивать векторы разных моделей бессмысленно.
BACKEND_VAR = "AI_ADVENT_EMBEDDINGS"


def бэкенд() -> str:
    """local | cloud. Выбор влияет и на модель, и на файл индекса."""
    выбор = os.environ.get(BACKEND_VAR, "").strip().lower()
    return "local" if выбор in ("local", "ollama", "offline") else "cloud"


def модель_бэкенда(бэк: str | None = None) -> str:
    return LOCAL_МОДЕЛЬ if (бэк or бэкенд()) == "local" else МОДЕЛЬ


def размерность_бэкенда(бэк: str | None = None) -> int:
    return (LOCAL_РАЗМЕРНОСТЬ if (бэк or бэкенд()) == "local"
            else РАЗМЕРНОСТЬ)

# Порог подобран опытом: по 64 куска запрос идёт около секунды, а тело
# остаётся в разумных пределах. Больше — растёт риск словить таймаут
# на всей пачке и потерять уже посчитанное.
ПАЧКА = 64

# Каталог тот же, что у истории диалогов: внутри собранного .app писать
# рядом с кодом нельзя — кэш терялся бы при каждой пересборке. Поэтому
# уважаем AI_ADVENT_MEMORY_DIR, который выставляет build_app.sh.
from memory import default_dir  # noqa: E402

КЭШ = default_dir() / "embeddings.db"


class EmbeddingError(LLMError):
    """Эмбеддинги не посчитались."""


@dataclass
class Расход:
    """Сколько потрачено на эмбеддинги за сеанс."""

    запросов: int = 0
    кусков: int = 0
    из_кэша: int = 0
    токенов: int = 0
    деньги: float = 0.0
    секунды: float = 0.0

    @property
    def деньги_строкой(self) -> str:
        if self.деньги <= 0:
            return "$0"
        if self.деньги < 0.01:
            return f"${self.деньги:.6f}".rstrip("0")
        return f"${self.деньги:.4f}"

    def как_словарь(self) -> dict:
        return {"requests": self.запросов, "chunks": self.кусков,
                "cached": self.из_кэша, "tokens": self.токенов,
                "money": self.деньги, "money_pretty": self.деньги_строкой,
                "seconds": round(self.секунды, 2)}


РАСХОД = Расход()


def _ключ(текст: str, модель: str) -> str:
    """Отпечаток текста вместе с моделью: смена модели обнуляет кэш сама."""
    return hashlib.sha256(f"{модель}\n{текст}".encode("utf-8")).hexdigest()


def _соединение() -> sqlite3.Connection:
    КЭШ.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(КЭШ, timeout=30)
    db.execute("""CREATE TABLE IF NOT EXISTS vectors (
                    key    TEXT PRIMARY KEY,
                    model  TEXT NOT NULL,
                    dim    INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    at     TEXT NOT NULL DEFAULT (datetime('now')))""")
    return db


def в_байты(вектор: list[float]) -> bytes:
    return array.array("f", вектор).tobytes()


def из_байтов(сырое: bytes) -> list[float]:
    в = array.array("f")
    в.frombytes(сырое)
    return list(в)


def similarity(а: list[float], б: list[float]) -> float:
    """Косинусная близость. Векторы OpenAI уже нормированы, но делим всё
    равно: на срезанных или склеенных руками векторах норма уже не единица,
    и молчаливая ошибка тут обошлась бы дороже одного деления."""
    if not а or not б or len(а) != len(б):
        return 0.0
    скаляр = sum(x * y for x, y in zip(а, б))
    норма = math.sqrt(sum(x * x for x in а)) * math.sqrt(sum(y * y for y in б))
    return скаляр / норма if норма else 0.0


def _запрос_локально(куски: list[str], модель: str
                     ) -> tuple[list[list[float]], dict]:
    """Векторы через Ollama. Формат ответа свой, не OpenAI-совместимый:
    список лежит в "embeddings", а не в "data"."""
    адрес = os.environ.get("OLLAMA_HOST", "").strip()
    if адрес:
        if not адрес.startswith("http"):
            адрес = "http://" + адрес
        адрес = адрес.rstrip("/") + "/api/embed"
    else:
        адрес = LOCAL_АДРЕС

    тело = json.dumps({"model": модель, "input": куски}).encode("utf-8")
    запрос = urllib.request.Request(адрес, тело,
                                    {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(запрос, timeout=180) as ответ:
            данные = json.load(ответ)
    except urllib.error.HTTPError as сбой:
        подробности = сбой.read()[:300].decode("utf-8", "replace")
        raise EmbeddingError(
            f"Ollama ответил {сбой.code}: {подробности}\n"
            f"Модель не скачана? ollama pull {модель}") from сбой
    except urllib.error.URLError as сбой:
        raise EmbeddingError(
            f"Ollama не отвечает на {адрес}: {сбой.reason}\n"
            f"Запустите: ollama serve") from сбой

    векторы = данные.get("embeddings") or []
    if len(векторы) != len(куски):
        raise EmbeddingError(
            f"Просили {len(куски)} векторов, вернулось {len(векторы)}")
    # Ollama не считает деньги, но токены сообщает — считаем их для
    # единообразия, чтобы расход показывался той же строкой.
    расход = {"prompt_tokens": int(данные.get("prompt_eval_count") or 0),
              "total_tokens": int(данные.get("prompt_eval_count") or 0),
              "cost": 0.0}
    return векторы, расход


def _запрос(куски: list[str], модель: str) -> tuple[list[list[float]], dict]:
    if модель == LOCAL_МОДЕЛЬ:
        return _запрос_локально(куски, модель)
    load_env()
    ключ_api = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not ключ_api:
        raise EmbeddingError(
            "Нет OPENROUTER_API_KEY. Эмбеддинги берём у OpenRouter: "
            "у DeepSeek эндпоинта /embeddings нет (отвечает 404).")

    тело = json.dumps({"model": модель, "input": куски}).encode("utf-8")
    запрос = urllib.request.Request(АДРЕС, тело, {
        "Authorization": f"Bearer {ключ_api}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/vk59/ai-challenge",
        "X-Title": "AI Advent indexing",
    })
    try:
        with urllib.request.urlopen(запрос, timeout=120) as ответ:
            данные = json.load(ответ)
    except urllib.error.HTTPError as сбой:
        подробности = сбой.read()[:300].decode("utf-8", "replace")
        raise EmbeddingError(f"OpenRouter ответил {сбой.code}: {подробности}") from сбой
    except urllib.error.URLError as сбой:
        raise EmbeddingError(f"Сеть недоступна: {сбой.reason}") from сбой

    записи = данные.get("data") or []
    if len(записи) != len(куски):
        raise EmbeddingError(
            f"Просили {len(куски)} векторов, вернулось {len(записи)}")
    # Порядок в ответе гарантирован полем index, а не позицией в списке.
    записи.sort(key=lambda з: з.get("index", 0))
    return [з["embedding"] for з in записи], данные.get("usage") or {}


def embed(тексты: list[str], *, модель: str | None = None,
          кэш: bool = True, на_пачку=None) -> list[list[float]]:
    """Векторы для списка текстов. Порядок ответа совпадает с порядком входа.

    Пустые строки до сети не доходят: эндпоинт на них ругается, а смысла
    в векторе пустоты нет. Возвращаем для них нули — так вызывающему коду
    не нужно просеивать список и потом сшивать результат обратно.
    """
    if not тексты:
        return []
    модель = модель or модель_бэкенда()
    пусто = [0.0] * (LOCAL_РАЗМЕРНОСТЬ if модель == LOCAL_МОДЕЛЬ
                     else РАЗМЕРНОСТЬ)

    готово: dict[int, list[float]] = {}
    нужно: list[tuple[int, str, str]] = []     # позиция, текст, ключ

    db = _соединение() if кэш else None
    try:
        for позиция, текст in enumerate(тексты):
            if not (текст or "").strip():
                готово[позиция] = list(пусто)
                continue
            к = _ключ(текст, модель)
            если_есть = None
            if db is not None:
                строка = db.execute(
                    "SELECT vector FROM vectors WHERE key = ?", (к,)).fetchone()
                если_есть = строка[0] if строка else None
            if если_есть is not None:
                готово[позиция] = из_байтов(если_есть)
                РАСХОД.из_кэша += 1
            else:
                нужно.append((позиция, текст, к))

        for начало in range(0, len(нужно), ПАЧКА):
            пачка = нужно[начало:начало + ПАЧКА]
            если_надо = time.monotonic()
            векторы, расход = _запрос([т for _, т, _ in пачка], модель)
            прошло = time.monotonic() - если_надо

            РАСХОД.запросов += 1
            РАСХОД.кусков += len(пачка)
            РАСХОД.токенов += int(расход.get("prompt_tokens")
                                  or расход.get("total_tokens") or 0)
            РАСХОД.деньги += float(расход.get("cost") or 0.0)
            РАСХОД.секунды += прошло

            for (позиция, _, к), вектор in zip(пачка, векторы):
                готово[позиция] = вектор
                if db is not None:
                    db.execute("INSERT OR REPLACE INTO vectors "
                               "(key, model, dim, vector) VALUES (?, ?, ?, ?)",
                               (к, модель, len(вектор), в_байты(вектор)))
            if db is not None:
                db.commit()
            if на_пачку:
                на_пачку(min(начало + ПАЧКА, len(нужно)), len(нужно))
    finally:
        if db is not None:
            db.close()

    return [готово[п] for п in range(len(тексты))]


def embed_one(текст: str, **прочее) -> list[float]:
    return embed([текст], **прочее)[0]


def сколько_в_кэше() -> int:
    if not КЭШ.exists():
        return 0
    db = _соединение()
    try:
        return db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    finally:
        db.close()


__all__ = ["embed", "embed_one", "similarity", "EmbeddingError", "РАСХОД",
           "МОДЕЛЬ", "РАЗМЕРНОСТЬ", "сколько_в_кэше", "в_байты", "из_байтов"]
