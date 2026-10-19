"""Goal-level scenarios using real MCP; cloud loop is mocked here."""
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from assistant import FileAssistant, AgentError, llm


def response(calls=(), text=""):
    return SimpleNamespace(text=text, tool_calls=[
        {"id": f"call-{i}", "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}}
        for i, (name, arguments) in enumerate(calls)])


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        shutil.copytree(HERE / "sample", self.root)

    def tearDown(self):
        self.temp.cleanup()

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "unit-test-key"})
    @patch("assistant.llm.ask")
    def test_model_billing_failure_is_actionable_and_does_not_leak_response(self, ask):
        failure = llm.LLMError("provider response must not be exposed")
        failure.__cause__ = urllib.error.HTTPError('https://example.test', 402, 'billing', {}, None)
        ask.side_effect = failure
        with self.assertRaises(AgentError) as caught:
            FileAssistant(self.root).run('Обнови документацию')
        self.assertEqual(caught.exception.code, 'model_payment_required')
        self.assertNotIn('provider response', str(caught.exception))

    def test_usage_goal_discovers_reads_and_proposes_without_writing(self):
        result = FileAssistant(self.root, mode="demo").run(
            "Найди все использования login и создай отчёт")
        self.assertGreaterEqual(len(result["files_read"]), 3)
        self.assertIn("src/web.py", result["diff"])
        self.assertIn("src/cli.py", result["diff"])
        self.assertIn("search_text", {tool["name"] for tool in result["tools"]})
        self.assertEqual(result["applied_files"], [])
        self.assertFalse((self.root / "reports").exists())

    def test_documentation_goal_applies_and_preserves_other_sections(self):
        goal = "Обнови документацию по входу на основе кода, сохрани остальные разделы"
        result = FileAssistant(self.root, mode="demo").run(goal, apply=True)
        self.assertEqual(result["applied_files"], ["docs/auth.md"])
        content = (self.root / "docs/auth.md").read_text()
        for text in ("mfa_code", "3600", "Этот раздел нужно сохранить."):
            self.assertIn(text, content)
        again = FileAssistant(self.root, mode="demo").run(goal, apply=True)
        self.assertEqual(again["diff"], "")
        self.assertEqual(again["applied_files"], [])

    def test_usage_report_is_idempotent(self):
        assistant = FileAssistant(self.root, mode="demo")
        goal = "Найди использования login и создай отчёт"
        first = assistant.run(goal, apply=True)
        self.assertTrue(first["applied_files"])
        again = assistant.run(goal, apply=True)
        self.assertEqual(again["diff"], "")

    def test_preview_can_export_exact_plan_and_progress_for_web(self):
        events = []
        result = FileAssistant(self.root, mode='demo').run(
            'Обнови документацию по входу', on_event=events.append, include_plan=True)
        plan = result['_plan']
        self.assertEqual([entry['path'] for entry in plan], ['docs/auth.md'])
        self.assertIn('3600', plan[0]['content'])
        self.assertEqual(len(plan[0]['expected_sha256']), 64)
        self.assertTrue(events)
        self.assertNotIn('3600', (self.root / 'docs/auth.md').read_text())

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "unit-test-key"})
    @patch("assistant.llm.ask")
    def test_model_itself_selects_and_calls_real_mcp_tools(self, ask):
        ask.side_effect = [
            response([("list_files", {})]),
            response([("search_text", {"query": "login"}),
                      *(('read_file', {'path': p}) for p in ("src/auth.py", "src/cli.py", "src/web.py"))]),
            response([("propose_file", {"path": "reports/model.md", "content": "# API\nsrc/auth.py src/web.py src/cli.py\n"})]),
            response(text="Отчёт подготовлен по прочитанному коду."),
        ]
        result = FileAssistant(self.root).run("Найди использования API и подготовь отчёт", apply=True)
        self.assertEqual(result["applied_files"], ["reports/model.md"])
        self.assertEqual(len(result["files_read"]), 3)
        self.assertTrue((self.root / "reports/model.md").exists())
        for call in ask.call_args_list:
            names = {t["function"]["name"] for t in call.kwargs["tools"]}
            self.assertNotIn("apply_changes", names)
        self.assertTrue(any(msg["role"] == "tool" for msg in ask.call_args.kwargs["history"]))

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "unit-test-key"})
    @patch("assistant.llm.ask", return_value=response(text="Всё сделано!"))
    def test_text_only_model_answer_does_not_count_as_file_work(self, ask):
        with self.assertRaises(AgentError) as caught:
            FileAssistant(self.root).run("Обнови документацию", apply=True)
        self.assertEqual(caught.exception.code, "insufficient_evidence")
        self.assertNotIn("3600", (self.root / "docs/auth.md").read_text())

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "unit-test-key"})
    @patch("assistant.llm.ask", return_value=response([("list_files", {})]))
    def test_repeated_tool_calls_stop_at_step_budget(self, ask):
        with self.assertRaises(AgentError) as caught:
            FileAssistant(self.root).run("Обнови документацию", apply=True)
        self.assertEqual(caught.exception.code, "step_limit")
        self.assertLessEqual(ask.call_count, 12)

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "unit-test-key"})
    @patch("assistant.llm.ask")
    def test_reading_without_proposing_is_not_claimed_as_completion(self, ask):
        ask.side_effect = [response([('read_file', {'path': p}) for p in (
            'src/auth.py', 'src/cli.py', 'src/web.py')]), response(text="Документация обновлена")]
        with self.assertRaises(AgentError) as caught:
            FileAssistant(self.root).run("Обнови документацию", apply=True)
        self.assertEqual(caught.exception.code, "no_changes_prepared")

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "unit-test-key"})
    @patch("assistant.llm.ask")
    def test_model_is_sent_back_when_report_omits_a_search_match(self, ask):
        ask.side_effect = [
            response([('search_text', {'query': 'login'}),
                      *(('read_file', {'path': p}) for p in ('src/auth.py', 'src/web.py', 'docs/auth.md')),
                      ('propose_file', {'path': 'reports/uses.md', 'content': 'src/auth.py src/web.py'})]),
            response(text='Готово'),
            response([('read_file', {'path': 'src/cli.py'}),
                      ('propose_file', {'path': 'reports/uses.md', 'content': 'src/auth.py src/web.py src/cli.py'})]),
            response(text='Готово, все найденные файлы учтены'),
        ]
        result = FileAssistant(self.root).run('Найди все использования login и создай отчёт', apply=True)
        self.assertIn('src/cli.py', (self.root / 'reports/uses.md').read_text())
        self.assertEqual(len(result['files_read']), 4)
        self.assertEqual(ask.call_count, 4)

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "unit-test-key"})
    @patch("assistant.llm.ask")
    def test_legitimate_no_match_report_can_complete(self, ask):
        ask.side_effect = [response([
            ('search_text', {'query': 'logout'}),
            *(('read_file', {'path': p}) for p in ('src/auth.py', 'src/web.py', 'src/cli.py')),
            ('propose_file', {'path': 'reports/logout.md', 'content': '# logout\nСовпадений нет.\n'})]),
            response(text='Использований logout не найдено, отчёт подготовлен')]
        result = FileAssistant(self.root).run('Найди использования logout и создай отчёт', apply=True)
        self.assertEqual(result['applied_files'], ['reports/logout.md'])

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": "unit-test-key"})
    @patch("assistant.llm.ask")
    def test_hardlinks_cannot_fake_three_read_files(self, ask):
        os.link(self.root / 'src/auth.py', self.root / 'src/alias1.py')
        os.link(self.root / 'src/auth.py', self.root / 'src/alias2.py')
        ask.side_effect = [response([
            *(('read_file', {'path': p}) for p in ('src/auth.py', 'src/alias1.py', 'src/alias2.py')),
            ('propose_file', {'path': 'reports/test.md', 'content': '# Read one file'})]), response(text='Готово')]
        with self.assertRaises(AgentError) as caught:
            FileAssistant(self.root).run('Изучи API и создай отчёт', apply=True)
        self.assertEqual(caught.exception.code, 'insufficient_evidence')


if __name__ == "__main__":
    unittest.main()
