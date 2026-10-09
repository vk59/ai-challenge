#!/usr/bin/env python3
"""День 28: локальный RAG против облачного — качество, скорость, стабильность.

    python3 bench.py                      # три стека, 3 повтора
    python3 bench.py --runs 5             # больше повторов
    python3 bench.py --stacks local:qwen2.5:7b,cloud
    python3 bench.py 3 7                  # только эти вопросы
    python3 bench.py --save итог.json     # сохранить сырые числа

Стеки задаются через запятую: `cloud`, `local` (модель по умолчанию)
или `local:имя-модели`.

Все прежние замеры в этом проекте были однократными, и это их слабое
место: разброс между прогонами я видел и в дне 25, и в дне 26, но нигде
не измерил. Здесь каждый вопрос задаётся несколько раз, и считается
не только «сколько попал», но и насколько ответ воспроизводим.

Стек — это пара «эмбеддинги + модель ответа», целиком своя:

    local  bge-m3 + qwen2.5:3b или 7b, индекс index-local.db, порог 0.54
    cloud  text-embedding-3-small + deepseek, индекс index.db, порог 0.39

Смешивать нельзя: векторы разных моделей несравнимы, и локальная модель
с облачным индексом — это не «локальный RAG», а полумера.
"""

import json
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

from questions import ВОПРОСЫ as QUESTIONS  # noqa: E402

DEFAULT_RUNS = 3
DEFAULT_STACKS = "local:qwen2.5:3b,local:qwen2.5:7b,cloud"


def parse_stacks(raw: str) -> list[tuple[str, str | None]]:
    """«local:qwen2.5:7b,cloud» → [("local", "qwen2.5:7b"), ("cloud", None)]"""
    out: list[tuple[str, str | None]] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        backend, _, model = piece.partition(":")
        backend = backend.strip().lower()
        if backend not in ("local", "cloud"):
            raise SystemExit(f"Неизвестный стек «{piece}». "
                             f"Есть: cloud, local, local:модель")
        out.append((backend, model.strip() or None if backend == "local"
                    else None))
    return out


def stack_label(backend: str, model: str | None) -> str:
    if backend == "cloud":
        return "cloud"
    from llm import DEFAULT_LOCAL_MODEL
    return (model or DEFAULT_LOCAL_MODEL).replace("qwen2.5:", "qwen-")


def load_stack(backend: str):
    """Переключить весь стек на бэкенд и вернуть его модули."""
    os.environ["AI_ADVENT_EMBEDDINGS"] = backend
    import importlib

    import cite
    import embeddings
    import index as index_module
    for module in (embeddings, index_module, cite):
        importlib.reload(module)
    return {"embeddings": embeddings, "index": index_module, "cite": cite}


def matches(text: str, expect: list[list[str]]) -> bool:
    low = (text or "").lower()
    return all(any(v.lower() in low for v in variants) for variants in expect)


def ask_once(stack, backend: str, item: dict, model: str | None) -> dict:
    """Один вопрос, один прогон. Всё, что потом понадобится для разбора."""
    from llm import DEFAULT_LOCAL_MODEL, LOCAL, LLMError

    started = time.monotonic()
    try:
        result = stack["cite"].answer_with_citations(
            item["q"], index=stack["index"].Index(), k=5,
            provider=None if backend == "cloud" else LOCAL,
            model=None if backend == "cloud" else (model or DEFAULT_LOCAL_MODEL),
            max_tokens=700)
    except LLMError as failure:
        return {"failed": True, "why": str(failure),
                "seconds": time.monotonic() - started}
    spent = time.monotonic() - started

    sources = {s.source for s in result.sources}
    return {
        "failed": False,
        "right": matches(result.answer, item["expect"]),
        "sources_ok": bool(sources & item["sources"]),
        "has_sources": bool(result.sources),
        "quotes": len(result.quotes),
        "verified": result.verified_quotes,
        "fabricated": result.fabricated_quotes,
        "abstained": result.abstained,
        "confident": result.confident,
        "blank": result.blank,
        "parse_failed": result.parse_failed,
        "tokens": result.total_tokens,
        "seconds": spent,
        "answer": result.answer,
    }


