#!/usr/bin/env python3
"""End-to-end HTTP/MCP/RAG check; --live also calls real OpenRouter APIs."""

import argparse
import http.client
import json
import threading
from pathlib import Path

from assistant import SupportAssistant
from service import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    assistant = SupportAssistant(mode="cloud" if args.live else "demo")
    server = create_server(assistant, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        scenarios = [
            ("U-100", "T-100", "SSO_REQUIRED", ("sso", "единый вход")),
            ("U-200", "T-200", "ACCOUNT_LOCKED", ("15", "пятнадцать")),
            ("U-300", "T-300", "EMAIL_UNVERIFIED", ("подтверж",)),
        ]
        for user_id, ticket_id, error_code, expected in scenarios:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=240)
            try:
                connection.request("POST", "/api/ask", json.dumps({
                    "user_id": user_id, "ticket_id": ticket_id,
                    "question": "Почему не работает авторизация?"}).encode(),
                    {"Content-Type": "application/json"})
                response = connection.getresponse()
                result = json.loads(response.read())
                if response.status != 200:
                    raise RuntimeError(f"Smoke failed: HTTP {response.status}, {result.get('error', {}).get('code')}")
                if result["context"]["ticket"]["error_code"] != error_code:
                    raise RuntimeError("Wrong ticket context")
                if not any(error_code in source["text"] for source in result["sources"]):
                    raise RuntimeError("Matching product documentation was not retrieved")
                if not any(word in result["answer"].lower() for word in expected):
                    raise RuntimeError(f"Answer for {ticket_id} missed the documented remedy")
                if result["tools"][0]["name"] != "get_support_context":
                    raise RuntimeError("MCP evidence is missing")
                results.append(result)
                print(f"PASS {ticket_id} [{assistant.mode}] · MCP + RAG · {result['seconds']}s")
                print(result["answer"])
                print("Sources:", ", ".join(s["source"] for s in result["sources"]))
            finally:
                connection.close()
        if args.live and not assistant.knowledge.index.stats("structural")["chunks"]:
            raise RuntimeError("Vector index is empty")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(results)}/3 support scenarios passed")


if __name__ == "__main__":
    main()
