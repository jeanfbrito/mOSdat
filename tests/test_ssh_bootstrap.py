"""Unit tests for ``mosdat ssh-bootstrap`` / ``bootstrap_windows_ssh``.

Pure unit-level — no live VM, no network, no VLM. SSHClient, VncClient, and
VLMClient.localize/verify are replaced with recording stubs.
"""

from __future__ import annotations

import sys as _sys

for _name in list(_sys.modules):
    if (
        _name.startswith("automation.transport")
        or _name.startswith("automation.vlm")
        or _name in (
            "openai",
            "httpx",
            "automation.config",
            "automation.proxmox.api",
            "automation.proxmox.vm",
            "automation.reporting.report",
        )
    ):
        _sys.modules.pop(_name, None)

import argparse
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from automation.commands.doctor import CheckResult
from automation.commands import ssh_bootstrap as mod
from automation.commands.ssh_bootstrap import (
    ERR_LOCK,
    ERR_NO_SHELL,
    ERR_SSH_STILL_FAILS,
    NON_WINDOWS_ERROR,
    bootstrap_windows_ssh,
    exit_code_for,
    read_pubkey,
    run_ssh_bootstrap,
)


PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey test@host"


def _win_args(**kwargs):
    ns = dict(
        vm_name="windows10",
        vm_ip="192.168.13.87",
        vm_user="jean",
        vmid=103,
        proxmox=object(),
        vlm=MagicMock(),
        pubkey=PUBKEY,
    )
    ns.update(kwargs)
    return ns


def _mock_vnc(monkeypatch):
    vnc = MagicMock()
    vnc.capture.return_value = (MagicMock(name="screenshot"), (1920, 1080))
    vnc.__enter__.return_value = vnc
    vnc.__exit__.return_value = False
    cls = MagicMock(return_value=vnc)
    monkeypatch.setattr(mod, "VncClient", cls)
    return cls, vnc


def _verify_side_effect(*, locked=False, uac=True, elevated=True):
    def _verify(screenshot, question, temperature=0.0):
        q = question.lower()
        if "lock screen" in q or "sign-in" in q:
            return locked
        if "user account control" in q:
            return uac
        if "elevated" in q:
            return elevated
        return False

    return _verify


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)


# ---------------------------------------------------------------------------
# Core: already-working short-circuit
# ---------------------------------------------------------------------------


def test_already_working_ssh_never_touches_console(monkeypatch):
    monkeypatch.setattr(
        mod, "check_ssh", lambda ssh: CheckResult("SSH reachable", "PASS"),
    )
    vnc_cls, vnc = _mock_vnc(monkeypatch)
    vlm = MagicMock()
    result = bootstrap_windows_ssh(**_win_args(vlm=vlm), dry_run=False)
    assert result.ok is True
    assert result.already_working is True
    assert result.error == ""
    vnc_cls.assert_not_called()
    vnc.click.assert_not_called()
    vnc.type_text.assert_not_called()
    vlm.localize.assert_not_called()
    vlm.verify.assert_not_called()


# ---------------------------------------------------------------------------
# Core: lock-screen abort
# ---------------------------------------------------------------------------


def test_lock_screen_aborts_before_click_or_type(monkeypatch):
    monkeypatch.setattr(
        mod, "check_ssh",
        lambda ssh: CheckResult("SSH reachable", "FAIL", "Permission denied"),
    )
    vnc_cls, vnc = _mock_vnc(monkeypatch)
    vlm = MagicMock()
    vlm.verify.side_effect = _verify_side_effect(locked=True)
    result = bootstrap_windows_ssh(**_win_args(vlm=vlm), dry_run=False)
    assert result.ok is False
    assert result.already_working is False
    assert result.error == ERR_LOCK
    assert exit_code_for(result) == 2
    vnc_cls.assert_called_once()
    vnc.click.assert_not_called()
    vnc.type_text.assert_not_called()
    vlm.localize.assert_not_called()


def test_lock_screen_vlm_error_aborts_before_click(monkeypatch):
    monkeypatch.setattr(
        mod, "check_ssh",
        lambda ssh: CheckResult("SSH reachable", "FAIL", "Permission denied"),
    )
    _vnc_cls, vnc = _mock_vnc(monkeypatch)
    vlm = MagicMock()
    vlm.verify.side_effect = RuntimeError("vlm down")
    result = bootstrap_windows_ssh(**_win_args(vlm=vlm), dry_run=False)
    assert result.ok is False
    assert "aborting rather than typing blind" in result.error
    assert exit_code_for(result) == 2
    vnc.click.assert_not_called()
    vnc.type_text.assert_not_called()


