#!/usr/bin/env python3
"""День 31: проверка, что ассистент берёт сведения из нужного источника.

    python3 check.py              # все вопросы
    python3 check.py 3 7          # выбранные
    python3 check.py -v           # плюс ответы
    python3 check.py --local      # на локальной модели

Вопросы трёх видов, и вид определяет, где ответ лежит:

    state   только git: ветка, незакоммиченное, структура файлов.
            В документах этого нет и быть не может
    docs    только документы: как устроено, почему так сделано
    both    нужны оба: сначала посмотреть состояние, потом объяснить

Проверяется не «ответил ли», а откуда взял. Ассистент, который на вопрос
о текущей ветке пересказывает README, бесполезен, даже если текст
выглядит складно.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from assistant import Assistant  # noqa: E402
from llm import LLMError  # noqa: E402

# (вопрос, вид, что должно быть в ответе, какие инструменты уместны)
QUESTIONS: list[dict] = [
    {"q": "В какой я сейчас ветке?", "kind": "state",
     "expect": [["main"]], "tools": ["state__current_branch"]},

    {"q": "Что у меня не закоммичено прямо сейчас?", "kind": "state",
     "expect": [], "tools": ["state__working_state"]},

    {"q": "Сколько файлов в папке shared и как они называются?",
     "kind": "state", "expect": [["llm.py"], ["memory.py"]],
     "tools": ["state__list_files"]},

    {"q": "Чем я занимался в последние три коммита?", "kind": "state",
     "expect": [], "tools": ["state__recent_commits", "hist__commits_by_day"]},

    {"q": "Почему сжатие истории иногда выходит дороже, чем экономит?",
     "kind": "docs", "expect": [["окупа", "дорож", "22"]], "tools": []},

    {"q": "Какая стратегия нарезки документов победила и с каким MRR?",
     "kind": "docs", "expect": [["structural"], ["0.774"]], "tools": []},

    {"q": "Что делает build_app.sh, чтобы приложение читало историю "
          "репозитория?", "kind": "docs",
     "expect": [["зеркал", "mirror"]], "tools": []},

    {"q": "Покажи структуру проекта и объясни, что лежит в shared",
     "kind": "both", "expect": [["shared"]],
     "tools": ["state__list_files"]},

    {"q": "В какой ветке я работаю и что в этом проекте вообще делается?",
     "kind": "both", "expect": [["main"]],
     "tools": ["state__current_branch"]},
]


def mentions(text: str, expect: list[list[str]]) -> bool:
    low = (text or "").lower()
    return all(any(v.lower() in low for v in variants) for variants in expect)


def main() -> None:
    argv = sys.argv[1:]
    verbose = "-v" in argv or "--verbose" in argv
    local = "--local" in argv
    numbers = [int(a) for a in argv if a.isdigit()]
    items = [(n, q) for n, q in enumerate(QUESTIONS, 1)
             if not numbers or n in numbers]

    with Assistant(local=local) as assistant:
        troubles = assistant.ready()
        if troubles:
            for trouble in troubles:
                print(f"  ✗ {trouble}")
            sys.exit(1)

        print(f"Вопросов {len(items)} · "
              f"{'локально' if local else 'облако'} · "
              f"{len(assistant.tools.names)} инструментов\n")

        tally = {"state": [0, 0], "docs": [0, 0], "both": [0, 0]}
        wrong_source = 0
        tokens = 0

        for number, item in items:
            try:
                answer = assistant.help(item["q"])
            except LLMError as failure:
                print(f"  {number}. сбой: {failure}")
                continue
            tokens += answer.tokens
            called = [t["name"] for t in answer.used_tools]

            # Инструменты нужны там, где ответ в git. И не нужны там,
            # где он в документах: лишний вызов не ошибка по существу,
            # но это лишние секунды и токены.
            if item["kind"] == "docs":
                source_ok = not called
            else:
                source_ok = bool(set(called) & set(item["tools"]))
            said_ok = mentions(answer.text, item["expect"])

            ok = source_ok and said_ok
            tally[item["kind"]][1] += 1
            tally[item["kind"]][0] += ok
            wrong_source += not source_ok

            print(f"  {'✓' if ok else '✗'} {number}. [{item['kind']:5}] "
                  f"{'git ' + ','.join(c.split('__')[1] for c in called) if called else 'только документы':38} "
                  f"{answer.tokens:5} ток. {answer.seconds:4.1f}с  "
                  f"{item['q'][:34]}")
            if not source_ok:
                print(f"       ждали {item['tools'] or 'без инструментов'}, "
                      f"получили {called or 'ничего'}")
            if not said_ok and item["expect"]:
                print(f"       в ответе нет: "
                      f"{'; '.join('/'.join(v) for v in item['expect'])}")
            if verbose:
                print(f"       {' '.join(answer.text.split())[:150]}")

        print(f"\n{'═' * 70}")
        total_ok = sum(v[0] for v in tally.values())
        total = sum(v[1] for v in tally.values())
        for kind, (ok, count) in tally.items():
            if count:
                print(f"  {kind:6} {ok}/{count}")
        print(f"  всего  {total_ok}/{total}")
        print(f"  источник выбран неверно: {wrong_source}")
        print(f"  токенов: {tokens:,}".replace(",", " "))
        sys.exit(0 if total_ok == total else 1)


if __name__ == "__main__":
    main()
