#!/usr/bin/env python3
"""TaskFlow support console. Local operator demo, not a public CRM API."""

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from assistant import SupportAssistant, SupportError

HERE = Path(__file__).resolve().parent
MAX_BODY = 16_384


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *_):
        # Questions and ticket data must not appear in access logs.
        pass

    def send_body(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        if status == 503:
            self.send_header("Retry-After", "3")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def json(self, status, data):
        self.send_body(status, json.dumps(data, ensure_ascii=False).encode())

    def fail(self, code, message, status=400):
        self.json(status, {"error": {"code": code, "message": message}})

    def local_request(self):
        port = self.server.server_port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed_hosts:
            self.fail("forbidden", "Недопустимый Host.", 403)
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {"http://" + host for host in allowed_hosts}:
            self.fail("forbidden", "Запросы с внешних сайтов не разрешены.", 403)
            return False
        return True

    def do_GET(self):
        if not self.local_request():
            return
        path = urlsplit(self.path).path
        if path == "/health":
            self.json(200, {"status": "ok", "mode": self.server.assistant.mode,
                            "documents": len(self.server.assistant.knowledge.documents),
                            "synthetic_data": True})
        elif path == "/":
            self.send_body(200, (HERE / "chat.html").read_bytes(), "text/html; charset=utf-8")
        elif path in {"/" + doc.path for doc in self.server.assistant.knowledge.documents}:
            self.send_body(200, (HERE / path.lstrip("/")).read_bytes(), "text/plain; charset=utf-8")
        else:
            self.fail("not_found", "Страница не найдена.", 404)

    def do_POST(self):
        if not self.local_request():
            return
        if self.path != "/api/ask":
            self.fail("not_found", "Эндпоинт не найден.", 404)
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.fail("unsupported_media_type", "Ожидается application/json.", 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.fail("invalid_request", "Некорректная длина запроса.")
            return
        if not 1 <= length <= MAX_BODY:
            self.fail("body_too_large", "Размер JSON должен быть от 1 до 16384 байт.", 413)
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, TimeoutError, OSError):
            self.fail("invalid_request", "Ожидается полный JSON-объект.")
            return
        if (not isinstance(payload, dict) or set(payload) - {"question", "user_id", "ticket_id"}
                or not {"question", "user_id"} <= set(payload)):
            self.fail("invalid_request", "Укажите question, user_id и необязательный ticket_id.")
            return
        if not self.server.gate.acquire(blocking=False):
            self.fail("busy", "Ассистент занят. Повторите запрос через несколько секунд.", 503)
            return
        try:
            answer = self.server.assistant.answer(**payload)
            self.json(200, answer)
        except SupportError as exc:
            self.fail(exc.code, str(exc), exc.status)
        except Exception:
            self.fail("internal_error", "Не удалось подготовить ответ.", 500)
        finally:
            self.server.gate.release()


def create_server(assistant, port=8033):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.assistant = assistant
    server.gate = threading.Lock()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Extractive demo: real MCP and local retrieval, no LLM")
    parser.add_argument("--port", type=int, default=8033)
    parser.add_argument("--question", help="Answer once as JSON instead of serving HTTP")
    parser.add_argument("--user", default="U-100")
    parser.add_argument("--ticket")
    args = parser.parse_args()
    assistant = SupportAssistant(mode="demo" if args.demo else "cloud")
    if args.question is not None:
        try:
            print(json.dumps(assistant.answer(args.question, args.user, args.ticket), ensure_ascii=False, indent=2))
            return 0
        except SupportError as exc:
            print(json.dumps({"error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
            return 1
    try:
        server = create_server(assistant, args.port)
    except (OSError, OverflowError, ValueError):
        print("Не удалось открыть локальный порт. Укажите другой --port.", file=sys.stderr)
        return 1
    print(f"TaskFlow support: http://127.0.0.1:{server.server_port} · {assistant.mode}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
