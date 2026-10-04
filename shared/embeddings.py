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


def _запрос(куски: list[str], модель: str) -> tuple[list[list[float]], dict]:
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


def embed(тексты: list[str], *, модель: str = МОДЕЛЬ,
          кэш: bool = True, на_пачку=None) -> list[list[float]]:
    """Векторы для списка текстов. Порядок ответа совпадает с порядком входа.

    Пустые строки до сети не доходят: эндпоинт на них ругается, а смысла
    в векторе пустоты нет. Возвращаем для них нули — так вызывающему коду
    не нужно просеивать список и потом сшивать результат обратно.
    """
    if not тексты:
        return []

    готово: dict[int, list[float]] = {}
    нужно: list[tuple[int, str, str]] = []     # позиция, текст, ключ

    db = _соединение() if кэш else None
    try:
        for позиция, текст in enumerate(тексты):
            if not (текст or "").strip():
                готово[позиция] = [0.0] * РАЗМЕРНОСТЬ
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