def run_backend(backend: str, items: list[tuple[int, dict]], runs: int,
                model: str | None, verbose: bool) -> dict | None:
    stack = load_stack(backend)
    path = stack["index"].база_для(backend)
    if not path.exists():
        print(f"\n{backend}: индекса нет ({path.name}). Соберите:")
        print(f"  AI_ADVENT_EMBEDDINGS={backend} "
              f"python3 ../day-21-indexing/build.py")
        return None

    from llm import DEFAULT_LOCAL_MODEL
    answer_model = ("deepseek (облако)" if backend == "cloud"
                    else (model or DEFAULT_LOCAL_MODEL))
    print(f"\n{'═' * 78}")
    print(f"{backend}: {stack['embeddings'].модель_бэкенда(backend)} "
          f"+ {answer_model}")
    print(f"индекс {path.name} · порог {stack['cite'].порог_отказа(backend)} "
          f"· повторов {runs}")

    rows = []
    for number, item in items:
        attempts = [ask_once(stack, backend, item, model) for _ in range(runs)]
        good = [a for a in attempts if not a["failed"]]
        right = sum(1 for a in good if a["right"])
        # Воспроизводимость: ответ либо верен всегда, либо неверен всегда.
        # Промежуточное — это и есть нестабильность.
        steady = right == 0 or right == len(good)
        seconds = [a["seconds"] for a in good]
        row = {
            "n": number, "question": item["q"], "runs": len(attempts),
            "ok": len(good), "right": right, "steady": steady,
            "sources_ok": sum(1 for a in good if a["sources_ok"]),
            "verified": sum(a["verified"] for a in good),
            "fabricated": sum(a["fabricated"] for a in good),
            "abstained": sum(1 for a in good if a["abstained"]),
            "blank": sum(1 for a in good if a["blank"]),
            "parse_failed": sum(1 for a in good if a["parse_failed"]),
            "tokens": sum(a["tokens"] for a in good),
            "seconds": seconds,
            "median": statistics.median(seconds) if seconds else 0.0,
        }
        rows.append(row)
        mark = ("✓" if right == len(good) else
                ("~" if right else "✗")) if good else "!"
        print(f"  {mark} {number:2}. {right}/{len(good)} верных  "
              f"{row['median']:5.2f}с  ист. {row['sources_ok']}/{len(good)}  "
              f"цитат {row['verified']}"
              + (f" (+{row['fabricated']} выдумано)" if row["fabricated"] else "")
              + f"   {item['q'][:38]}")
        if verbose and not steady:
            for attempt in good:
                print(f"       [{'✓' if attempt['right'] else '✗'}] "
                      f"{' '.join(attempt['answer'].split())[:110]}")

    return {"backend": backend, "model": answer_model, "rows": rows,
            "runs": runs, "label": stack_label(backend, model)}


def summarize(result: dict) -> dict:
    rows = result["rows"]
    total_runs = sum(r["ok"] for r in rows)
    all_seconds = [s for r in rows for s in r["seconds"]]
    all_seconds.sort()

    def percentile(data: list[float], share: float) -> float:
        if not data:
            return 0.0
        position = min(len(data) - 1, int(round(share * (len(data) - 1))))
        return data[position]

    return {
        "backend": result["backend"],
        "label": result["label"],
        "model": result["model"],
        "questions": len(rows),
        "runs": total_runs,
        # Качество: доля верных ответов среди всех прогонов.
        "right": sum(r["right"] for r in rows),
        "sources_ok": sum(r["sources_ok"] for r in rows),
        "verified": sum(r["verified"] for r in rows),
        "fabricated": sum(r["fabricated"] for r in rows),
        # Стабильность: на скольких вопросах ответ не менялся от прогона
        # к прогону, и сколько раз модель просто сломалась.
        "steady": sum(1 for r in rows if r["steady"]),
        "blank": sum(r["blank"] for r in rows),
        "parse_failed": sum(r["parse_failed"] for r in rows),
        "abstained": sum(r["abstained"] for r in rows),
        # Скорость.
        "median": statistics.median(all_seconds) if all_seconds else 0.0,
        "p95": percentile(all_seconds, 0.95),
        "fastest": all_seconds[0] if all_seconds else 0.0,
        "slowest": all_seconds[-1] if all_seconds else 0.0,
        "spread": (statistics.pstdev(all_seconds)
                   if len(all_seconds) > 1 else 0.0),
        "tokens": sum(r["tokens"] for r in rows),
    }


