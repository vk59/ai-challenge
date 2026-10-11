#!/usr/bin/env python3
"""День 29: прогон вариантов настройки и сравнение с ориентирами.

    python3 tune.py                      # все варианты, 2 повтора
    python3 tune.py --runs 3
    python3 tune.py base pointer         # только эти
    python3 tune.py -v                   # плюс ответы
    python3 tune.py --save итог.json

Мерится то же, что в дне 28, плюс одна мера, которой там не было.

    дословно   цитата нашлась в выданном куске знак в знак. Для варианта
               с указанием строк это всегда 100%: текст подставляем мы.
    опора      подтверждающий фрагмент содержит то, что мы ждём в ответе.
               Вот эта мера честная для обоих подходов: указать можно
               и не ту строку, и тогда цитата дословная, но бесполезная.

Без второй меры вариант `pointer` выиграл бы по определению, и сравнение
было бы подтасовкой.
"""

import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import paths  # noqa: E402

paths.setup()

os.environ.setdefault("AI_ADVENT_EMBEDDINGS", "local")

from cite import build_excerpts, normalize, verify_quote  # noqa: E402
from index import Index  # noqa: E402
from llm import LOCAL, LLMError, ask  # noqa: E402
from questions import ВОПРОСЫ as QUESTIONS  # noqa: E402
from variants import (COPY_PROMPT, COPY_SHOWN_PROMPT,  # noqa: E402
                      FINAL_PROMPT, POINTER_PROMPT, TARGETS, VARIANTS)

DEFAULT_RUNS = 2

# Фраза короче этого в строку-цитату не годится: подтверждать нечем.
MIN_LINE = 25
SPLIT = re.compile(r"(?<=[.!?:;])\s+|\n+")


def numbered_lines(chunks: list[dict]) -> tuple[str, dict]:
    """Строки выдержек с номерами и таблица «номер → откуда»."""
    rendered, lookup = [], {}
    number = 0
    for position, chunk in enumerate(chunks, 1):
        rendered.append(f"── выдержка {position}: {chunk['source']} "
                        f"«{chunk['section']}»")
        for piece in SPLIT.split(chunk["text"] or ""):
            piece = " ".join(piece.split())
            if len(piece) < MIN_LINE:
                continue
            number += 1
            lookup[number] = {"text": piece, "source": chunk["source"],
                              "chunk_id": chunk["chunk_id"], "n": position}
            rendered.append(f"{number:3} │ {piece}")
    return "\n".join(rendered), lookup


