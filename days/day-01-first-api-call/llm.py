"""Минимальный клиент к DeepSeek API поверх стандартной библиотеки.

Никаких зависимостей: HTTP-запрос собирается руками через urllib, чтобы было видно,
из чего вообще состоит обращение к LLM — URL, заголовок Authorization и JSON-тело.

Два режима:
    ask()        — дождаться ответа целиком (проще некуда)
    ask_stream() — получать текст кусками по мере генерации (stream=True)
"""

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
REQUEST_TIMEOUT = 120  # deepseek-reasoner думает долго
ENV_FILE_VAR = "AI_ADVENT_ENV_FILE"


class LLMError(Exception):
    """Понятная человеку ошибка обращения к API."""


def load_env(path: Path | None = None) -> None:
    """Простейший .env-загрузчик: KEY=VALUE, строки с # игнорируются.

    Отдельная библиотека (python-dotenv) ради двадцати строк тут не нужна.
    Уже существующие переменные окружения не перетираем — они приоритетнее файла.

    Собранное приложение лежит отдельно от репозитория, поэтому путь к .env
    ему передаётся через переменную AI_ADVENT_ENV_FILE.
    """
    env_file = path or Path(
        os.environ.get(ENV_FILE_VAR) or Path(__file__).resolve().parents[2] / ".env"
    )

    try:
        text = env_file.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return
    except PermissionError as exc:
        raise LLMError(
            f"macOS не пускает приложение к файлу {env_file}.\n"
            "Разрешите доступ в Системных настройках → Конфиденциальность и "
            "безопасность → Файлы и папки (или Доступ к диску) для «День 1»."
        ) from exc

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def ask(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    history: list[dict] | None = None,
) -> str:
    """Отправляет prompt в LLM и возвращает текст ответа целиком."""
    request, _ = _build_request(prompt, system, model, history, stream=False)

    with _open(request) as response:
        body = json.loads(response.read().decode("utf-8"))

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"Неожиданный формат ответа: {body}") from exc


def ask_stream(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    history: list[dict] | None = None,
    stats: dict | None = None,
) -> Iterator[str]:
    """Тот же запрос, но с stream=True — отдаёт куски текста по мере генерации.

    API отвечает потоком SSE: строки вида `data: {...}` и финальное `data: [DONE]`.
    Если передан словарь stats, в него складываются модель, время и токены —
    интерфейсу есть что показать в подвале.
    """
    request, model_name = _build_request(prompt, system, model, history, stream=True)
    started = time.monotonic()

    with _open(request) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue

            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break

            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            # Самый последний чанк приходит без текста, зато со статистикой по токенам.
            if chunk.get("usage") and stats is not None:
                stats["usage"] = chunk["usage"]

            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {}).get("content")
                if delta:
                    yield delta

    if stats is not None:
        stats["model"] = model_name
        stats["seconds"] = round(time.monotonic() - started, 1)


def _build_request(
    prompt: str,
    system: str | None,
    model: str | None,
    history: list[dict] | None,
    *,
    stream: bool,
) -> tuple[urllib.request.Request, str]:
    """Собирает POST-запрос к /chat/completions. Здесь вся суть обращения к LLM."""
    load_env()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        # Собранное приложение читает не сам .env, а его копию рядом с рантаймом,
        # поэтому и совет там другой: одного сохранения файла не хватит.
        bundled_env = os.environ.get(ENV_FILE_VAR)
        if bundled_env:
            raise LLMError(
                "Не найден DEEPSEEK_API_KEY.\n"
                f"Приложение читает ключ из копии: {bundled_env}\n"
                "Впишите ключ в .env репозитория и пересоберите: ./build_app.sh"
            )
        raise LLMError(
            "Не найден DEEPSEEK_API_KEY.\n"
            "Впиши ключ в файл .env в корне репозитория "
            "(взять тут: https://platform.deepseek.com/api_keys)"
        )

    base_url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)

    # Модель не помнит ничего сама — вся «память» диалога это просто список сообщений,
    # который мы каждый раз отправляем заново.
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend(history or [])
    messages.append({"role": "user", "content": prompt})

    body: dict = {"model": model, "messages": messages, "stream": stream}
    if stream:
        body["stream_options"] = {"include_usage": True}

    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    return request, model


def _open(request: urllib.request.Request):
    """urlopen с человеческими сообщениями вместо трейсбеков."""
    try:
        return urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT)
    except urllib.error.HTTPError as exc:
        raise LLMError(_explain_http_error(exc)) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Сеть недоступна или API не отвечает: {exc.reason}") from exc


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
