#!/usr/bin/env python3
"""День 1: отправляем запрос в LLM через API и печатаем ответ в консоль.

    python3 main.py "Привет! Кто ты?"
    python3 main.py                      # спросит промпт интерактивно

Текст печатается по мере генерации — тот же стрим, что и в веб-версии.
"""

import os
import sys

from llm import DEFAULT_MODEL, LLMError, ask_stream, load_env

# Цвета включаем только если вывод идёт в терминал: при перенаправлении
# в файл управляющие последовательности только мешают.
COLOR = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def run() -> int:
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        try:
            prompt = input(paint("2", "Вопрос к модели: ")).strip()
        except (EOFError, KeyboardInterrupt):
            return 1
    if not prompt:
        print("Пустой запрос — нечего отправлять.", file=sys.stderr)
        return 1

    load_env()
    model = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
    print(paint("2", f"\n  {model} · думаю…\n"), file=sys.stderr)

    stats: dict = {}
    try:
        for delta in ask_stream(prompt, stats=stats):
            print(delta, end="", flush=True)
    except LLMError as exc:
        print(paint("31", f"\nОшибка: {exc}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(paint("2", "\n\n  прервано"), file=sys.stderr)
        return 130

    print(paint("2", f"\n\n  {summarize(stats)}"), file=sys.stderr)
    return 0


def summarize(stats: dict) -> str:
    bits = []
    if stats.get("seconds") is not None:
        bits.append(f"{stats['seconds']} с")
    if stats.get("usage", {}).get("total_tokens"):
        bits.append(f"{stats['usage']['total_tokens']} токенов")
    return " · ".join(bits)


if __name__ == "__main__":
    sys.exit(run())
