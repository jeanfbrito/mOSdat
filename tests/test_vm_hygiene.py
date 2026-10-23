"""Unit tests for ``mosdat vm-hygiene`` / ``vm_hygiene``.

Pure unit-level — no live VM, no network. SSHClient is replaced with a
recording stub whose ``run()`` dispatches on the encoded PowerShell script
contents (decoded from the base64 ``-EncodedCommand`` argument) so tests can
assert on human-readable substrings instead of raw base64.
"""

from __future__ import annotations

import argparse
import base64
import inspect
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from automation.commands import vm_hygiene as mod
from automation.commands.vm_hygiene import (
    ITEM_BACKUP_NAG,
    ITEM_CLOCK,
    ITEM_NEWS_WIDGET,
    ITEM_ONEDRIVE,
    NON_WINDOWS_ERROR,
    STATUS_ALREADY_OK,
    STATUS_DRY_RUN,
    STATUS_FAILED,
    STATUS_FIXED,
    add_vm_hygiene_subparser,
    exit_code_for,
    run_vm_hygiene,
    run_vm_hygiene_checks,
    vm_hygiene,
)
from automation.mcp_tools import TOOL_DEFINITIONS, _vm_hygiene


def _decode(cmd: str) -> str:
    """Extract and UTF-16LE-decode the ``-EncodedCommand`` payload."""
    marker = "-EncodedCommand "
    idx = cmd.index(marker) + len(marker)
    b64 = cmd[idx:].strip()
    return base64.b64decode(b64).decode("utf-16-le")


class FakeSSH:
    """Records every PowerShell script run (decoded) and returns canned results.

    ``responses`` maps a substring of the decoded script to an ``SSHResult``.
    First substring match wins; scripts with no match get a generic failure.
    """

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []

    def run(self, cmd: str, timeout=None):
        decoded = _decode(cmd)
        self.calls.append(decoded)
        for substr, result in self.responses.items():
            if substr in decoded:
                return result
        return SimpleNamespace(success=False, returncode=1, stdout="", stderr="no matching stub")


def _ok(stdout: str):
    return SimpleNamespace(success=True, returncode=0, stdout=stdout, stderr="")


def _fail(stderr: str = "boom"):
    return SimpleNamespace(success=False, returncode=1, stdout="", stderr=stderr)


# ---------------------------------------------------------------------------
# Idempotent no-op: every item already clean
# ---------------------------------------------------------------------------


def test_all_items_already_ok_when_clean(monkeypatch):
    now_epoch = int(time.time())
    ssh = FakeSSH({
        "EPOCH": _ok(f"EPOCH={now_epoch}\n"),
        "DisableFileSyncNGSC": _ok("VALUE=1\n"),
        "Get-ScheduledTask": _ok("STATE=Disabled\n"),
        "ShellFeedsTaskbarViewMode": _ok("FEEDS=2\nTASKBARDA=0\n"),
    })
    result = run_vm_hygiene_checks(ssh, "windows10")
    assert result.ok is True
    statuses = {i.item: i.status for i in result.items}
    assert statuses == {
        ITEM_CLOCK: STATUS_ALREADY_OK,
        ITEM_ONEDRIVE: STATUS_ALREADY_OK,
        ITEM_BACKUP_NAG: STATUS_ALREADY_OK,
        ITEM_NEWS_WIDGET: STATUS_ALREADY_OK,
    }
    # No Set-Date / Set-ItemProperty / Disable-ScheduledTask / Stop-Process calls.
    joined = "\n".join(ssh.calls)
    assert "Set-Date" not in joined
    assert "Set-ItemProperty" not in joined
    assert "Disable-ScheduledTask" not in joined
    assert "Stop-Process -Name explorer" not in joined


def test_backup_nag_task_not_found_is_already_ok():
    ssh = FakeSSH({
        "EPOCH": _ok(f"EPOCH={int(time.time())}\n"),
        "DisableFileSyncNGSC": _ok("VALUE=1\n"),
        "Get-ScheduledTask": _ok("STATE=NOTFOUND\n"),
        "ShellFeedsTaskbarViewMode": _ok("FEEDS=2\nTASKBARDA=0\n"),
    })
    result = run_vm_hygiene_checks(ssh, "windows10")
    item = result.item(ITEM_BACKUP_NAG)
    assert item.status == STATUS_ALREADY_OK
    assert "not present" in item.detail


# ---------------------------------------------------------------------------
# Each of the 4 fixes applied when not clean
# ---------------------------------------------------------------------------


