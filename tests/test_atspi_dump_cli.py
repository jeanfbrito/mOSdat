"""Unit tests for ``mosdat atspi-dump`` (Stage 3b).

All tests stub SSHClient + AtspiClient + load_config so they run without
a live VM or AT-SPI bus. The handler under test is
``automation.commands.atspi_dump.cmd_atspi_dump``.
"""
from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub heavy deps before importing the handler
# ---------------------------------------------------------------------------
# Mirror tests/test_trace.py teardown pattern so stub pollution doesn't bleed
# into sibling tests that depend on real automation.config / .transport.ssh.
_STUBBED_ORIGINALS: dict[str, object] = {}
for _mod in list(sys.modules):
    if any(_mod.startswith(p) for p in [
        "automation.atspi",
        "automation.transport.ssh",
        "automation.config",
        "automation.commands.atspi_dump",
    ]):
        _STUBBED_ORIGINALS.setdefault(_mod, sys.modules.get(_mod))
        sys.modules.pop(_mod, None)


class _FakeSSHResult:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.success = returncode == 0


class _FakeSSHClient:
    def __init__(self, host, user, *args, **kwargs):
        self.host = host
        self.user = user

    def run(self, cmd, timeout=None, **kwargs):
        return _FakeSSHResult(stdout="{}", returncode=0)

    def scp_to(self, local, remote):
        return _FakeSSHResult(stdout="", returncode=0)


_ssh_mod = types.ModuleType("automation.transport.ssh")
_ssh_mod.SSHClient = _FakeSSHClient
_ssh_mod.SSHResult = _FakeSSHResult
sys.modules["automation.transport.ssh"] = _ssh_mod


class _FakeVM:
    name = "ubuntu2204"
    ip = "192.168.1.100"
    user = "jean"
    vmid = 105
    is_windows = False


class _FakeWinVM:
    name = "win11"
    ip = "192.168.1.101"
    user = "jean"
    vmid = 106
    is_windows = True


class _FakeConfig:
    def __init__(self, vms: dict | None = None):
        self.vm_by_name = vms or {"ubuntu2204": _FakeVM()}


_config_mod = types.ModuleType("automation.config")
_config_mod.load_config = lambda path: _FakeConfig()
sys.modules["automation.config"] = _config_mod


# Stub the atspi package: client + error class. We don't want
# importing atspi_dump to drag in real worker.py / transport machinery.
class _StubAtspiError(RuntimeError):
    def __init__(self, message, *, result=None, stderr=None):
        super().__init__(message)
        self.result = result
        self.stderr = stderr


class _StubAtspiClient:
    """Per-test instances are configured via class-level attribute injection.

    Tests assign ``_StubAtspiClient.next_nodes`` / ``next_error`` before
    calling the handler.
    """
    next_nodes: list[dict] | None = None
    next_error: Exception | None = None
    last_call_kwargs: dict = {}

    def __init__(self, ssh, app_filter="rocket", **kwargs):
        self.ssh = ssh
        self.app_filter = app_filter

    def tree_dump(self, *, max_depth=30, app_filter=None, timeout=None):
        _StubAtspiClient.last_call_kwargs = {
            "max_depth": max_depth,
            "app_filter": app_filter,
            "timeout": timeout,
        }
        if _StubAtspiClient.next_error is not None:
            raise _StubAtspiClient.next_error
        return list(_StubAtspiClient.next_nodes or [])


_atspi_pkg = types.ModuleType("automation.atspi")
_atspi_pkg.AtspiClient = _StubAtspiClient
_atspi_pkg.AtspiError = _StubAtspiError
sys.modules["automation.atspi"] = _atspi_pkg


