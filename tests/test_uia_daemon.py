"""Tests for the persistent UIA daemon transport.

Covers the host-side `UiaClient` daemon path and the SSH transport's
port-forward support. Worker `run_daemon` is mocked at the socket layer —
no Windows VM, no real pywinauto, no real SSH required.
"""
from __future__ import annotations

import importlib
import json
import socket
import sys
import threading
import time
from unittest import mock
from unittest.mock import MagicMock

import pytest

# `tests/test_atspi_dump_cli.py` poisons sys.modules["automation.transport.ssh"]
# with a stub class. Force a real reload so our import binds to the actual
# SSHClient, regardless of collection order.
for _name in list(sys.modules):
    if _name == "automation.transport.ssh" or _name == "automation.uia.client":
        sys.modules.pop(_name, None)
import automation.transport.ssh as _ssh_real_mod  # noqa: E402
_ssh_real_mod = importlib.reload(_ssh_real_mod) if not hasattr(
    _ssh_real_mod, "SSHClient"
) or not hasattr(_ssh_real_mod.SSHClient, "_ssh_args") else _ssh_real_mod
# If reload still yields a stub (e.g. another conftest re-stubs), import the
# file directly.
if not hasattr(_ssh_real_mod.SSHClient, "_ssh_args"):
    import importlib.util
    from pathlib import Path
    _proj = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "_real_ssh_for_daemon_tests",
        _proj / "automation" / "transport" / "ssh.py",
    )
    _real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_real)  # type: ignore[union-attr]
    sys.modules["automation.transport.ssh"] = _real
    _ssh_real_mod = _real

SSHClient = _ssh_real_mod.SSHClient

# Same defensive reload for the client module.
for _name in list(sys.modules):
    if _name.startswith("automation.uia"):
        sys.modules.pop(_name, None)
from automation.uia.client import UiaClient, UiaError  # noqa: E402


# ---------------------------------------------------------------------------
# SSHClient.add_port_forward / -L arg shape
# ---------------------------------------------------------------------------