def parse_json(raw: str) -> dict | None:
    block = re.search(r"\{.*\}", raw or "", re.S)
    if not block:
        return None
    try:
        data = json.loads(block.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def matches(text: str, expect: list[list[str]]) -> bool:
    low = normalize(text)
    return all(any(v.lower() in low for v in variants)
               for variants in expect)


def run_once(variant: dict, item: dict, index: Index) -> dict:
    """Один вопрос одним вариантом. Возвращает всё для сводки."""
    pairs = index.search(item["q"], "structural", k=variant["chunks"])
    chunks = [{"source": c.source, "section": c.section,
               "chunk_id": c.chunk_id, "text": c.text} for c, _ in pairs]

    pointer = variant["style"] in ("pointer", "final")
    if pointer:
        excerpts, lookup = numbered_lines(chunks)
        template = (FINAL_PROMPT if variant["style"] == "final"
                    else POINTER_PROMPT)
        system = template.format(plain=build_excerpts(chunks),
                                 excerpts=excerpts)
    else:
        lookup = {}
        template = (COPY_SHOWN_PROMPT if variant["style"] == "copy_shown"
                    else COPY_PROMPT)
        system = template.format(excerpts=build_excerpts(chunks))

    started = time.monotonic()
    try:
        reply = ask(item["q"], system=system,
                    provider=LOCAL, model=variant["model"],
                    max_tokens=variant["max_tokens"],
                    temperature=variant["temperature"], json_mode=True)
    except LLMError as failure:
        return {"failed": True, "why": str(failure),
                "seconds": time.monotonic() - started}
    spent = time.monotonic() - started

    data = parse_json(reply.text)
    if data is None:
        return {"failed": False, "parse_failed": True, "right": False,
                "support": False, "verified": 0, "fabricated": 0,
                "sources_ok": False, "cites": 0, "confident": False,
                "seconds": spent,
                "tokens": int(reply.usage.get("total_tokens") or 0),
                "answer": (reply.text or "")[:200]}

    answer = str(data.get("answer") or "").strip()
    confident = bool(data.get("confident", True))

    verified = fabricated = 0
    cited_text: list[str] = []
    sources: set[str] = set()

    if pointer:
        # Больше четырёх ссылок — это уже не «самые точные», а перечисление
        # всего подряд. Лишние отбрасываем, иначе опора засчитается по
        # случайному попаданию.
        for raw in (data.get("lines") or [])[:4]:
            try:
                number = int(raw)
            except (TypeError, ValueError):
                fabricated += 1
                continue
            line = lookup.get(number)
            if not line:
                # Номера нет в выдаче — это и есть выдумка в этом подходе.
                fabricated += 1
                continue
            verified += 1
            cited_text.append(line["text"])
            sources.add(line["source"])
    else:
        for quote in (data.get("quotes") or []):
            if not isinstance(quote, dict):
                continue
            try:
                number = int(quote.get("n", 0) or 0)
            except (TypeError, ValueError):
                number = 0
            text = str(quote.get("text") or "").strip()
            if not text:
                continue
            if 1 <= number <= len(chunks):
                ok, _ = verify_quote(text, chunks[number - 1]["text"])
                if ok:
                    verified += 1
                    cited_text.append(text)
                    sources.add(chunks[number - 1]["source"])
                else:
                    fabricated += 1
            else:
                fabricated += 1
        # Формат "sources" модель соблюдает не всегда: вместо {"n": 1}
        # приходит то число, то строка с chunk_id целиком. Падать на этом
        # нельзя — это и есть нестабильность, которую мы измеряем.
        for source in (data.get("sources") or []):
            if isinstance(source, dict):
                raw = source.get("n", source.get("source", 0))
            else:
                raw = source
            try:
                number = int(raw)
            except (TypeError, ValueError):
                # Пришёл путь или chunk_id — попробуем узнать его среди
                # выданных кусков.
                text = str(raw)
                for chunk in chunks:
                    if chunk["chunk_id"] == text or chunk["source"] == text:
                        sources.add(chunk["source"])
                        break
                continue
            if 1 <= number <= len(chunks):
                sources.add(chunks[number - 1]["source"])

    pile = " ".join(normalize(t) for t in cited_text)
    support = bool(pile) and all(
        any(v.lower() in pile for v in variants)
        for variants in item["expect"])

    right = matches(answer, item["expect"])
    return {
        "failed": False, "parse_failed": False,
        "right": right,
        "support": support,
        # Главная мера для пользователя: факт перед глазами — неважно,
        # в тексте ответа или в приведённой цитате под ним. У варианта
        # с указанием строк факт часто уезжает из ответа в цитату,
        # и считать это промахом было бы неверно.
        "shown": right or support,
        "verified": verified, "fabricated": fabricated,
        "cites": verified + fabricated,
        "sources_ok": bool(sources & item["sources"]),
        "confident": confident,
        "seconds": spent,
        "tokens": int(reply.usage.get("total_tokens") or 0),
        "answer": answer,
    }


def run_variant(variant: dict, items, runs: int, index: Index,
                verbose: bool) -> dict:
    print(f"\n{'═' * 78}")
    print(f"{variant['key']}: {variant['title']}")
    print(f"  {variant['note']}")

    rows = []
    for number, item in items:
        attempts = []
        for _ in range(runs):
            try:
                attempts.append(run_once(variant, item, index))
            except Exception as failure:                  # noqa: BLE001
                # Падение разбора — тоже результат варианта, а не повод
                # прервать прогон. Считаем попытку неудачной и идём дальше.
                attempts.append({"failed": True,
                                 "why": f"{type(failure).__name__}: {failure}",
                                 "seconds": 0.0})
        good = [a for a in attempts if not a["failed"]]
        right = sum(1 for a in good if a["right"])
        seconds = [a["seconds"] for a in good]
        row = {
            "n": number, "runs": len(attempts), "ok": len(good),
            "right": right,
            "support": sum(1 for a in good if a["support"]),
            "shown": sum(1 for a in good if a.get("shown")),
            "verified": sum(a["verified"] for a in good),
            "fabricated": sum(a["fabricated"] for a in good),
            "sources_ok": sum(1 for a in good if a["sources_ok"]),
            "parse_failed": sum(1 for a in good if a["parse_failed"]),
            "tokens": sum(a["tokens"] for a in good),
            "seconds": seconds,
            "median": statistics.median(seconds) if seconds else 0.0,
            "steady": right == 0 or right == len(good),
        }
        rows.append(row)
        mark = "✓" if right == len(good) else ("~" if right else "✗")
        print(f"  {mark} {number:2}. {right}/{len(good)} верных  "
              f"опора {row['support']}/{len(good)}  "
              f"цитат {row['verified']}"
              + (f" (+{row['fabricated']})" if row["fabricated"] else "")
              + f"  {row['median']:5.2f}с   {item['q'][:34]}")
        if verbose:
            print(f"       {' '.join(good[0]['answer'].split())[:130]}"
                  if good else "       (сбой)")
    return {"variant": variant, "rows": rows, "runs": runs}


def summarize(result: dict) -> dict:
    rows = result["rows"]
    seconds = sorted(s for r in rows for s in r["seconds"])
    total = sum(r["ok"] for r in rows)

    def pct(share: float) -> float:
        if not seconds:
            return 0.0
        return seconds[min(len(seconds) - 1,
                           int(round(share * (len(seconds) - 1))))]

    cites = sum(r["verified"] + r["fabricated"] for r in rows)
    verified = sum(r["verified"] for r in rows)
    return {
        "key": result["variant"]["key"],
        "title": result["variant"]["title"],
        "runs": total,
        "right": sum(r["right"] for r in rows),
        "support": sum(r["support"] for r in rows),
        "shown": sum(r.get("shown", 0) for r in rows),
        "sources_ok": sum(r["sources_ok"] for r in rows),
        "verified": verified,
        "fabricated": sum(r["fabricated"] for r in rows),
        "exact_share": (verified / cites) if cites else 0.0,
        "steady": sum(1 for r in rows if r["steady"]),
        "questions": len(rows),
        "parse_failed": sum(r["parse_failed"] for r in rows),
        "median": statistics.median(seconds) if seconds else 0.0,
        "p95": pct(0.95),
        "spread": statistics.pstdev(seconds) if len(seconds) > 1 else 0.0,
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
    save_to = None
    if "--save" in argv:
        position = argv.index("--save")
        if position + 1 < len(argv):
            save_to = argv[position + 1]
            argv.pop(position + 1)
    keys = [a for a in argv if not a.startswith("-")]
    chosen = [v for v in VARIANTS if not keys or v["key"] in keys]
    if not chosen:
        print(f"Нет таких вариантов. Есть: "
              f"{', '.join(v['key'] for v in VARIANTS)}")
        sys.exit(1)

    items = list(enumerate(QUESTIONS, 1))
    index = Index()
    print(f"Вопросов {len(items)}, повторов {runs}, вариантов {len(chosen)}, "
          f"итого {len(items) * runs * len(chosen)} ответов")

    # Сохраняем после каждого варианта: смена квантования заставляет
    # Ollama перезагружать модель, прогон идёт долго, и терять всё
    # из-за прерывания на последнем варианте обидно.
    summaries = []
    for variant in chosen:
        summaries.append(summarize(run_variant(variant, items, runs, index,
                                               verbose)))
        if save_to:
            Path(save_to).write_text(
                json.dumps({"runs": runs, "summaries": summaries},
                           ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'═' * 78}")
    print(f"{'вариант':10} {'в ответе':>9} {'в цитате':>9} {'видно':>8} "
          f"{'цитат':>9} {'дословно':>9} {'медиана':>9} {'p95':>7}")
    for s in summaries:
        print(f"{s['key']:10} {s['right']:>6}/{s['runs']:<2} "
              f"{s['support']:>6}/{s['runs']:<2} "
              f"{s.get('shown', 0):>5}/{s['runs']:<2} "
              f"{s['verified']:>5}"
              + (f"+{s['fabricated']:<3}" if s["fabricated"] else "    ")
              + f" {s['exact_share'] * 100:>7.0f}% "
              f"{s['median']:>8.2f}с {s['p95']:>6.2f}с")

    print(f"\n{'─' * 78}\nОриентиры дня 28 (30 прогонов):")
    for name, t in TARGETS.items():
        print(f"  {name:22} верных {t['right']}/30, цитат {t['verified']}"
              f" (+{t['fabricated']}), медиана {t['median']:.2f}с")

    if summaries:
        best = max(summaries, key=lambda s: (s.get("shown", s["right"]),
                                             s["support"], -s["median"]))
        target = TARGETS["qwen-7b (цель)"]
        share = best.get("shown", best["right"]) / max(1, best["runs"])
        goal = target["right"] / 30
        print(f"\nЛучший вариант: {best['key']} — {best['title']}")
        print(f"  факт виден в {share * 100:.0f}% против "
              f"{goal * 100:.0f}% верных у 7B "
              f"({'догнали' if share >= goal else 'не догнали'})")
        print(f"  медиана {best['median']:.2f}с против {target['median']:.2f}с "
              f"у 7B ({'быстрее' if best['median'] < target['median'] else 'медленнее'})")

    if save_to:
        Path(save_to).write_text(
            json.dumps({"runs": runs, "summaries": summaries},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nсырые числа: {save_to}")


if __name__ == "__main__":
    main()
