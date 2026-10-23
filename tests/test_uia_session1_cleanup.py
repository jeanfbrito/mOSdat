"""Regression: the Session-1 task cleanup must survive the VM's PowerShell SSH shell.

The cleanup string is sent as ``powershell -Command "..."``. The Windows
OpenSSH default shell on the lab VMs is PowerShell, which expands ``$``
variables inside the outer double quotes before the inner PowerShell runs.
``2>$null`` became ``2>`` and every cleanup failed to parse, leaking one
``mosdat-uia-*`` scheduled task per call (336 on windows10, 2026-10-23).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

for _name in list(sys.modules):
    if _name == "automation.uia" or _name.startswith("automation.uia."):
        sys.modules.pop(_name, None)

from automation.uia.client import UiaClient  # noqa: E402


def test_session1_cleanup_command_has_no_dollar_sign(monkeypatch, tmp_path):
    ssh = MagicMock()
    ssh.scp_to.return_value = SimpleNamespace(success=True, stderr="")
    calls = []

    def _run(cmd, timeout=None):
        calls.append(cmd)
        if "Test-Path" in cmd:
            return SimpleNamespace(success=True, stdout='{"ok": true}', stderr="", returncode=0)
        return SimpleNamespace(success=True, stdout="", stderr="", returncode=0)

    ssh.run.side_effect = _run
    client = UiaClient(ssh, use_session1=True, session1_poll_interval=0)
    client._run_batch_session1('{"ops": []}', timeout=1)

    cleanup = [c for c in calls if "schtasks /Delete" in c and c.startswith("powershell")]
    assert cleanup, calls
    assert "$" not in cleanup[-1]