def test_clock_fixed_when_drifted():
    stale_epoch = int(time.time()) - 3600  # 1h off — Focus-Assist-quiet-hours class of bug
    ssh = FakeSSH({
        "EPOCH": _ok(f"EPOCH={stale_epoch}\n"),
        "Set-Date": _ok("CLOCK_SET_OK\n"),
        "DisableFileSyncNGSC": _ok("VALUE=1\n"),
        "Get-ScheduledTask": _ok("STATE=Disabled\n"),
        "ShellFeedsTaskbarViewMode": _ok("FEEDS=2\nTASKBARDA=0\n"),
    })
    result = run_vm_hygiene_checks(ssh, "windows10")
    item = result.item(ITEM_CLOCK)
    assert item.status == STATUS_FIXED
    assert result.ok is True
    assert any("Set-Date" in c for c in ssh.calls)
    assert any("FromUnixTimeSeconds" in c and "ToLocalTime" in c for c in ssh.calls)


def test_onedrive_fixed_when_policy_absent():
    ssh = FakeSSH({
        "EPOCH": _ok(f"EPOCH={int(time.time())}\n"),
        "DisableFileSyncNGSC": _ok("VALUE=\n"),
        "Set-ItemProperty": _ok("ONEDRIVE_POLICY_OK\n"),
        "Get-ScheduledTask": _ok("STATE=Disabled\n"),
        "ShellFeedsTaskbarViewMode": _ok("FEEDS=2\nTASKBARDA=0\n"),
    })
    result = run_vm_hygiene_checks(ssh, "windows10")
    item = result.item(ITEM_ONEDRIVE)
    assert item.status == STATUS_FIXED
    fix_calls = [c for c in ssh.calls if "Set-ItemProperty" in c and "OneDrive" in c]
    assert any("DisableFileSyncNGSC" in c and "-Value 1" in c for c in fix_calls)
    assert any("Stop-Process -Force -ErrorAction SilentlyContinue" in c for c in ssh.calls if "OneDrive" in c)


def test_backup_nag_fixed_when_task_enabled():
    ssh = FakeSSH({
        "EPOCH": _ok(f"EPOCH={int(time.time())}\n"),
        "DisableFileSyncNGSC": _ok("VALUE=1\n"),
        "Get-ScheduledTask": _ok("STATE=Ready\n"),
        "Disable-ScheduledTask": _ok("BACKUP_TASK_DISABLED\n"),
        "ShellFeedsTaskbarViewMode": _ok("FEEDS=2\nTASKBARDA=0\n"),
    })
    result = run_vm_hygiene_checks(ssh, "windows10")
    item = result.item(ITEM_BACKUP_NAG)
    assert item.status == STATUS_FIXED
    assert any("Disable-ScheduledTask" in c and "ConfigNotification" in c for c in ssh.calls)


def test_news_widget_fixed_and_restarts_explorer():
    ssh = FakeSSH({
        "EPOCH": _ok(f"EPOCH={int(time.time())}\n"),
        "DisableFileSyncNGSC": _ok("VALUE=1\n"),
        "Get-ScheduledTask": _ok("STATE=Disabled\n"),
        "ShellFeedsTaskbarViewMode": _ok("FEEDS=\nTASKBARDA=\n"),
        "Stop-Process -Name explorer": _ok("NEWS_WIDGET_OK\n"),
    })
    result = run_vm_hygiene_checks(ssh, "windows10")
    item = result.item(ITEM_NEWS_WIDGET)
    assert item.status == STATUS_FIXED
    fix_call = next(c for c in ssh.calls if "Stop-Process -Name explorer" in c)
    assert "ShellFeedsTaskbarViewMode" in fix_call and "-Value 2" in fix_call
    assert "TaskbarDa" in fix_call and "-Value 0" in fix_call
    assert "Start-Process explorer" in fix_call


def test_one_item_fails_others_still_run_and_overall_not_ok():
    ssh = FakeSSH({
        "EPOCH": _fail("ssh timeout"),
        "DisableFileSyncNGSC": _ok("VALUE=1\n"),
        "Get-ScheduledTask": _ok("STATE=Disabled\n"),
        "ShellFeedsTaskbarViewMode": _ok("FEEDS=2\nTASKBARDA=0\n"),
    })
    result = run_vm_hygiene_checks(ssh, "windows10")
    assert result.ok is False
    assert result.item(ITEM_CLOCK).status == STATUS_FAILED
    assert result.item(ITEM_ONEDRIVE).status == STATUS_ALREADY_OK
    assert exit_code_for(result) == 2


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


def test_dry_run_skips_all_io(monkeypatch):
    ssh_cls = MagicMock(side_effect=AssertionError("SSHClient must not be constructed"))
    monkeypatch.setattr(mod, "SSHClient", ssh_cls, raising=False)
    result = vm_hygiene("windows10", "192.168.13.87", "jean", dry_run=True)
    assert result.ok is True
    assert len(result.items) == 4
    assert all(i.status == STATUS_DRY_RUN for i in result.items)
    ssh_cls.assert_not_called()


# ---------------------------------------------------------------------------
# CLI: Windows-only guard, unknown VM, dry-run, subparser wiring
# ---------------------------------------------------------------------------


