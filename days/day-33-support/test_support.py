"""Offline checks: real stdio MCP and HTTP; cloud model calls are mocked."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "shared"))

from mcp import MCPClient
from assistant import SupportAssistant, SupportError
from knowledge import Knowledge


class CRMTests(unittest.TestCase):
    def test_mcp_close_releases_pipes_and_process(self):
        with MCPClient([sys.executable, str(HERE / "crm_mcp.py")]) as client:
            process = client._proc
            pipes = (process.stdin, process.stdout, process.stderr)
        self.assertIsNotNone(process.poll())
        self.assertTrue(all(stream.closed for stream in pipes))

    def call(self, user_id, ticket_id=None):
        with MCPClient([sys.executable, str(HERE / "crm_mcp.py")]) as client:
            self.assertEqual(client.info.name, "taskflow-crm")
            self.assertEqual([t.name for t in client.list_tools()], ["get_support_context"])
            return client.call_tool("get_support_context", {
                "user_id": user_id, **({"ticket_id": ticket_id} if ticket_id else {})})

    def test_reads_user_and_owned_ticket_over_mcp(self):
        result = self.call("U-100", "T-100")
        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"]["ticket"]["error_code"], "SSO_REQUIRED")
        self.assertNotIn("password", json.dumps(result))

    def test_foreign_and_missing_ticket_are_errors(self):
        for user_id, ticket_id, expected in (
            ("U-100", "T-200", "forbidden"),
            ("U-100", "T-999", "not_found"),
            ("U-999", "T-100", "not_found"),
        ):
            with self.subTest(user=user_id, ticket=ticket_id):
                result = self.call(user_id, ticket_id)
                self.assertTrue(result["isError"])
                error = json.loads(result["content"][0]["text"])
                self.assertEqual(error["code"], expected)
                self.assertNotIn("SSO_REQUIRED", json.dumps(error))

    def test_invalid_identifiers_are_rejected(self):
        result = self.call("../../crm", "T-100")
        self.assertTrue(result["isError"])


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.assistant = SupportAssistant(mode="demo")

    def test_same_question_uses_distinct_ticket_context(self):
        for user, ticket, expected in (
            ("U-100", "T-100", "SSO"),
            ("U-200", "T-200", "15 минут"),
            ("U-300", "T-300", "подтверждения"),
        ):
            with self.subTest(ticket=ticket):
                answer = self.assistant.answer("Почему не работает авторизация?", user, ticket)
                self.assertEqual(answer["status"], "answered")
                self.assertEqual(answer["mode"], "demo")
                self.assertIn(expected, answer["answer"])
                self.assertEqual(answer["context"]["ticket"]["id"], ticket)
                self.assertTrue(answer["sources"])
                self.assertEqual(answer["tools"][0]["name"], "get_support_context")
                self.assertNotIn("LLM", answer["tools"][0]["name"])

    def test_foreign_ticket_never_reaches_retrieval(self):
        self.assistant.knowledge.search = Mock()
        with self.assertRaises(SupportError) as caught:
            self.assistant.answer("Помогите войти", "U-100", "T-200")
        self.assertEqual(caught.exception.code, "forbidden")
        self.assistant.knowledge.search.assert_not_called()

    def test_context_does_not_survive_between_requests(self):
        self.assistant.answer("Почему не работает авторизация?", "U-100", "T-100")
        second = self.assistant.answer("Как экспортировать задачи?", "U-200")
        self.assertIsNone(second["context"]["ticket"])
        self.assertNotIn("U-100", json.dumps(second))
        self.assertIn("CSV", second["answer"])

    def test_unknown_question_escalates_without_inventing_answer(self):
        answer = self.assistant.answer("Как приготовить борщ?", "U-100")
        self.assertEqual(answer["status"], "needs_operator")
        self.assertEqual(answer["sources"], [])

    def test_input_limits_are_validated_before_mcp(self):
        for question in ("", "x" * 2001, None):
            with self.subTest(question=str(question)[:10]), self.assertRaises(SupportError):
                self.assistant.answer(question, "U-100")

    @patch("assistant.llm.ask")
    def test_cloud_prompt_contains_only_scoped_context_and_retrieved_docs(self, ask):
        # Keep real MCP but replace the network-dependent knowledge implementation.
        assistant = SupportAssistant(mode="cloud", knowledge=Knowledge(mode="demo"))
        ask.return_value = SimpleNamespace(text="В вашей организации обязателен SSO. [1]")
        result = assistant.answer("Почему не работает авторизация?", "U-100", "T-100")
        prompt = ask.call_args.args[0]
        self.assertIn("SSO_REQUIRED", prompt)
        self.assertNotIn("U-200", prompt)
        self.assertIn("docs/auth.md", prompt)
        self.assertEqual(result["mode"], "cloud")
        self.assertIs(ask.call_args.kwargs["provider"], __import__("llm").OPENROUTER)

    @patch("assistant.llm.ask")
    def test_empty_model_answer_is_an_error(self, ask):
        assistant = SupportAssistant(mode="cloud", knowledge=Knowledge(mode="demo"))
        ask.return_value = SimpleNamespace(text=" ")
        with self.assertRaises(SupportError) as caught:
            assistant.answer("Вход", "U-100", "T-100")
        self.assertEqual(caught.exception.code, "model_error")


if __name__ == "__main__":
    unittest.main()
