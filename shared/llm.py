"""Клиент к DeepSeek API поверх стандартной библиотеки.

Вырос из days/day-01-first-api-call/llm.py: там задача была показать голый запрос,
здесь добавились рычаги управления ответом, которые нужны со дня 2 —
формат, длина и условие остановки.

День 1 сознательно оставлен со своей копией: это уже сданный самодостаточный
артефакт, и его код показан на видео как есть.
"""

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
REQUEST_TIMEOUT = 120  # deepseek-reasoner думает долго
ENV_FILE_VAR = "AI_ADVENT_ENV_FILE"


class LLMError(Exception):
    """Понятная человеку ошибка обращения к API."""


@dataclass
class Answer:
    """Ответ модели вместе со всем, что о нём рассказал API."""

    text: str
    finish_reason: str          # stop | length | content_filter — почему генерация кончилась
    model: str
    seconds: float
    usage: dict = field(default_factory=dict)

    @property
    def completion_tokens(self) -> int:
        return self.usage.get("completion_tokens", 0)

    @property
    def words(self) -> int:
        return len(self.text.split())


def load_env(path: Path | None = None) -> None:
    """Простейший .env-загрузчик: KEY=VALUE, строки с # игнорируются.

    Отдельная библиотека (python-dotenv) ради двадцати строк тут не нужна.
    Уже существующие переменные окружения не перетираем — они приоритетнее файла.
    """
    env_file = path or Path(
        os.environ.get(ENV_FILE_VAR) or Path(__file__).resolve().parents[1] / ".env"
    )

    try:
        text = env_file.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return
    except PermissionError as exc:
        raise LLMError(f"Нет доступа к файлу {env_file}") from exc

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
    max_tokens: int | None = None,
    stop: list[str] | None = None,
    json_mode: bool = False,
) -> Answer:
    """Один запрос к модели с полным набором рычагов управления ответом.

    max_tokens — жёсткий потолок длины: API оборвёт генерацию на этом месте
                 и вернёт finish_reason="length".
    stop       — стоп-последовательности: как только модель их напишет,
                 генерация прекращается, а сама последовательность в ответ не попадает.
    json_mode  — режим строгого JSON на стороне API (response_format).
    """
    request, model_name = _build_request(
        prompt, system, model, history,
        stream=False, max_tokens=max_tokens, stop=stop, json_mode=json_mode,
    )
    started = time.monotonic()

    with _open(request) as response:
        body = json.loads(response.read().decode("utf-8"))

    try:
        choice = body["choices"][0]
        return Answer(
            text=choice["message"]["content"],
            finish_reason=choice.get("finish_reason", "?"),
            model=body.get("model", model_name),
            seconds=round(time.monotonic() - started, 1),
            usage=body.get("usage", {}),
        )
    except (KeyError, IndexError) as exc:
        raise LLMError(f"Неожиданный формат ответа: {body}") from exc


def ask_stream(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    history: list[dict] | None = None,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
    json_mode: bool = False,
    stats: dict | None = None,
) -> Iterator[str]:
    """То же самое, но с stream=True — текст отдаётся кусками по мере генерации."""
    request, model_name = _build_request(
        prompt, system, model, history,
        stream=True, max_tokens=max_tokens, stop=stop, json_mode=json_mode,
    )
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

            if chunk.get("usage") and stats is not None:
                stats["usage"] = chunk["usage"]

            for choice in chunk.get("choices", []):
                if choice.get("finish_reason") and stats is not None:
                    stats["finish_reason"] = choice["finish_reason"]
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
    max_tokens: int | None = None,
    stop: list[str] | None = None,
    json_mode: bool = False,
) -> tuple[urllib.request.Request, str]:
    """Собирает POST-запрос к /chat/completions."""
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
    messages.extend(history or [])
    messages.append({"role": "user", "content": prompt})

    body: dict = {"model": model, "messages": messages, "stream": stream}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if stop:
        body["stop"] = stop
    if json_mode:
        body["response_format"] = {"type": "json_object"}
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