# ---------------------------------------------------------------------------
# Core: happy path
# ---------------------------------------------------------------------------


def test_happy_path_ssh_fails_then_succeeds(monkeypatch):
    probes = iter([
        CheckResult("SSH reachable", "FAIL", "Permission denied"),
        CheckResult("SSH reachable", "PASS"),
    ])
    monkeypatch.setattr(mod, "check_ssh", lambda ssh: next(probes))
    vnc_cls, vnc = _mock_vnc(monkeypatch)
    vlm = MagicMock()
    vlm.verify.side_effect = _verify_side_effect(locked=False, uac=True, elevated=True)
    vlm.localize.side_effect = lambda screenshot, target, screen_size: {
        "search": (100, 1060),
        "Run as administrator": (400, 200),
        "Yes": (960, 540),
    }[
        "search" if "search box" in target
        else "Run as administrator" if "Run as administrator" in target
        else "Yes"
    ]
    result = bootstrap_windows_ssh(**_win_args(vlm=vlm), dry_run=False)
    assert result.ok is True
    assert result.already_working is False
    assert result.steps
    assert any("typed:" in s for s in result.steps)
    vnc_cls.assert_called_once()
    assert vnc.click.call_count == 3
    typed = [c.args[0] for c in vnc.type_text.call_args_list]
    assert typed[0] == "powershell"
    assert typed[1] == f"$key = '{PUBKEY}'"
    assert any("authorized_keys" in t for t in typed)
    assert any("administrators_authorized_keys" in t for t in typed)
    assert any("icacls.exe" in t for t in typed)
    enter_calls = [c for c in vnc.key.call_args_list if c.args and c.args[0] == "enter"]
    assert len(enter_calls) == 5
    localize_targets = [c.args[1] for c in vlm.localize.call_args_list]
    assert localize_targets[0] == "the taskbar search box"
    assert "Run as administrator" in localize_targets[1]
    assert localize_targets[2] == "the Yes button on the User Account Control dialog"
    verify_questions = [c.args[1] for c in vlm.verify.call_args_list]
    assert "lock screen" in verify_questions[0]
    assert "User Account Control" in verify_questions[1]
    assert "elevated Administrator PowerShell" in verify_questions[2]


def test_happy_path_no_uac_still_types_when_elevated(monkeypatch):
    probes = iter([
        CheckResult("SSH reachable", "FAIL", "denied"),
        CheckResult("SSH reachable", "PASS"),
    ])
    monkeypatch.setattr(mod, "check_ssh", lambda ssh: next(probes))
    _cls, vnc = _mock_vnc(monkeypatch)
    vlm = MagicMock()
    vlm.verify.side_effect = _verify_side_effect(locked=False, uac=False, elevated=True)
    vlm.localize.return_value = (10, 20)
    result = bootstrap_windows_ssh(**_win_args(vlm=vlm), dry_run=False)
    assert result.ok is True
    assert vnc.click.call_count == 2  # search + run-as-admin; no UAC Yes
    assert any("no UAC dialog" in s for s in result.steps)


# ---------------------------------------------------------------------------
# Core: console done but SSH still fails vs UI failure
# ---------------------------------------------------------------------------


def test_console_done_but_ssh_still_fails(monkeypatch):
    monkeypatch.setattr(
        mod, "check_ssh",
        lambda ssh: CheckResult("SSH reachable", "FAIL", "Permission denied"),
    )
    monkeypatch.setattr(mod, "_SSH_RETRY_DELAYS", ())
    _cls, vnc = _mock_vnc(monkeypatch)
    vlm = MagicMock()
    vlm.verify.side_effect = _verify_side_effect()
    vlm.localize.return_value = (10, 20)
    result = bootstrap_windows_ssh(**_win_args(vlm=vlm), dry_run=False)
    assert result.ok is False
    assert result.error == ERR_SSH_STILL_FAILS
    assert exit_code_for(result) == 1
    assert result.steps
    assert vnc.type_text.called
    assert ERR_NO_SHELL not in result.error
    assert ERR_LOCK not in result.error


def test_elevated_shell_not_confirmed_does_not_type_commands(monkeypatch):
    monkeypatch.setattr(
        mod, "check_ssh",
        lambda ssh: CheckResult("SSH reachable", "FAIL", "denied"),
    )
    _cls, vnc = _mock_vnc(monkeypatch)
    vlm = MagicMock()
    vlm.verify.side_effect = _verify_side_effect(elevated=False)
    vlm.localize.return_value = (10, 20)
    result = bootstrap_windows_ssh(**_win_args(vlm=vlm), dry_run=False)
    assert result.ok is False
    assert result.error == ERR_NO_SHELL
    assert exit_code_for(result) == 2
    typed = [c.args[0] for c in vnc.type_text.call_args_list]
    assert typed == ["powershell"]
    assert not any("$key" in t for t in typed)


