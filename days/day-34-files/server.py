#!/usr/bin/env python3
"""Root-scoped project files over MCP stdio. Only JSON-RPC on stdout."""
import argparse
import json
import sys
from workspace import Workspace, FileError


def tool(name, description, properties=None, required=None):
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties or {}, "additionalProperties": False,
        "required": required or []}}


STRING = {"type": "string"}
TOOLS = [
    tool("list_files", "Discover readable project paths; generated reports are excluded."),
    tool("search_text", "Search literal text across project files. Returns file/line; inspect truncated/skipped.",
         {"query": STRING}, ["query"]),
    tool("read_file", "Read a UTF-8 file and remember its version before editing.", {"path": STRING}, ["path"]),
    tool("propose_file", "Stage Markdown creation/update without saving. Existing file must be read first.",
         {"path": STRING, "content": STRING}, ["path", "content"]),
    tool("show_diff", "Show actual unified diff for all staged files."),
]
APPLY = tool("apply_changes", "Apply staged changes if explicitly enabled by the operator.")


def dispatch(message, workspace):
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    if "id" not in message:
        return None
    reply = {"jsonrpc": "2.0", "id": message["id"]}
    method = message.get("method")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        reply["error"] = {"code": -32602, "message": "Invalid params"}
    elif method == "initialize":
        reply["result"] = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "project-files", "version": "1.0"}}
    elif method == "ping":
        reply["result"] = {}
    elif method == "tools/list":
        reply["result"] = {"tools": TOOLS + ([APPLY] if workspace.allow_apply else [])}
    elif method == "tools/call":
        try:
            names = {tool["name"] for tool in TOOLS} | {"apply_changes"}
            name, arguments = params.get("name"), params.get("arguments", {})
            if name not in names or not isinstance(arguments, dict):
                raise FileError("invalid_request", "Неизвестный инструмент или неверные аргументы.")
            result = getattr(workspace, name)(**arguments)
            reply["result"] = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
        except (FileError, TypeError, OSError) as exc:
            error = {"code": exc.code, "message": str(exc), **exc.details} if isinstance(exc, FileError) else {
                "code": "invalid_request", "message": "Аргументы или файловая операция недоступны."}
            reply["result"] = {"isError": True, "content": [{"type": "text", "text": json.dumps(error, ensure_ascii=False)}]}
    else:
        reply["error"] = {"code": -32601, "message": "Method not found"}
    return reply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--allow-apply", action="store_true")
    args = parser.parse_args()
    try:
        workspace = Workspace(args.project, allow_apply=args.allow_apply)
        for line in sys.stdin:
            try:
                reply = dispatch(json.loads(line), workspace)
            except json.JSONDecodeError:
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            if reply is not None:
                print(json.dumps(reply, ensure_ascii=False), flush=True)
    except (FileError, OSError):
        print("Workspace unavailable", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
