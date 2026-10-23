"""MCP stdio hygiene: stdout carries JSON-RPC only.

Before the fix, ``build._log`` prints and child processes that inherit fd 1
went straight into the protocol stream (a strict client failed to parse the
``mosdat_deploy`` response, 2026-10-23).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_claim_protocol_stdout_moves_prints_and_children_to_stderr():
    code = (
        "import os, sys\n"
        "from automation.mcp_server import _claim_protocol_stdout\n"
        "out = _claim_protocol_stdout()\n"
        "print('python-noise')\n"
        "os.system('echo child-noise')\n"
        "out.write('{\"jsonrpc\": \"2.0\", \"id\": 1, \"result\": {}}\\n'); out.flush()\n"
    )
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    lines = p.stdout.splitlines()
    assert lines == ['{"jsonrpc": "2.0", "id": 1, "result": {}}']
    assert "python-noise" in p.stderr and "child-noise" in p.stderr


def test_server_answers_requests_but_not_notifications():
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    p = subprocess.run(
        [sys.executable, "-m", "automation.mcp_server"], cwd=ROOT,
        input="".join(json.dumps(m) + "\n" for m in msgs),
        capture_output=True, text=True, timeout=60,
    )
    ids = [json.loads(line)["id"] for line in p.stdout.splitlines()]
    assert ids == [1, 2]
