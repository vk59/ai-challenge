"""Exercise the actual local HTTP server without using a browser."""

import http.client
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assistant import SupportAssistant
from service import create_server


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server(SupportAssistant(mode="demo"), port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method="POST", path="/api/ask", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            if body is None and method == "POST":
                body = json.dumps({"question": "Почему не работает авторизация?",
                                   "user_id": "U-100", "ticket_id": "T-100"}).encode()
            connection.request(method, path, body, {"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            connection.close()

    def test_serves_chat_health_and_answer(self):
        code, _, body = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("TaskFlow", body.decode())
        code, _, body = self.request("GET", "/health")
        self.assertEqual(json.loads(body)["mode"], "demo")
        code, headers, body = self.request()
        self.assertEqual(code, 200)
        result = json.loads(body)
        self.assertIn("SSO", result["answer"])
        self.assertEqual(result["tools"][0]["name"], "get_support_context")
        self.assertTrue(result["sources"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_foreign_ticket_returns_403(self):
        code, _, body = self.request(body=json.dumps({"question": "Вход",
                                    "user_id": "U-100", "ticket_id": "T-200"}).encode())
        self.assertEqual(code, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "forbidden")
        self.assertNotIn("ACCOUNT_LOCKED", body.decode())

    def test_rejects_bad_json_shapes_and_missing_fields(self):
        for body in (b"{", b"[]", b"null", b"{}", b'{"question":42}', b'{"extra":true}'):
            with self.subTest(body=body):
                code, _, result = self.request(body=body)
                self.assertEqual(code, 400)
                self.assertIn("error", json.loads(result))

    def test_rejects_excessive_body_before_reading(self):
        code, _, _ = self.request(body=b"", headers={"Content-Length": "20000"})
        self.assertEqual(code, 413)

    def test_rejects_other_origins_hosts_and_content_types(self):
        for headers, expected in (({"Origin": "https://other.example"}, 403),
                                  ({"Host": "other.example"}, 403),
                                  ({"Content-Type": "text/plain"}, 415)):
            with self.subTest(headers=headers):
                code, _, _ = self.request(headers=headers)
                self.assertEqual(code, expected)

    def test_busy_service_returns_retryable_error(self):
        self.server.gate.acquire()
        try:
            code, headers, body = self.request()
            self.assertEqual(code, 503)
            self.assertEqual(json.loads(body)["error"]["code"], "busy")
            self.assertIn("Retry-After", headers)
        finally:
            self.server.gate.release()

    def test_does_not_serve_arbitrary_files(self):
        for path in ("/data/crm.json", "/../data/crm.json", "/.env", "/docs/missing.md"):
            code, _, _ = self.request("GET", path)
            self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