def test_localize_failure_is_ui_error_not_ssh_error(monkeypatch):
    monkeypatch.setattr(
        mod, "check_ssh",
        lambda ssh: CheckResult("SSH reachable", "FAIL", "denied"),
    )
    _cls, vnc = _mock_vnc(monkeypatch)
    vlm = MagicMock()
    vlm.verify.side_effect = _verify_side_effect()
    vlm.localize.side_effect = RuntimeError("element not visible")
    result = bootstrap_windows_ssh(**_win_args(vlm=vlm), dry_run=False)
    assert result.ok is False
    assert "could not find UI element" in result.error
    assert result.error != ERR_SSH_STILL_FAILS
    assert exit_code_for(result) == 2
    vnc.type_text.assert_not_called()


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


def test_dry_run_skips_all_io(monkeypatch):
    ssh = MagicMock(side_effect=AssertionError("SSHClient must not be constructed"))
    monkeypatch.setattr(mod, "SSHClient", ssh)
    vnc_cls, _vnc = _mock_vnc(monkeypatch)
    vlm = MagicMock()
    result = bootstrap_windows_ssh(**_win_args(vlm=vlm), dry_run=True)
    assert result.ok is True
    assert result.already_working is False
    assert result.steps
    assert all(s.startswith("[dry-run]") for s in result.steps)
    ssh.assert_not_called()
    vnc_cls.assert_not_called()
    vlm.verify.assert_not_called()
    vlm.localize.assert_not_called()


# ---------------------------------------------------------------------------
# CLI: non-Windows / dry-run / missing VM
# ---------------------------------------------------------------------------


def _cfg(os_type="windows", name="windows10"):
    vm = SimpleNamespace(
        name=name,
        ip="192.168.13.87",
        user="jean",
        vmid=103,
        os_type=os_type,
    )
    return SimpleNamespace(vm_by_name={name: vm}, vms=[vm], proxmox=object(), vlm=SimpleNamespace(
        base_url="http://127.0.0.1:9",
        model="dummy",
        verify_model=None,
        api_key="",
        max_tokens_floor=16,
    ))


def test_run_rejects_non_windows_before_io(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_load_config", lambda path: _cfg(os_type="linux", name="ubuntu2404"))
    boot = MagicMock()
    monkeypatch.setattr(mod, "bootstrap_windows_ssh", boot)
    args = argparse.Namespace(
        vm="ubuntu2404", pubkey=None, dry_run=False, config=None,
    )
    rc = run_ssh_bootstrap(args)
    assert rc == 5
    boot.assert_not_called()


def test_run_rejects_unknown_vm(monkeypatch):
    monkeypatch.setattr(mod, "_load_config", lambda path: _cfg())
    boot = MagicMock()
    monkeypatch.setattr(mod, "bootstrap_windows_ssh", boot)
    args = argparse.Namespace(vm="nope", pubkey=None, dry_run=False, config=None)
    rc = run_ssh_bootstrap(args)
    assert rc == 5
    boot.assert_not_called()


def test_run_dry_run_returns_0(monkeypatch, tmp_path):
    pub = tmp_path / "id_ed25519.pub"
    pub.write_text(PUBKEY + "\n")
    monkeypatch.setattr(mod, "_load_config", lambda path: _cfg())
    args = argparse.Namespace(
        vm="windows10", pubkey=str(pub), dry_run=True, config=None,
    )
    rc = run_ssh_bootstrap(args)
    assert rc == 0


def test_read_pubkey_strips_and_rejects_quote(tmp_path):
    p = tmp_path / "id.pub"
    p.write_text(PUBKEY + "\n")
    assert read_pubkey(str(p)) == PUBKEY
    p.write_text("ssh-ed25519 AAAAfoo 'evil'\n")
    with pytest.raises(ValueError, match="single quote"):
        read_pubkey(str(p))


def test_add_ssh_bootstrap_subparser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    mod.add_ssh_bootstrap_subparser(sub)
    args = parser.parse_args(["ssh-bootstrap", "windows10", "--dry-run"])
    assert args.command == "ssh-bootstrap"
    assert args.vm == "windows10"
    assert args.dry_run is True