def _cfg(os_type="windows", name="windows10"):
    vm = SimpleNamespace(
        name=name,
        ip="192.168.13.87",
        user="jean",
        vmid=103,
        os_type=os_type,
        is_windows=(os_type == "windows"),
    )
    return SimpleNamespace(vm_by_name={name: vm}, vms=[vm])


def test_run_rejects_non_windows_before_io(monkeypatch):
    monkeypatch.setattr(mod, "_load_config", lambda path: _cfg(os_type="linux", name="ubuntu2404"))
    hygiene = MagicMock()
    monkeypatch.setattr(mod, "vm_hygiene", hygiene)
    args = argparse.Namespace(vm="ubuntu2404", dry_run=False, config=None)
    rc = run_vm_hygiene(args)
    assert rc == 5
    hygiene.assert_not_called()


def test_run_rejects_unknown_vm(monkeypatch):
    monkeypatch.setattr(mod, "_load_config", lambda path: _cfg())
    hygiene = MagicMock()
    monkeypatch.setattr(mod, "vm_hygiene", hygiene)
    args = argparse.Namespace(vm="nope", dry_run=False, config=None)
    rc = run_vm_hygiene(args)
    assert rc == 5
    hygiene.assert_not_called()


def test_run_dry_run_returns_0(monkeypatch):
    monkeypatch.setattr(mod, "_load_config", lambda path: _cfg())
    args = argparse.Namespace(vm="windows10", dry_run=True, config=None)
    rc = run_vm_hygiene(args)
    assert rc == 0


def test_run_returns_2_when_an_item_fails(monkeypatch):
    monkeypatch.setattr(mod, "_load_config", lambda path: _cfg())
    monkeypatch.setattr(
        mod, "vm_hygiene",
        lambda *a, **k: mod.HygieneResult(
            vm="windows10",
            items=[mod.ItemResult(ITEM_CLOCK, STATUS_FAILED, "boom")],
        ),
    )
    args = argparse.Namespace(vm="windows10", dry_run=False, config=None)
    rc = run_vm_hygiene(args)
    assert rc == 2


def test_add_vm_hygiene_subparser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_vm_hygiene_subparser(sub)
    args = parser.parse_args(["vm-hygiene", "windows10", "--dry-run"])
    assert args.command == "vm-hygiene"
    assert args.vm == "windows10"
    assert args.dry_run is True


# ---------------------------------------------------------------------------
# MCP envelope + registration
# ---------------------------------------------------------------------------


def test_vm_hygiene_mcp_envelope_ok(monkeypatch):
    vm = SimpleNamespace(name="windows10", ip="192.168.13.87", user="jean", os_type="windows", is_windows=True)
    monkeypatch.setattr("automation.mcp_tools._resolve_vm", lambda name: (vm, None))
    # _vm_hygiene imports vm_hygiene lazily from automation.commands.vm_hygiene —
    # patch it there so the lazy import inside the handler picks it up.
    monkeypatch.setattr(
        mod, "vm_hygiene",
        lambda *a, **k: mod.HygieneResult(
            vm="windows10",
            items=[mod.ItemResult(ITEM_CLOCK, STATUS_ALREADY_OK, "drift +1s")],
        ),
    )
    result = _vm_hygiene({"vm": "windows10"})
    for key in ("ok", "error", "degraded", "vm", "items"):
        assert key in result
    assert result["ok"] is True
    assert result["error"] is None
    assert result["vm"] == "windows10"
    assert result["items"] == [{"item": ITEM_CLOCK, "status": STATUS_ALREADY_OK, "detail": "drift +1s"}]


def test_vm_hygiene_mcp_envelope_non_windows(monkeypatch):
    vm = SimpleNamespace(name="ubuntu2404", ip="192.168.13.10", user="jean", os_type="linux", is_windows=False)
    monkeypatch.setattr("automation.mcp_tools._resolve_vm", lambda name: (vm, None))
    result = _vm_hygiene({"vm": "ubuntu2404"})
    assert result["ok"] is False
    assert "non-Windows" in result["error"]


def test_vm_hygiene_mcp_envelope_unknown_vm(monkeypatch):
    err_envelope = {"ok": False, "error": "unknown VM 'nope' — available: windows10", "degraded": []}
    monkeypatch.setattr("automation.mcp_tools._resolve_vm", lambda name: (None, err_envelope))
    result = _vm_hygiene({"vm": "nope"})
    assert result is err_envelope


def test_vm_hygiene_registered_in_tool_definitions_and_dispatch():
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "mosdat_vm_hygiene" in names

    from automation import mcp_server

    assert "mosdat_vm_hygiene" in inspect.getsource(mcp_server.handle_tools_call)
    assert "_vm_hygiene" in inspect.getsource(mcp_server.handle_tools_call)


def test_non_windows_error_message_constant():
    assert "non-Windows" in NON_WINDOWS_ERROR
