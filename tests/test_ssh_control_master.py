"""Tests for SSH ControlMaster multiplexing (Stage 3c).

These tests stay hermetic: no live SSH is required. They poke the private
arg-builder and mock subprocess.run for the close path.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from automation.transport.ssh import SSHClient


# ---------------------------------------------------------------- arg builder
def test_persistent_adds_control_path_args(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice", persistent=True)
    argv = ssh._ssh_args()
    joined = " ".join(argv)
    assert "ControlMaster=auto" in joined
    assert "ControlPath=" in joined
    assert "ControlPersist=60" in joined
    # remote target still last
    assert argv[-1] == "alice@10.0.0.1"


def test_non_persistent_omits_control_path_args(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice")
    argv = ssh._ssh_args()
    joined = " ".join(argv)
    assert "ControlMaster" not in joined
    assert "ControlPath" not in joined
    assert "ControlPersist" not in joined


def test_persistent_custom_control_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice", persistent=True, control_persist=120)
    argv = ssh._ssh_args()
    assert "ControlPersist=120" in " ".join(argv)


# ---------------------------------------------------------------- socket path
def test_control_path_stable_per_host_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    a = SSHClient("10.0.0.1", "alice", persistent=True)
    b = SSHClient("10.0.0.1", "alice", persistent=True)
    assert a._control_path == b._control_path
    assert a._control_path is not None


def test_control_path_differs_per_host(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    a = SSHClient("10.0.0.1", "alice", persistent=True)
    b = SSHClient("10.0.0.2", "alice", persistent=True)
    assert a._control_path != b._control_path


def test_control_path_differs_per_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    a = SSHClient("10.0.0.1", "alice", persistent=True)
    b = SSHClient("10.0.0.1", "bob", persistent=True)
    assert a._control_path != b._control_path


def test_control_path_under_linux_sun_path_limit(tmp_path, monkeypatch):
    """Linux unix sockets max sun_path = 108 bytes. Hashed name keeps us safe."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("some-fairly-long-hostname.example.internal", "alice", persistent=True)
    assert ssh._control_path is not None
    assert len(ssh._control_path.encode()) < 108


# ---------------------------------------------------------------- scp args
def test_scp_args_include_control_path_when_persistent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice", persistent=True)
    args = ssh._scp_args()
    assert "ControlMaster=auto" in " ".join(args)


def test_scp_args_omit_control_path_when_not_persistent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice")
    args = ssh._scp_args()
    assert "ControlMaster" not in " ".join(args)


# ---------------------------------------------------------------- teardown
def test_close_persistent_runs_ssh_O_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice", persistent=True)
    with mock.patch("automation.transport.ssh.subprocess.run") as run:
        ssh.close_persistent()
    assert run.called
    argv = run.call_args.args[0]
    assert argv[:3] == ["ssh", "-O", "exit"]
    assert "-S" in argv
    assert ssh._control_path in argv
    assert "alice@10.0.0.1" in argv


def test_close_persistent_noop_when_not_persistent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice")  # not persistent
    with mock.patch("automation.transport.ssh.subprocess.run") as run:
        ssh.close_persistent()
    run.assert_not_called()


def test_close_persistent_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice", persistent=True)
    with mock.patch("automation.transport.ssh.subprocess.run") as run:
        ssh.close_persistent()
        ssh.close_persistent()
    assert run.call_count == 2  # safe to call twice; no exception


def test_close_persistent_swallows_subprocess_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice", persistent=True)
    with mock.patch("automation.transport.ssh.subprocess.run", side_effect=RuntimeError("boom")):
        # Must not raise
        ssh.close_persistent()


# ---------------------------------------------------------------- context mgr
def test_context_manager_closes_on_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with mock.patch("automation.transport.ssh.subprocess.run") as run:
        with SSHClient("10.0.0.1", "alice", persistent=True) as ssh:
            assert ssh._control_path is not None
        # context exit should have invoked close_persistent
        assert run.called
        argv = run.call_args.args[0]
        assert argv[:3] == ["ssh", "-O", "exit"]


# ---------------------------------------------------------------- run() integration
def test_run_uses_control_path_args_when_persistent(tmp_path, monkeypatch):
    """The ssh argv passed to subprocess.run must include the control opts."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice", persistent=True)

    fake_result = mock.Mock(returncode=0, stdout="OK\n", stderr="")
    with mock.patch("automation.transport.ssh.subprocess.run", return_value=fake_result) as run:
        ssh.run("echo OK", timeout=5)
    argv = run.call_args.args[0]
    joined = " ".join(argv)
    assert "ControlMaster=auto" in joined
    assert "ControlPath=" in joined


def test_run_omits_control_path_args_when_not_persistent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice")

    fake_result = mock.Mock(returncode=0, stdout="OK\n", stderr="")
    with mock.patch("automation.transport.ssh.subprocess.run", return_value=fake_result) as run:
        ssh.run("echo OK", timeout=5)
    argv = run.call_args.args[0]
    assert "ControlMaster" not in " ".join(argv)


# ---------------------------------------------------------------- stdin isolation
# The MCP server is a stdio JSON-RPC process. An ssh/scp child that inherits
# its stdin can read the client's next request bytes (c8883777), so every
# child must get stdin=DEVNULL.
def test_run_passes_stdin_devnull(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice")
    fake_result = mock.Mock(returncode=0, stdout="OK\n", stderr="")
    with mock.patch("automation.transport.ssh.subprocess.run", return_value=fake_result) as run:
        ssh.run("echo OK", timeout=5)
    assert run.call_args.kwargs["stdin"] is subprocess.DEVNULL
    # A non-UTF-8 byte from a Windows console must not raise.
    assert run.call_args.kwargs["errors"] == "replace"


def test_run_stream_output_passes_stdin_devnull(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice")
    proc = mock.Mock(returncode=0, stdout=iter(["line\n"]))
    with mock.patch("automation.transport.ssh.subprocess.Popen", return_value=proc) as popen:
        result = ssh.run("echo line", stream_output=True)
    assert popen.call_args.kwargs["stdin"] is subprocess.DEVNULL
    assert result.stdout == "line\n"


@pytest.mark.parametrize("method,args", [
    ("scp_to", (Path("/tmp/a"), "/tmp/b")),
    ("scp_from", ("/tmp/b", Path("/tmp/a"))),
    ("scp_dir_to", (Path("/tmp/a"), "/tmp/b")),
])
def test_scp_methods_pass_stdin_devnull(tmp_path, monkeypatch, method, args):
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh = SSHClient("10.0.0.1", "alice")
    fake_result = mock.Mock(returncode=0, stdout="", stderr="")
    with mock.patch("automation.transport.ssh.subprocess.run", return_value=fake_result) as run:
        getattr(ssh, method)(*args)
    assert run.call_args.args[0][0] == "scp"
    assert run.call_args.kwargs["stdin"] is subprocess.DEVNULL
