#!/usr/bin/env python3
"""Two goal-level scenarios: actual MCP; --live adds real model tool calling."""
import argparse
import json
import shutil
import tempfile
from pathlib import Path
from assistant import FileAssistant

HERE = Path(__file__).resolve().parent
GOALS = [
    "Найди все использования API login и создай отчёт с файлами и номерами строк.",
    "Обнови документацию по авторизации на основе кода: сигнатура API и поля ответа, включая конкретные значения из return, а не только типы. Сохрани остальные разделы.",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, help="Directory for evidence artifacts")
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix="day34-smoke-") as temporary:
        root = Path(temporary) / "project"
        shutil.copytree(HERE / "sample", root)
        originals = {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "src").glob("*.py")}
        assistant = FileAssistant(root, "cloud" if args.live else "demo")
        try:
            for goal in GOALS:
                result = assistant.run(goal, apply=True)
                if len(result["files_read"]) < 3 or not result["diff"] or not result["applied_files"]:
                    raise RuntimeError("No evidence of actual multi-file work and persisted changes")
                results.append(result)
                print(f"PASS [{assistant.mode}] {goal}\nRead: {result['files_read']}\nSaved: {result['applied_files']}")
                print(result["diff"])
            report_paths = results[0]["applied_files"]
            report = "\n".join((root / path).read_text() for path in report_paths)
            if not all(path in report for path in originals):
                raise RuntimeError("Usage report missed actual source files")
            documentation = (root / "docs/auth.md").read_text()
            if not all(text in documentation for text in ("mfa_code", "3600", "Этот раздел нужно сохранить.")):
                raise RuntimeError("Documentation was not grounded in source or unrelated content was lost")
            if "Устаревшее описание" in documentation:
                raise RuntimeError("Stale documentation remains")
            if any((root / path).read_bytes() != content for path, content in originals.items()):
                raise RuntimeError("Source code was modified")
            if not args.live:
                again = assistant.run(GOALS[1], apply=True)
                if again["diff"] or again["applied_files"]:
                    raise RuntimeError("Deterministic demo was not idempotent")
        finally:
            if args.output:
                args.output.mkdir(parents=True, exist_ok=True)
                (args.output / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                shutil.copytree(root, args.output / "project", dirs_exist_ok=True)
    print(f"{len(results)}/2 file-work scenarios passed")


if __name__ == "__main__":
    main()
