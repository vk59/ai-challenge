"""Offline tests for day 32; no model API or GitHub credentials are used."""

import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from corpus import collect_documents  # noqa: E402
from review import (  # noqa: E402
    MAX_DIFF_CHARS,
    ReviewError,
    build_review,
    collect_changes,
    main,
    render_report,
)


class GitRepository(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "day32@example.invalid")
        self.git("config", "user.name", "Day 32 Tests")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")


class CorpusTests(GitRepository):
    def test_reads_base_docs_and_python_only(self):
        self.write("README.md", "Project architecture and service boundaries. " * 8)
        self.write("src/app.py", "def load_config():\n    return 'base'\n")
        self.write(".env", "OPENROUTER_API_KEY=sk-test-secret-value-123456789")
        self.write("credentials/token.py", "TOKEN = 'ghp_123456789012345678901234567890'\n")
        self.write("certs/service.pem", "-----BEGIN PRIVATE KEY-----\nredacted\n")
        self.write("dist/generated.py", "generated = True\n")
        self.write("shared/tokens.py", "def count_tokens(text):\n    return len(text)\n")
        self.write("assets/logo.png", b"\x89PNG\x00\x01")
        base = self.commit("base")
        self.write("src/app.py", "def load_config():\n    return 'head'\n")
        self.commit("head")

        docs = collect_documents(self.repo, base)

        self.assertEqual(
            {doc.path for doc in docs},
            {"README.md", "src/app.py", "shared/tokens.py"},
        )
        source = next(doc for doc in docs if doc.path == "src/app.py")
        self.assertIn("'base'", source.text)
        self.assertNotIn("'head'", source.text)


class ChangeTests(GitRepository):
    def test_collects_patch_changed_paths_and_head_contents(self):
        self.write("src/app.py", "def value():\n    return 1\n")
        base = self.commit("base")
        self.write("src/app.py", "def value():\n    return 2\n")
        self.write("src/new.py", "def extra():\n    return True\n")
        head = self.commit("head")

        patch_text, paths, contents = collect_changes(self.repo, base, head)

        self.assertEqual(set(paths), {"src/app.py", "src/new.py"})
        self.assertIn("return 1", patch_text)
        self.assertIn("return 2", patch_text)
        self.assertEqual(contents["src/app.py"], "def value():\n    return 2\n")
        self.assertEqual(contents["src/new.py"], "def extra():\n    return True\n")

    def test_rejects_oversized_patch_instead_of_truncating(self):
        self.write("src/app.py", "value = 1\n")
        base = self.commit("base")
        self.write("src/app.py", "value = '" + ("x" * (MAX_DIFF_CHARS + 100)) + "'\n")
        head = self.commit("large change")

        with self.assertRaisesRegex(ReviewError, "слишком большой"):
            collect_changes(self.repo, base, head)


class ReportTests(unittest.TestCase):
    def test_requires_all_three_review_sections(self):
        text = """## Потенциальные баги
Нет подтверждённых багов.
## Архитектурные проблемы
Нет замечаний.
## Рекомендации
Добавить наблюдаемость.
"""
        report = render_report(text)
        self.assertIn("## Потенциальные баги", report)
        self.assertIn("## Архитектурные проблемы", report)
        self.assertIn("## Рекомендации", report)

    def test_rejects_empty_or_malformed_model_output(self):
        for text in ("", "Текст без разделов", "## Потенциальные баги\n- maybe"):
            with self.subTest(text=text), self.assertRaises(ReviewError):
                render_report(text)

    def test_local_report_does_not_require_github_token(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "review.md"
            valid = """## Потенциальные баги
Нет.
## Архитектурные проблемы
Нет.
## Рекомендации
Нет.
"""
            with patch.dict(os.environ, {}, clear=True), patch(
                "review.build_review", return_value=valid
            ):
                result = main(["--base", "base", "--head", "head", "--output", str(output)])

            self.assertEqual(result, 0)
            self.assertIn("## Рекомендации", output.read_text(encoding="utf-8"))


class BuildReviewTests(GitRepository):
    def test_uses_rag_context_and_head_diff_without_calling_external_services(self):
        self.write("README.md", "Base architecture and data flow. " * 8)
        self.write("src/app.py", "def value():\n    return 1\n")
        base = self.commit("base")
        self.write("src/app.py", "def value():\n    return 2\n")
        head = self.commit("head")

        class FakeIndex:
            observed_query = ""

            def __init__(self, path):
                self.path = path

            def rebuild(self, documents, strategy):
                self.documents = documents

            def search(self, query, strategy, k):
                FakeIndex.observed_query = query
                chunk = SimpleNamespace(
                    source="README.md", section="Architecture", text="Base RAG context"
                )
                return [(chunk, 0.9)]

        index_module = types.ModuleType("index")
        index_module.Index = FakeIndex
        index_module.STRUCTURAL = "structural"
        llm_module = types.ModuleType("llm")
        llm_module.OPENROUTER = object()
        llm_module.ask = Mock(return_value=SimpleNamespace(text="""## Потенциальные баги
Нет подтверждённых багов.
## Архитектурные проблемы
Нет замечаний.
## Рекомендации
Добавить тест на граничный случай.
"""))
        docs = [SimpleNamespace(
            path="README.md", title="README", kind="markdown",
            text="Base architecture and data flow. " * 8,
        )]

        with patch("review.collect_documents", return_value=docs), patch.dict(
            sys.modules, {"index": index_module, "llm": llm_module}
        ):
            report = build_review(self.repo, base, head)

        prompt = llm_module.ask.call_args.args[0]
        self.assertIn("return 1", prompt)
        self.assertIn("return 2", prompt)
        self.assertIn("Base RAG context", prompt)
        self.assertIn("src/app.py", FakeIndex.observed_query)
        self.assertIn("## Рекомендации", report)


if __name__ == "__main__":
    unittest.main()