class TestPortForward:

    def test_port_forward_in_ssh_args(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        ssh = SSHClient(
            "10.0.0.1", "alice",
            persistent=True,
            port_forwards=[(55001, "127.0.0.1", 5555)],
        )
        joined = " ".join(ssh._ssh_args())
        assert "-L" in joined
        assert "55001:127.0.0.1:5555" in joined

    def test_port_forward_in_scp_args(self, tmp_path, monkeypatch):
        # SCP shares the same ControlMaster, so the -L need only appear once
        # on the ssh side. scp itself does not need -L. Just confirm the
        # forward kwarg does not blow up scp arg construction.
        monkeypatch.setenv("HOME", str(tmp_path))
        ssh = SSHClient(
            "10.0.0.1", "alice",
            persistent=True,
            port_forwards=[(55001, "127.0.0.1", 5555)],
        )
        # scp args should NOT carry -L
        scp_joined = " ".join(ssh._scp_args())
        assert "ControlMaster=auto" in scp_joined

    def test_add_port_forward_appends(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        ssh = SSHClient("10.0.0.1", "alice", persistent=True)
        assert "-L" not in " ".join(ssh._ssh_args())
        ssh.add_port_forward(60001, "127.0.0.1", 7777)
        joined = " ".join(ssh._ssh_args())
        assert "-L" in joined
        assert "60001:127.0.0.1:7777" in joined

    def test_no_forwards_no_L_flag(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        ssh = SSHClient("10.0.0.1", "alice", persistent=True)
        assert "-L" not in ssh._ssh_args()


# ---------------------------------------------------------------------------
# UiaClient: setup idempotency + dispatch
# ---------------------------------------------------------------------------

def _make_daemon_client(*, local_port: int = 0) -> tuple[UiaClient, MagicMock]:
    ssh = MagicMock()
    # _pick_free_local_port runs at construction — give it a real socket bind
    # so the value is sensible. Or pass an explicit local_forward_port.
    if local_port == 0:
        # use ephemeral
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        local_port = s.getsockname()[1]
        s.close()
    client = UiaClient(
        ssh,
        use_daemon=True,
        local_forward_port=local_port,
        daemon_port=5555,
    )
    client._worker_deployed = True  # skip scp of worker
    return client, ssh


class TestDaemonSetupIdempotent:

    def test_setup_runs_once(self, monkeypatch):
        client, ssh = _make_daemon_client()
        # Pretend setup_daemon.ps1 exists.
        import automation.uia.client as _client_mod
        monkeypatch.setattr(
            _client_mod, "_LOCAL_SETUP_DAEMON_PATH",
            mock.MagicMock(exists=lambda: True),
        )
        ssh.scp_to.return_value = MagicMock(success=True, returncode=0,
                                            stdout="", stderr="")
        ssh.run.return_value = MagicMock(
            success=True, returncode=0,
            stdout="DAEMON_LISTENING_PORT_5555\n", stderr="",
        )

        # Start a tiny TCP server on local_port so the probe loop succeeds.
        srv = socket.socket()
        srv.bind(("127.0.0.1", client._local_port))
        srv.listen(1)
        try:
            client._ensure_daemon_setup()
            assert client._daemon_setup_done is True
            scp_calls_after_first = ssh.scp_to.call_count
            run_calls_after_first = ssh.run.call_count
            # Second call: must short-circuit
            client._ensure_daemon_setup()
            assert ssh.scp_to.call_count == scp_calls_after_first
            assert ssh.run.call_count == run_calls_after_first
        finally:
            srv.close()

    def test_setup_failure_does_not_mark_done(self, monkeypatch):
        client, ssh = _make_daemon_client()
        import automation.uia.client as _client_mod
        monkeypatch.setattr(
            _client_mod, "_LOCAL_SETUP_DAEMON_PATH",
            mock.MagicMock(exists=lambda: True),
        )
        ssh.scp_to.return_value = MagicMock(success=True, returncode=0,
                                            stdout="", stderr="")
        ssh.run.return_value = MagicMock(
            success=False, returncode=1,
            stdout="", stderr="something exploded",
        )
        with pytest.raises(UiaError, match="setup_daemon.ps1 failed"):
            client._ensure_daemon_setup()
        assert client._daemon_setup_done is False


class TestDaemonRequestViaSocket:
    """Spin up a real loopback TCP server that mimics the daemon. Verify the
    client sends the JSON payload framed with `\\n` and parses the response.
    """

    def _start_fake_daemon(self, *, response: bytes) -> tuple[int,
                                                              threading.Thread,
                                                              list]:
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.listen(1)
        received: list[bytes] = []

        def serve():
            try:
                conn, _ = srv.accept()
                buf = b""
                conn.settimeout(2.0)
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                received.append(buf)
                conn.sendall(response)
                conn.close()
            finally:
                try:
                    srv.close()
                except Exception:
                    pass

        th = threading.Thread(target=serve, daemon=True)
        th.start()
        return port, th, received

    def test_run_batch_daemon_round_trip(self):
        response = (
            json.dumps({"ok": True, "results": {"1": {"ok": True}}}).encode()
            + b"\n"
        )
        port, th, received = self._start_fake_daemon(response=response)

        client, _ = _make_daemon_client(local_port=port)
        client._daemon_setup_done = True  # skip setup

        result = client.run_batch(
            [{"id": "1", "op": "find", "role": "Button"}],
            timeout=5,
        )
        th.join(timeout=3.0)
        assert result["ok"] is True
        assert result["results"]["1"]["ok"] is True
        # Request was line-terminated and JSON.
        assert received[0].endswith(b"\n")
        sent = json.loads(received[0].decode().rstrip("\n"))
        assert sent["ops"][0]["op"] == "find"

    def test_daemon_garbage_response_raises(self):
        port, th, _ = self._start_fake_daemon(response=b"not-json\n")
        client, _ = _make_daemon_client(local_port=port)
        client._daemon_setup_done = True
        with pytest.raises(UiaError, match="parse worker response"):
            client.run_batch(
                [{"id": "1", "op": "find", "role": "Button"}],
                timeout=5,
            )
        th.join(timeout=3.0)

    def test_daemon_empty_response_raises(self):
        # Closes immediately.
        port, th, _ = self._start_fake_daemon(response=b"")
        client, _ = _make_daemon_client(local_port=port)
        client._daemon_setup_done = True
        with pytest.raises(UiaError, match="empty response"):
            client.run_batch(
                [{"id": "1", "op": "find", "role": "Button"}],
                timeout=5,
            )
        th.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Tunnel resilience: retry on ConnectionRefusedError
# ---------------------------------------------------------------------------

class TestDaemonTunnelResilience:
    """Verify that _run_batch_daemon retries once after a ConnectionRefused
    and raises UiaError when both attempts fail."""

    def test_daemon_retry_on_connection_refused(self, monkeypatch):
        """socket.create_connection fails twice then succeeds on 3rd call.
        The first failure triggers _ensure_daemon_setup (re-setup); the
        second call is the retry. We mock the retry to succeed so run_batch
        returns the expected result.
        """
        import automation.uia.client as _client_mod

        # Use a real loopback server for the eventual successful connection.
        response = (
            json.dumps({"ok": True, "results": {"1": {"ok": True}}}).encode()
            + b"\n"
        )

        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        real_port = srv.getsockname()[1]

        def _fake_server():
            try:
                conn, _ = srv.accept()
                buf = b""
                conn.settimeout(2.0)
                while not buf.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                conn.sendall(response)
                conn.close()
            finally:
                srv.close()

        srv_th = threading.Thread(target=_fake_server, daemon=True)
        srv_th.start()

        call_count = {"n": 0}
        real_create_connection = socket.create_connection

        def _patched_create(addr, timeout=None):
            call_count["n"] += 1
            if call_count["n"] <= 1:
                # First call: simulate dead tunnel.
                raise ConnectionRefusedError(111, "Connection refused")
            # Second call (retry after re-setup): connect for real.
            return real_create_connection((addr[0], real_port), timeout)

        monkeypatch.setattr(
            _client_mod.socket, "create_connection", _patched_create,
        )

        client, ssh = _make_daemon_client(local_port=real_port)
        client._daemon_setup_done = True  # first call skips setup...

        # _ensure_daemon_setup will be re-triggered (resets _daemon_setup_done)
        # and must succeed — patch it to set the flag without real SSH.
        def _fake_ensure():
            client._daemon_setup_done = True

        monkeypatch.setattr(client, "_ensure_daemon_setup", _fake_ensure)

        result = client.run_batch(
            [{"id": "1", "op": "find", "role": "Button"}],
            timeout=5,
        )
        srv_th.join(timeout=3.0)
        assert result["ok"] is True
        # create_connection was called twice (first refused, second succeeded).
        assert call_count["n"] == 2

    def test_daemon_two_retry_failures_raise_uia_error(self, monkeypatch):
        """socket.create_connection always raises ConnectionRefusedError;
        UiaError must be raised after the single retry attempt."""
        import automation.uia.client as _client_mod

        def _always_refused(addr, timeout=None):
            raise ConnectionRefusedError(111, "Connection refused")

        monkeypatch.setattr(
            _client_mod.socket, "create_connection", _always_refused,
        )

        client, ssh = _make_daemon_client(local_port=19999)
        client._daemon_setup_done = True

        # Re-setup must also succeed (not the thing under test).
        def _fake_ensure():
            client._daemon_setup_done = True

        monkeypatch.setattr(client, "_ensure_daemon_setup", _fake_ensure)

        with pytest.raises(UiaError, match="retry also failed"):
            client.run_batch(
                [{"id": "1", "op": "find", "role": "Button"}],
                timeout=5,
            )


# ---------------------------------------------------------------------------
# Legacy mode still works (backward compatibility)
# ---------------------------------------------------------------------------

class TestLegacyModeStillWorks:

    def test_use_session1_false_uses_legacy_argv(self):
        """With `use_session1=False` and no `use_daemon`, run_batch must
        go through the legacy argv/stdin transport."""
        ssh = MagicMock()
        client = UiaClient(ssh, use_session1=False)
        client._worker_deployed = True

        ssh.run.return_value = MagicMock(
            success=True, returncode=0,
            stdout='{"ok": true, "results": {"1": {"ok": true}}}',
            stderr="",
        )
        result = client.run_batch(
            [{"id": "1", "op": "find", "role": "Button"}],
        )
        assert result["ok"] is True
        cmd = ssh.run.call_args[0][0]
        assert "python" in cmd
        assert "mosdat_uia_worker.py" in cmd

    def test_explicit_use_daemon_false_keeps_session1(self):
        """When `use_daemon=False` is explicit, default `use_session1=True`
        still wins and the session1 schtasks path runs."""
        ssh = MagicMock()
        client = UiaClient(ssh, use_daemon=False, session1_poll_interval=0.0)
        client._worker_deployed = True
        # First ssh.run = schtasks Create/Run.
        ssh.scp_to.return_value = MagicMock(success=True, returncode=0,
                                            stdout="", stderr="")
        ssh.run.side_effect = [
            MagicMock(success=True, returncode=0,
                      stdout="uia-launch:ok\n", stderr=""),
            MagicMock(
                success=True, returncode=0,
                stdout='{"ok": true, "results": {"1": {"ok": true}}}',
                stderr="",
            ),
            MagicMock(success=True, returncode=0, stdout="", stderr=""),
        ]
        result = client.run_batch(
            [{"id": "1", "op": "find", "role": "Button"}],
        )
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# Worker daemon entry point (run_daemon) — direct
# ---------------------------------------------------------------------------

class TestWorkerDaemon:
    """Direct test of `automation.uia.worker.run_daemon`: spin it up on a
    free port, send a request with `_shutdown` to bring it down cleanly.
    """

    def test_daemon_handles_shutdown_op(self):
        from automation.uia import worker as uia_worker

        # Bind to a free port and pass it.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        th = threading.Thread(
            target=uia_worker.run_daemon, args=(port,), daemon=True,
        )
        th.start()

        # Wait for listener.
        deadline = time.monotonic() + 5.0
        connected = False
        while time.monotonic() < deadline:
            try:
                c = socket.create_connection(("127.0.0.1", port), timeout=0.5)
                c.close()
                connected = True
                break
            except OSError:
                time.sleep(0.1)
        assert connected, "daemon never opened the port"

        # Send _shutdown and check response.
        c = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        c.sendall(b'{"op":"_shutdown"}\n')
        data = b""
        c.settimeout(2.0)
        while True:
            chunk = c.recv(4096)
            if not chunk:
                break
            data += chunk
            if data.endswith(b"\n"):
                break
        c.close()
        parsed = json.loads(data.decode().rstrip("\n"))
        assert parsed["ok"] is True
        assert parsed.get("shutdown") is True

        th.join(timeout=5.0)
        assert not th.is_alive()

    def test_daemon_dispatches_empty_batch(self):
        """An empty op batch is a valid no-op (smoke shape); daemon should
        accept it on the wire even without pywinauto installed."""
        from automation.uia import worker as uia_worker

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        th = threading.Thread(
            target=uia_worker.run_daemon, args=(port,), daemon=True,
        )
        th.start()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                c = socket.create_connection(("127.0.0.1", port), timeout=0.5)
                c.close()
                break
            except OSError:
                time.sleep(0.1)

        c = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        c.sendall(b'{"ops":[]}\n')
        data = b""
        c.settimeout(3.0)
        while True:
            chunk = c.recv(4096)
            if not chunk:
                break
            data += chunk
            if data.endswith(b"\n"):
                break
        c.close()
        parsed = json.loads(data.decode().rstrip("\n"))
        assert parsed["ok"] is True
        assert parsed["results"] == {}

        # Shut it down for cleanup.
        c = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        c.sendall(b'{"op":"_shutdown"}\n')
        c.recv(4096)
        c.close()
        th.join(timeout=5.0)
