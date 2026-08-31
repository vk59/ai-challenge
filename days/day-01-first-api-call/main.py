#!/usr/bin/env python3
"""День 1: отправляем запрос в LLM через API и печатаем ответ в консоль.

    python main.py "Привет! Кто ты?"
    python main.py                      # спросит промпт интерактивно
"""

import sys

from llm import LLMError, ask


def run() -> int:
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        prompt = input("Ваш вопрос к LLM: ").strip()
    if not prompt:
        print("Пустой запрос — нечего отправлять.", file=sys.stderr)
        return 1

    print("→ Отправляю запрос в DeepSeek...\n", file=sys.stderr)
    try:
        answer = ask(prompt)
    except LLMError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(run())