# Now import the module under test.
from automation.commands.atspi_dump import (  # noqa: E402
    cmd_atspi_dump,
    _format_tree,
    _format_json,
    _format_roles,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_stub_state():
    _StubAtspiClient.next_nodes = None
    _StubAtspiClient.next_error = None
    _StubAtspiClient.last_call_kwargs = {}
    yield


def _sample_nodes() -> list[dict]:
    """Realistic tree_dump output: a frame with two buttons + an entry.

    Includes 6 push buttons total so the roles histogram has a clear winner.
    """
    return [
        {"role": "frame", "name": "Rocket.Chat", "n_actions": 0,
         "action_names": [], "child_count": 8, "path": "", "depth": 0},
        {"role": "push button", "name": "Login", "n_actions": 1,
         "action_names": ["click"], "child_count": 0, "path": "/0", "depth": 1},
        {"role": "push button", "name": "Sign up", "n_actions": 1,
         "action_names": ["click"], "child_count": 0, "path": "/1", "depth": 1},
        {"role": "push button", "name": "Forgot password", "n_actions": 1,
         "action_names": ["click"], "child_count": 0, "path": "/2", "depth": 1},
        {"role": "push button", "name": "Connect", "n_actions": 1,
         "action_names": ["click"], "child_count": 0, "path": "/3", "depth": 1},
        {"role": "push button", "name": "Cancel", "n_actions": 1,
         "action_names": ["click"], "child_count": 0, "path": "/4", "depth": 1},
        {"role": "push button", "name": "OK", "n_actions": 1,
         "action_names": ["click"], "child_count": 0, "path": "/5", "depth": 1},
        {"role": "entry", "name": "username", "n_actions": 0,
         "action_names": [], "child_count": 0, "path": "/6", "depth": 1},
        {"role": "label", "name": "Welcome", "n_actions": 0,
         "action_names": [], "child_count": 0, "path": "/7", "depth": 1},
    ]


def _make_args(
    *,
    vms="ubuntu2204",
    output=None,
    fmt="tree",
    max_depth=30,
    app_filter="rocket",
    raw=False,
    config_exists=True,
    tmp_path: Path | None = None,
) -> object:
    if config_exists:
        # Use a real existing file so the .exists() check passes.
        cfg = (tmp_path / "fake.toml") if tmp_path else Path("/tmp/fake.toml")
        if tmp_path:
            cfg.write_text("# stub\n")
    else:
        cfg = Path("/nonexistent/path/to/fake.toml")

    ns = types.SimpleNamespace(
        config=cfg,
        vms=vms,
        output=output,
        max_depth=max_depth,
        app_filter=app_filter,
        format=fmt,
        raw=raw,
    )
    return ns


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_atspi_dump_tree_format(tmp_path, capsys):
    _StubAtspiClient.next_nodes = _sample_nodes()
    args = _make_args(fmt="tree", tmp_path=tmp_path)

    rc = cmd_atspi_dump(args)

    assert rc == 0
    out = capsys.readouterr().out
    # Each line is "[role] name (actions=N)"
    assert "[push button] Login (actions=1)" in out
    assert "[frame] Rocket.Chat (actions=0)" in out
    # Indented tree connector for at least one child line
    assert "├─" in out or "└─" in out


def test_atspi_dump_json_format(tmp_path, capsys):
    nodes = _sample_nodes()
    _StubAtspiClient.next_nodes = nodes
    args = _make_args(fmt="json", tmp_path=tmp_path)

    rc = cmd_atspi_dump(args)

    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert parsed == nodes


def test_atspi_dump_roles_histogram(tmp_path, capsys):
    _StubAtspiClient.next_nodes = _sample_nodes()
    args = _make_args(fmt="roles", tmp_path=tmp_path)

    rc = cmd_atspi_dump(args)

    assert rc == 0
    out = capsys.readouterr().out
    # 6 push buttons in fixture; sorted by count desc
    assert "push button: 6" in out
    assert "frame: 1" in out
    assert "entry: 1" in out
    # push button (highest count) must appear before frame (count=1)
    assert out.index("push button: 6") < out.index("frame: 1")


def test_atspi_dump_writes_file(tmp_path, capsys):
    _StubAtspiClient.next_nodes = _sample_nodes()
    out_path = tmp_path / "tree.json"
    args = _make_args(fmt="json", output=out_path, tmp_path=tmp_path)

    rc = cmd_atspi_dump(args)

    assert rc == 0
    assert out_path.exists()
    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed == _sample_nodes()
    # Status line goes to stderr, stdout should be empty
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "wrote" in captured.err


def test_atspi_dump_unknown_vm(tmp_path, capsys):
    args = _make_args(vms="nosuchvm", tmp_path=tmp_path)
    rc = cmd_atspi_dump(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "unknown VM" in err


def test_atspi_dump_rejects_windows(tmp_path, capsys, monkeypatch):
    # Swap config to one with a Windows VM only. Patch the binding inside
    # the handler module since it imported load_config by name at load time.
    fake_cfg = _FakeConfig(vms={"win11": _FakeWinVM()})
    import automation.commands.atspi_dump as mod
    monkeypatch.setattr(mod, "load_config", lambda p: fake_cfg)

    args = _make_args(vms="win11", tmp_path=tmp_path)
    rc = cmd_atspi_dump(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "Linux VMs" in err or "AT-SPI is GNOME" in err


def test_atspi_dump_atspi_error_clean_exit(tmp_path, capsys):
    _StubAtspiClient.next_error = _StubAtspiError("worker missing")
    args = _make_args(tmp_path=tmp_path)

    # Patch the AtspiError reference in the handler module so the except
    # clause matches our stub error class.
    import automation.commands.atspi_dump as mod
    with patch.object(mod, "AtspiError", _StubAtspiError):
        rc = cmd_atspi_dump(args)

    assert rc == 1
    err = capsys.readouterr().err
    assert "AT-SPI call failed" in err
    # No traceback unless MOSDAT_ATSPI_DUMP_DEBUG
    assert "Traceback" not in err


def test_atspi_dump_passes_max_depth_and_filter(tmp_path):
    _StubAtspiClient.next_nodes = _sample_nodes()
    args = _make_args(
        fmt="json", max_depth=12, app_filter="firefox", tmp_path=tmp_path,
    )
    rc = cmd_atspi_dump(args)
    assert rc == 0
    assert _StubAtspiClient.last_call_kwargs["max_depth"] == 12
    assert _StubAtspiClient.last_call_kwargs["app_filter"] == "firefox"


def test_atspi_dump_raw_uses_empty_filter(tmp_path):
    _StubAtspiClient.next_nodes = _sample_nodes()
    args = _make_args(raw=True, tmp_path=tmp_path)
    rc = cmd_atspi_dump(args)
    assert rc == 0
    assert _StubAtspiClient.last_call_kwargs["app_filter"] == ""


# ---------------------------------------------------------------------------
# Formatter unit tests (pure functions)
# ---------------------------------------------------------------------------


def test_format_tree_empty():
    assert "empty tree" in _format_tree([])


def test_format_json_round_trips():
    nodes = _sample_nodes()
    assert json.loads(_format_json(nodes)) == nodes


def test_format_roles_sort_ties_alphabetical():
    nodes = [
        {"role": "alpha", "name": "", "n_actions": 0, "path": "", "depth": 0},
        {"role": "beta", "name": "", "n_actions": 0, "path": "/0", "depth": 1},
    ]
    out = _format_roles(nodes)
    # Tied counts: alpha sorts before beta
    assert out.index("alpha") < out.index("beta")
