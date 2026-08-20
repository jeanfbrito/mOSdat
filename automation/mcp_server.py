#!/usr/bin/env python3
"""
mOSdat MCP Server — Hermes-native tools for VM orchestration + VLM testing.

Protocol: JSON-RPC 2.0 over stdio (MCP stdio transport).
Discovery: Hermes lists tools via tools/list, calls via tools/call.

Usage:
  mosdat mcp              # via CLI entry point
  python -m automation.mcp_server  # direct
"""

import json
import sys
from pathlib import Path
from typing import Any

from automation.mcp_tools import (
    TOOL_DEFINITIONS,
    _build,
    _deploy,
    _list_scenarios,
    _list_vms,
    _readiness,
    _run_functional,
    _ssh_bootstrap,
    _run_smoke,
    _ssh_tool,
    _vm_start,
    _vm_status,
    _vm_stop,
)

FRAMEWORK = Path(__file__).parent.parent

# ── MCP protocol helpers ────────────────────────────────────────────────────

Request = dict[str, Any]


def jsonrpc_error(req: Request, code: int, message: str, data: Any = None) -> str:
    resp = {
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "error": {"code": code, "message": message},
    }
    if data is not None:
        resp["error"]["data"] = str(data)[:2000]
    return json.dumps(resp) + "\n"


def jsonrpc_result(req: Request, result: Any) -> str:
    """Return a JSON-RPC result.

    MCP uses raw result objects for initialize/tools/list, but tools/call must
    return a CallToolResult shape: {content: [{type: "text", text: "..."}]}.
    """
    needs_wrap = req.get("method") == "tools/call" and not (
        isinstance(result, dict) and "content" in result
    )
    if needs_wrap:
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        result = {"content": [{"type": "text", "text": text}]}

    resp = {
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": result,
    }
    return json.dumps(resp) + "\n"


def jsonrpc_notification(method: str, params: dict = None) -> str:
    msg = {"jsonrpc": "2.0", "method": method}
    if params:
        msg["params"] = params
    return json.dumps(msg) + "\n"


# ── Request handlers ────────────────────────────────────────────────────────


def handle_initialize(req: Request) -> str:
    """MCP initialization handshake."""
    return jsonrpc_result(
        req,
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "mosdat", "version": "0.2.0"},
        },
    )


def handle_tools_list(req: Request) -> str:
    return jsonrpc_result(req, {"tools": TOOL_DEFINITIONS})


def handle_tools_call(req: Request) -> str:
    params = req.get("params", {})
    name = params.get("name", "")
    args = params.get("arguments", {})

    # Partial helpers bound to this request's protocol fns
    ok = jsonrpc_result
    err = jsonrpc_error

    try:
        if name == "mosdat_list_vms":
            return jsonrpc_result(req, _list_vms(args))
        elif name == "mosdat_vm_start":
            return _vm_start(req, args["vm_name"], ok)
        elif name == "mosdat_vm_stop":
            return _vm_stop(req, args["vm_name"], ok)
        elif name == "mosdat_vm_status":
            return _vm_status(req, args["vm_name"], ok)
        elif name == "mosdat_build":
            return jsonrpc_result(req, _build(args))
        elif name == "mosdat_deploy":
            return jsonrpc_result(req, _deploy(args))
        elif name == "mosdat_run_smoke":
            return _run_smoke(req, args["vm_name"], args.get("timeout", 120), jsonrpc_result=ok, jsonrpc_error=err)
        elif name == "mosdat_run_functional":
            return jsonrpc_result(req, _run_functional(args))
        elif name == "mosdat_list_scenarios":
            return jsonrpc_result(req, _list_scenarios(args))
        elif name == "mosdat_readiness":
            return jsonrpc_result(req, _readiness(args))
        elif name == "mosdat_ssh_bootstrap":
            return jsonrpc_result(req, _ssh_bootstrap(args))
        elif name == "mosdat_ssh":
            return _ssh_tool(req, args["vm_name"], args["command"], args.get("timeout", 30), jsonrpc_result=ok)
        else:
            return jsonrpc_error(req, -32601, f"Unknown tool: {name}")
    except Exception as e:
        import traceback

        return jsonrpc_error(req, -32000, str(e), traceback.format_exc())


# ── Main loop ────────────────────────────────────────────────────────────────

HANDLERS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
}


def main():
    """Run MCP server: read JSON-RPC from stdin, write responses to stdout."""
    import os

    os.chdir(str(FRAMEWORK))

    print("[mcp] mOSdat MCP server starting", file=sys.stderr)
    print(f"[mcp] Framework: {FRAMEWORK}", file=sys.stderr)
    print(f"[mcp] Python: {sys.version.split()[0]}", file=sys.stderr)

    buffer = ""
    for line in sys.stdin:
        if not line:
            continue
        buffer += line
        while buffer.strip():
            try:
                req = json.loads(buffer.strip())
                buffer = ""
            except json.JSONDecodeError:
                buffer += sys.stdin.readline()
                continue

            method = req.get("method", "")
            handler = HANDLERS.get(method)
            if handler:
                response = handler(req)
                sys.stdout.write(response)
                sys.stdout.flush()
            else:
                sys.stdout.write(jsonrpc_error(req, -32601, f"Method not found: {method}"))
                sys.stdout.flush()

        sys.stdout.flush()


if __name__ == "__main__":
    main()