def main() -> None:
    argv = sys.argv[1:]
    verbose = "-v" in argv or "--verbose" in argv
    runs = DEFAULT_RUNS
    if "--runs" in argv:
        position = argv.index("--runs")
        if position + 1 < len(argv) and argv[position + 1].isdigit():
            runs = max(1, int(argv[position + 1]))
            argv.pop(position + 1)
    model = None
    if "--model" in argv:
        position = argv.index("--model")
        if position + 1 < len(argv):
            model = argv[position + 1]
            argv.pop(position + 1)
    save_to = None
    if "--save" in argv:
        position = argv.index("--save")
        if position + 1 < len(argv):
            save_to = argv[position + 1]
            argv.pop(position + 1)
    raw_stacks = DEFAULT_STACKS
    for flag in ("--stacks", "--only"):
        if flag in argv:
            position = argv.index(flag)
            if position + 1 < len(argv):
                raw_stacks = argv[position + 1]
                argv.pop(position + 1)
            argv.remove(flag)
    stacks = parse_stacks(raw_stacks)
    if model:
        # --model остался для совместимости: применяем к локальным стекам.
        stacks = [(b, model if b == "local" else m) for b, m in stacks]

    numbers = [int(a) for a in argv if a.isdigit()]
    items = [(n, q) for n, q in enumerate(QUESTIONS, 1)
             if not numbers or n in numbers]

    print(f"Вопросов {len(items)}, повторов {runs}, стеков {len(stacks)}, "
          f"итого {len(items) * runs * len(stacks)} ответов")

    results = [r for r in (run_backend(b, items, runs, m, verbose)
                           for b, m in stacks) if r]
    if not results:
        return
    summaries = [summarize(r) for r in results]

    print(f"\n{'═' * 78}")
    print(f"{'стек':11} {'верных':>10} {'источники':>12} {'цитат':>8} "
          f"{'стабильно':>11} {'медиана':>9} {'p95':>7}")
    for s in summaries:
        print(f"{s['label']:11} {s['right']:>6}/{s['runs']:<3} "
              f"{s['sources_ok']:>8}/{s['runs']:<3} "
              f"{s['verified']:>5}"
              + (f"+{s['fabricated']}" if s["fabricated"] else "  ")
              + f" {s['steady']:>7}/{s['questions']:<3} "
              f"{s['median']:>8.2f}с {s['p95']:>6.2f}с")

    print(f"\n{'стек':11} {'разброс':>9} {'быстрее':>9} {'медленнее':>11} "
          f"{'сбоев':>7} {'отказов':>9} {'токенов':>9}")
    for s in summaries:
        breaks = s["blank"] + s["parse_failed"]
        print(f"{s['label']:11} ±{s['spread']:>7.2f}с {s['fastest']:>8.2f}с "
              f"{s['slowest']:>10.2f}с {breaks:>7} {s['abstained']:>9} "
              f"{s['tokens']:>9,}".replace(",", " "))

    locals_ = [s for s in summaries if s["backend"] == "local"]
    clouds = [s for s in summaries if s["backend"] == "cloud"]
    if locals_ and clouds:
        # Сравниваем лучший локальный: вопрос дня не «хуже ли локальный
        # вообще», а «насколько близко к облаку можно подобраться».
        best = max(locals_, key=lambda s: (s["right"], s["verified"]))
        cloud = clouds[0]
        print(f"\n{'─' * 78}")
        print(f"лучший локальный стек: {best['label']}")
        quality = best["right"] / max(1, best["runs"]) - \
            cloud["right"] / max(1, cloud["runs"])
        print(f"качество     {quality * 100:+.0f} процентных пунктов к облаку")
        if best["median"]:
            ratio = best["median"] / cloud["median"] if cloud["median"] else 0
            print(f"скорость     в {ratio:.1f} раза медленнее по медиане, "
                  f"p95 {best['p95']:.1f}с против {cloud['p95']:.1f}с")
        print(f"стабильность {best['steady']}/{best['questions']} против "
              f"{cloud['steady']}/{cloud['questions']} вопросов без разброса, "
              f"разброс задержки ±{best['spread']:.2f}с против "
              f"±{cloud['spread']:.2f}с")
        print(f"цитаты       {best['verified']} подтверждённых "
              f"(+{best['fabricated']} выдуманных) против "
              f"{cloud['verified']} (+{cloud['fabricated']})")
        print(f"деньги       $0 против оплаты по тарифу")

    if save_to:
        payload = {"runs": runs, "summaries": summaries,
                   "details": [{"backend": r["backend"], "rows": r["rows"]}
                               for r in results]}
        Path(save_to).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nсырые числа: {save_to}")


if __name__ == "__main__":
    main()
