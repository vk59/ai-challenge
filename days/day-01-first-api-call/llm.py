"""Минимальный клиент к DeepSeek API поверх стандартной библиотеки.

Никаких зависимостей: HTTP-запрос собирается руками через urllib, чтобы было видно,
из чего вообще состоит обращение к LLM — URL, заголовок Authorization и JSON-тело.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
REQUEST_TIMEOUT = 120  # deepseek-reasoner думает долго


class LLMError(Exception):
    """Понятная человеку ошибка обращения к API."""


def load_env(path: Path | None = None) -> None:
    """Простейший .env-загрузчик: KEY=VALUE, строки с # игнорируются.

    Отдельная библиотека (python-dotenv) ради двадцати строк тут не нужна.
    Уже существующие переменные окружения не перетираем — они приоритетнее файла.
    """
    env_file = path or Path(__file__).resolve().parents[2] / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def ask(prompt: str, *, system: str | None = None, model: str | None = None) -> str:
    """Отправляет prompt в LLM и возвращает текст ответа."""
    load_env()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise LLMError(
            "Не найден DEEPSEEK_API_KEY.\n"
            "Впиши ключ в файл .env в корне репозитория "
            "(взять тут: https://platform.deepseek.com/api_keys)"
        )

    base_url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps(
        {"model": model, "messages": messages, "stream": False},
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise LLMError(_explain_http_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Сеть недоступна или API не отвечает: {exc.reason}") from exc

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"Неожиданный формат ответа: {body}") from exc


def _explain_http_error(exc: urllib.error.HTTPError) -> str:
    """Переводит коды DeepSeek в человеческие подсказки."""
    try:
        detail = json.loads(exc.read().decode("utf-8"))["error"]["message"]
    except Exception:
        detail = exc.reason

    hints = {
        401: "неверный или отозванный API-ключ",
        402: "закончились деньги на балансе DeepSeek",
        422: "некорректные параметры запроса",
        429: "превышен лимит запросов, подожди немного",
        500: "ошибка на стороне DeepSeek, повтори запрос",
        503: "сервер перегружен, повтори запрос",
    }
    hint = hints.get(exc.code)
    return f"HTTP {exc.code}: {detail}" + (f" ({hint})" if hint else "")
