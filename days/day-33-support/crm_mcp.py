#!/usr/bin/env python3
"""Read-only synthetic CRM over MCP stdio; stdout contains protocol messages only."""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = {
    "name": "get_support_context",
    "description": "Read one synthetic user and optionally their own support ticket.",
    "inputSchema": {"type": "object", "additionalProperties": False,
                    "properties": {"user_id": {"type": "string"},
                                   "ticket_id": {"type": "string"}},
                    "required": ["user_id"]},
}
USER_FIELDS = ("id", "name", "plan", "auth_method", "account_status", "email_verified")
TICKET_FIELDS = ("id", "user_id", "status", "subject", "description", "error_code")


class CRMError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def get_context(data, arguments):
    if not isinstance(arguments, dict) or set(arguments) - {"user_id", "ticket_id"}:
        raise CRMError("invalid_request", "Некорректные аргументы MCP.")
    user_id, ticket_id = arguments.get("user_id"), arguments.get("ticket_id")
    if not isinstance(user_id, str) or not re.fullmatch(r"U-[0-9]{3}", user_id):
        raise CRMError("invalid_request", "user_id должен иметь вид U-100.")
    if ticket_id is not None and (
        not isinstance(ticket_id, str) or not re.fullmatch(r"T-[0-9]{3}", ticket_id)
    ):
        raise CRMError("invalid_request", "ticket_id должен иметь вид T-100.")
    user = next((u for u in data["users"] if u["id"] == user_id), None)
    if user is None:
        raise CRMError("not_found", "Пользователь не найден.")
    ticket = None
    if ticket_id:
        ticket = next((t for t in data["tickets"] if t["id"] == ticket_id), None)
        if ticket is None:
            raise CRMError("not_found", "Тикет не найден.")
        if ticket["user_id"] != user_id:
            raise CRMError("forbidden", "Тикет не принадлежит выбранному пользователю.")
    return {"user": {k: user[k] for k in USER_FIELDS},
            "ticket": {k: ticket[k] for k in TICKET_FIELDS} if ticket else None}


def dispatch(message, data):
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "Invalid request"}}
    if "id" not in message:
        return None
    reply = {"jsonrpc": "2.0", "id": message["id"]}
    method, params = message.get("method"), message.get("params") or {}
    if not isinstance(params, dict):
        reply["error"] = {"code": -32602, "message": "Invalid params"}
    elif method == "initialize":
        reply["result"] = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "taskflow-crm", "version": "1.0"}}
    elif method == "ping":
        reply["result"] = {}
    elif method == "tools/list":
        reply["result"] = {"tools": [TOOL]}
    elif method == "tools/call":
        try:
            if params.get("name") != TOOL["name"]:
                raise CRMError("invalid_request", "Неизвестный инструмент MCP.")
            result = get_context(data, params.get("arguments", {}))
            reply["result"] = {"structuredContent": result,
                               "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
        except CRMError as exc:
            reply["result"] = {"isError": True, "content": [{"type": "text", "text":
                json.dumps({"code": exc.code, "message": str(exc)}, ensure_ascii=False)}]}
    else:
        reply["error"] = {"code": -32601, "message": "Method not found"}
    return reply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=HERE / "data/crm.json")
    args = parser.parse_args()
    try:
        data = json.loads(args.data.read_text(encoding="utf-8"))
        if data.get("synthetic") is not True:
            raise ValueError("Only synthetic fixtures are supported")
        for line in sys.stdin:
            try:
                message = json.loads(line)
                response = dispatch(message, data)
            except json.JSONDecodeError:
                response = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700, "message": "Parse error"}}
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
    except (OSError, ValueError, KeyError, TypeError):
        print("CRM fixture is unavailable or invalid", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
