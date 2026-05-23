"""UIA host-side client — drives the Windows VM worker over SSH.

Windows analog of `automation/atspi/client.py`. Wraps
`automation.transport.ssh.SSHClient` to deploy and invoke
`automation/uia/worker.py` on a remote Windows VM. The worker is
single-shot (see `worker.py` header for protocol shape); each public
method on `UiaClient` builds a JSON op-batch, ships it via SSH, parses
the result, and raises `UiaError` on any op failure.

Op-batch shape and result shape are defined in `worker.py`. This module
holds no UIA state — every call is stateless w.r.t. the Windows tree.

Big architectural difference vs `AtspiClient`:
  * Linux pointer-mode click moves the cursor HOST-SIDE via VNC button
    events (`InputInjector._position_cursor` → `vnc.click`).
  * Windows pointer-mode click moves the cursor VM-SIDE via pywinauto.
    A single op-batch executes `find → move_cursor → get_at_point →
    click_cursor`, eliminating a round-trip. Callers no longer pass an
    `input_injector` — for symmetry with the Linux signature the kwarg
    is still accepted but ignored.

Session 0 vs Session 1 (the reason this file looks complicated):
  Windows OpenSSH runs incoming commands in Session 0 (services). The
  interactive desktop where RC.Electron lives is Session 1.
  `pywinauto.Desktop(backend='uia').windows()` is bound to the caller's
  window station — from Session 0 it sees zero windows. To reach the
  user-visible tree, the worker MUST run in Session 1. The fix uses the
  same `schtasks /Create /XML` with `<LogonType>InteractiveToken</LogonType>`
  trick that `automation/vlm/input.py::launch/focus_app` uses to spawn
  GUI processes. Because stdout doesn't cross sessions, the worker writes
  its result to a file and the client polls/reads it via SSH.

Payload-passing strategy:
  * `use_session1=True` (default): JSON is scp'd to
    `C:\\tmp\\uia-req-<uuid>.json`, a transient scheduled task launches the
    worker as the interactive user with `--out C:\\tmp\\uia-res-<uuid>.json`,
    the client polls for the result file and reads it back. Required for
    any op that touches the live desktop tree.
  * `use_session1=False`: legacy path. Small batches go via shell-quoted
    `sys.argv[1]`; larger ones go via stdin redirection. Retained for
    unit tests and for any future Session-0-safe use case.

Threshold for argv-vs-stdin in the legacy path is
`DEFAULT_PAYLOAD_INLINE_MAX_BYTES`; override per call via
the `inline_max_bytes=` kwarg on `run_batch`.
"""

from __future__ import annotations

import json
import shlex
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from automation.transport.ssh import SSHClient


# --------------------------- module-level defaults ---------------------------

DEFAULT_WORKER_REMOTE_PATH = "C:\\tmp\\mosdat_uia_worker.py"
DEFAULT_SETUP_REMOTE_PATH = "C:\\tmp\\mosdat_uia_setup.ps1"
DEFAULT_SETUP_DAEMON_REMOTE_PATH = "C:\\tmp\\mosdat_uia_setup_daemon.ps1"
DEFAULT_REMOTE_PAYLOAD_DIR = "C:\\tmp"
DEFAULT_PAYLOAD_INLINE_MAX_BYTES = 8 * 1024  # 8 KB
DEFAULT_CALL_TIMEOUT_S = 60
DEFAULT_WAIT_FOR_TIMEOUT_S = 15.0
DEFAULT_WAIT_FOR_INTERVAL_S = 0.5
DEFAULT_APP_FILTER = "rocket"
DEFAULT_SESSION1_POLL_INTERVAL_S = 0.25
DEFAULT_SESSION1_BUFFER_S = 8  # extra slack on top of the op-batch timeout
DEFAULT_DAEMON_PORT = 5555
DEFAULT_DAEMON_SETUP_TIMEOUT_S = 60
DEFAULT_DAEMON_TUNNEL_WAIT_S = 15.0

_LOCAL_WORKER_PATH = Path(__file__).resolve().parent / "worker.py"
_LOCAL_SETUP_PATH = Path(__file__).resolve().parent / "setup.ps1"
_LOCAL_SETUP_DAEMON_PATH = Path(__file__).resolve().parent / "setup_daemon.ps1"


# --------------------------- exceptions --------------------------------------


class UiaError(RuntimeError):
    """Raised when a UIA op-batch returns a non-ok result, the SSH transport
    fails, or the worker output cannot be parsed.

    Carries the raw worker result (if available) in `.result` for debugging.
    """

    def __init__(self, message: str, *, result: Optional[dict] = None,
                 stderr: Optional[str] = None) -> None:
        super().__init__(message)
        self.result = result
        self.stderr = stderr


# --------------------------- client ------------------------------------------


class UiaClient:
    def __init__(
        self,
        ssh: SSHClient,
        worker_remote_path: str = DEFAULT_WORKER_REMOTE_PATH,
        *,
        setup_remote_path: str = DEFAULT_SETUP_REMOTE_PATH,
        setup_daemon_remote_path: str = DEFAULT_SETUP_DAEMON_REMOTE_PATH,
        remote_payload_dir: str = DEFAULT_REMOTE_PAYLOAD_DIR,
        app_filter: str = DEFAULT_APP_FILTER,
        call_timeout: int = DEFAULT_CALL_TIMEOUT_S,
        use_session1: bool = True,
        session1_poll_interval: float = DEFAULT_SESSION1_POLL_INTERVAL_S,
        # Daemon mode: persistent worker registered as a logon-triggered
        # scheduled task on the VM, host talks to it via an SSH -L tunnel.
        # When `use_daemon=True`, supersedes use_session1.
        use_daemon: Optional[bool] = None,
        daemon_port: int = DEFAULT_DAEMON_PORT,
        local_forward_port: Optional[int] = None,
    ) -> None:
        self._ssh = ssh
        self._worker_path = worker_remote_path
        self._setup_path = setup_remote_path
        self._setup_daemon_path = setup_daemon_remote_path
        self._payload_dir = remote_payload_dir
        self._app_filter = app_filter
        self._call_timeout = call_timeout
        self._worker_deployed = False
        self._use_session1 = bool(use_session1)
        self._session1_poll_interval = float(session1_poll_interval)
        # Daemon-mode state.
        # Default policy: enable daemon only when the caller has NOT opted
        # into a legacy transport explicitly. `use_session1=False` is the
        # signal unit tests use to request the legacy argv shape; we honour
        # it by leaving the daemon off so the legacy ssh.run path stays in
        # play. Production callers leave `use_session1` at its True default
        # and end up on the daemon transport.
        # Default: daemon OFF to keep existing unit tests (which mock ssh.run
        # and inspect the legacy argv / session1 schtasks XML wire shapes)
        # working without modification. Production callers explicitly pass
        # `use_daemon=True` (see commands/functional_cmd.py, routines/harness.py,
        # mcp_tools.py, issue_confirm.py, main.py) to get the persistent
        # transport.
        if use_daemon is None:
            self._use_daemon = False
        else:
            self._use_daemon = bool(use_daemon)
        self._daemon_port = int(daemon_port)
        self._local_port = (int(local_forward_port)
                            if local_forward_port is not None
                            else self._pick_free_local_port())
        self._daemon_setup_done = False
        if self._use_daemon:
            # Inject the port forward into the SSH transport. The
            # ControlMaster picks it up on first connect.
            try:
                self._ssh.add_port_forward(
                    self._local_port, "127.0.0.1", self._daemon_port,
                )
            except AttributeError:
                # Older SSHClient without port-forward support — fall back to
                # legacy session1 transport rather than failing at construction.
                self._use_daemon = False

    @staticmethod
    def _pick_free_local_port() -> int:
        """Bind a transient ephemeral socket to grab a free port from the OS.

        Closed immediately; there's a tiny TOCTOU window before the SSH
        forward grabs it but in practice the kernel won't recycle that
        port for the brief gap.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
        finally:
            try:
                s.close()
            except Exception:
                pass

    # ----- deployment -------------------------------------------------------

    def _ensure_deployed(self) -> None:
        """SCP the worker to the VM on first use. Idempotent.

        The setup.ps1 is NOT auto-run here — it installs pip packages and
        callers/orchestrators should run it once per VM (see
        `automation/uia/setup.ps1`). We only ship the worker.
        """
        if self._worker_deployed:
            return
        if not _LOCAL_WORKER_PATH.exists():
            raise UiaError(
                f"local worker not found at {_LOCAL_WORKER_PATH}")
        r = self._ssh.scp_to(_LOCAL_WORKER_PATH, self._worker_path)
        if not r.success:
            raise UiaError(
                f"failed to scp worker to {self._worker_path}: {r.stderr}",
                stderr=r.stderr)
        self._worker_deployed = True

    def deploy_setup_script(self) -> None:
        """Optional helper: SCP setup.ps1 to the VM. Caller must invoke it
        via `powershell -ExecutionPolicy Bypass -File <path>`."""
        if not _LOCAL_SETUP_PATH.exists():
            raise UiaError(
                f"local setup script not found at {_LOCAL_SETUP_PATH}")
        r = self._ssh.scp_to(_LOCAL_SETUP_PATH, self._setup_path)
        if not r.success:
            raise UiaError(
                f"failed to scp setup.ps1 to {self._setup_path}: {r.stderr}",
                stderr=r.stderr)

    # ----- raw escape hatch -------------------------------------------------

    def run_batch(
        self,
        ops: list[dict],
        *,
        init: Optional[dict] = None,
        timeout: Optional[int] = None,
        inline_max_bytes: int = DEFAULT_PAYLOAD_INLINE_MAX_BYTES,
    ) -> dict:
        """Execute an arbitrary op-batch. Returns the worker result dict.

        Does NOT raise on per-op failure (worker batch may legitimately
        have a failing `verify` you want to inspect). Raises `UiaError`
        only on transport/parse failure or a batch-level error.

        Transport selection (priority order):
          * `use_daemon=True` (default) — TCP request to the persistent
            worker over the SSH -L tunnel. No schtasks per call.
          * `use_session1=True` — legacy schtasks-per-op InteractiveToken
            transport (preserved for backward-compat).
          * else — Session-0 argv-or-stdin transport (unit tests).
        """
        batch = {"ops": ops, "init": init or {"force_uia_init": True}}
        payload = json.dumps(batch, separators=(",", ":"))
        timeout = timeout if timeout is not None else self._call_timeout

        if self._use_daemon:
            return self._run_batch_daemon(payload, timeout=timeout)

        # Legacy paths still need the worker on the VM.
        self._ensure_deployed()
        if self._use_session1:
            return self._run_batch_session1(payload, timeout=timeout)
        return self._run_batch_legacy(
            payload, timeout=timeout, inline_max_bytes=inline_max_bytes,
        )

    # ----- daemon transport (persistent worker + SSH port-forward) ----------

    def _run_batch_daemon(self, payload: str, *, timeout: int) -> dict:
        """Send one op-batch to the persistent daemon via the local-forwarded
        TCP tunnel. Idempotently registers/starts the daemon on first call.

        Wire protocol: <json>\n request, <json>\n response. The daemon
        accepts one batch per connection and writes the response back on
        the same socket, then closes.

        On `ConnectionRefusedError` / `socket.error` the method re-triggers
        `_ensure_daemon_setup()` (which re-opens the SSH tunnel) and retries
        the connection exactly once. If the retry also fails, `UiaError` is
        raised with both the original and retry errors.
        """
        self._ensure_daemon_setup()
        _orig_err: Optional[OSError] = None
        for _attempt in range(2):
            try:
                s = socket.create_connection(
                    ("127.0.0.1", self._local_port), timeout=10,
                )
                break  # connected — exit retry loop
            except (ConnectionRefusedError, socket.error) as e:
                if _attempt == 0:
                    _orig_err = e
                    # Tunnel died mid-session — force re-setup on next call so
                    # _ensure_daemon_setup() re-opens the port-forward.
                    self._daemon_setup_done = False
                    try:
                        self._ensure_daemon_setup()
                    except UiaError:
                        raise UiaError(
                            f"daemon: cannot connect to local tunnel 127.0.0.1:"
                            f"{self._local_port} (original: {_orig_err!r}); "
                            f"re-setup also failed",
                        ) from e
                else:
                    raise UiaError(
                        f"daemon: cannot connect to local tunnel 127.0.0.1:"
                        f"{self._local_port} (original: {_orig_err!r}); "
                        f"retry also failed: {e!r}",
                    ) from e
        try:
            s.settimeout(max(1.0, float(timeout)))
            s.sendall(payload.encode("utf-8") + b"\n")
            buf = bytearray()
            while True:
                try:
                    chunk = s.recv(65536)
                except socket.timeout as e:
                    raise UiaError(
                        f"daemon: timed out after {timeout}s waiting for "
                        f"response (got {len(buf)} bytes)",
                    ) from e
                if not chunk:
                    break
                buf.extend(chunk)
                if buf.endswith(b"\n"):
                    break
        finally:
            try:
                s.close()
            except Exception:
                pass
        if not buf:
            raise UiaError("daemon: empty response from worker")
        try:
            return json.loads(buf.decode("utf-8").rstrip("\n"))
        except Exception as e:
            raise UiaError(
                f"daemon: could not parse worker response as JSON: {e!r}; "
                f"first 500 bytes: {bytes(buf)[:500]!r}",
            ) from e

    def _ensure_daemon_setup(self) -> None:
        """First-call setup: deploy worker + setup script, register the
        scheduled task, open the SSH port-forward, probe the tunnel.

        Idempotent (Register-ScheduledTask -Force on the VM side handles
        re-registration cleanly). After success, `_daemon_setup_done`
        prevents further work for the lifetime of this client.
        """
        if self._daemon_setup_done:
            return

        # Step 1: ship the worker + setup script to the VM.
        self._ensure_deployed()
        if not _LOCAL_SETUP_DAEMON_PATH.exists():
            raise UiaError(
                f"local setup_daemon.ps1 not found at "
                f"{_LOCAL_SETUP_DAEMON_PATH}",
            )
        scp_res = self._ssh.scp_to(_LOCAL_SETUP_DAEMON_PATH,
                                   self._setup_daemon_path)
        if not scp_res.success:
            raise UiaError(
                f"daemon: failed to scp setup_daemon.ps1: {scp_res.stderr}",
                stderr=scp_res.stderr,
            )

        # Step 2: run setup_daemon.ps1 on the VM (registers + starts task).
        # Pass the worker path + port explicitly so test/runtime overrides
        # propagate. Wait up to DEFAULT_DAEMON_SETUP_TIMEOUT_S for the
        # listener probe inside the script.
        setup_cmd = (
            f"powershell -NoProfile -ExecutionPolicy Bypass -File "
            f"\"{self._setup_daemon_path}\" -WorkerPath "
            f"\"{self._worker_path}\" -Port {self._daemon_port}"
        )
        setup_res = self._ssh.run(
            setup_cmd, timeout=DEFAULT_DAEMON_SETUP_TIMEOUT_S,
        )
        if not setup_res.success or "DAEMON_LISTENING_PORT_" not in (
            setup_res.stdout or ""
        ):
            raise UiaError(
                f"daemon: setup_daemon.ps1 failed: "
                f"rc={setup_res.returncode} "
                f"stdout={setup_res.stdout!r} stderr={setup_res.stderr!r}",
                stderr=setup_res.stderr,
            )

        # Step 3: probe the local end of the SSH tunnel. The setup_res run
        # above already opened the ControlMaster (forward came along).
        deadline = time.monotonic() + DEFAULT_DAEMON_TUNNEL_WAIT_S
        last_err: Optional[BaseException] = None
        while time.monotonic() < deadline:
            try:
                probe = socket.create_connection(
                    ("127.0.0.1", self._local_port), timeout=2,
                )
                probe.close()
                self._daemon_setup_done = True
                return
            except (OSError, socket.error) as e:
                last_err = e
                time.sleep(0.5)
        raise UiaError(
            f"daemon: port-forward to {self._local_port}->VM:"
            f"{self._daemon_port} not reachable after "
            f"{DEFAULT_DAEMON_TUNNEL_WAIT_S}s "
            f"(last error: {last_err!r})",
        )

    # ----- legacy (Session 0) transport -------------------------------------

    def _run_batch_legacy(
        self, payload: str, *, timeout: int, inline_max_bytes: int,
    ) -> dict:
        """Original argv-or-stdin transport. Runs in Session 0 (SSH session)
        and thus CANNOT see the interactive user's UIA tree. Retained for
        unit tests and any future Session-0-safe op (e.g. process listing
        if the worker grows one)."""
        if len(payload.encode("utf-8")) <= inline_max_bytes:
            # Windows: `python` is on PATH after the setup.ps1 prereq install.
            cmd = f"python {shlex.quote(self._worker_path)} {shlex.quote(payload)}"
            r = self._ssh.run(cmd, timeout=timeout)
        else:
            remote_payload = (
                f"{self._payload_dir.rstrip(chr(92))}\\"
                f"mosdat_uia_payload_{uuid.uuid4().hex}.json"
            )
            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8",
            ) as tf:
                tf.write(payload)
                local_payload = Path(tf.name)
            try:
                scp_res = self._ssh.scp_to(local_payload, remote_payload)
                if not scp_res.success:
                    raise UiaError(
                        f"failed to scp payload to {remote_payload}: "
                        f"{scp_res.stderr}", stderr=scp_res.stderr)
                # Windows redirection works in cmd.exe via `<`. The default
                # OpenSSH shell on Win10+ is cmd; ssh transport doesn't
                # change that.
                cmd = (
                    f"python {shlex.quote(self._worker_path)} "
                    f"< {shlex.quote(remote_payload)}"
                )
                r = self._ssh.run(cmd, timeout=timeout)
                # Best-effort cleanup.
                self._ssh.run(f"del {shlex.quote(remote_payload)}",
                              timeout=10)
            finally:
                try:
                    local_payload.unlink()
                except OSError:
                    pass

        if not r.success and not r.stdout:
            raise UiaError(
                f"worker ssh call failed (rc={r.returncode}): {r.stderr}",
                stderr=r.stderr)
        try:
            result = json.loads(r.stdout)
        except Exception as e:
            raise UiaError(
                f"could not parse worker stdout as JSON: {e!r}; "
                f"first 500 bytes: {r.stdout[:500]!r}",
                stderr=r.stderr) from e
        return result

    # ----- session 1 (schtasks InteractiveToken) transport ------------------

    def _run_batch_session1(self, payload: str, *, timeout: int) -> dict:
        """Run the worker in Session 1 via a transient scheduled task.

        Mirrors `automation/vlm/input.py::launch/focus_app`: build an XML
        task with `<LogonType>InteractiveToken</LogonType>` so the worker
        process is spawned in the interactive user's window station and
        can enumerate the live UIA tree. Stdout does not cross sessions,
        so the worker writes its result to a file (`--out <path>`) which
        the client polls and reads back over SSH.

        Lifecycle: scp request JSON → schtasks Create+Run → poll for the
        result file → read+parse → cleanup task + temp files. Failures at
        any stage raise `UiaError`.
        """
        token = uuid.uuid4().hex
        payload_dir = self._payload_dir.rstrip("\\")
        req_path = f"{payload_dir}\\uia-req-{token}.json"
        res_path = f"{payload_dir}\\uia-res-{token}.json"
        task_name = f"mosdat-uia-{token}"

        # 1. Write request JSON locally, scp to VM.
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8",
        ) as tf:
            tf.write(payload)
            local_payload = Path(tf.name)
        try:
            scp_res = self._ssh.scp_to(local_payload, req_path)
            if not scp_res.success:
                raise UiaError(
                    f"session1: failed to scp request to {req_path}: "
                    f"{scp_res.stderr}", stderr=scp_res.stderr)

            # 2. Create + run the transient scheduled task.
            # Use the XML form so we get InteractiveToken (matches
            # automation/vlm/input.py::launch).
            ps_create = self._build_session1_launch_ps(
                task_name=task_name, req_path=req_path, res_path=res_path,
            )
            create_res = self._ssh.run(ps_create, timeout=20)
            if not create_res.success:
                raise UiaError(
                    f"session1: schtasks Create/Run failed: "
                    f"rc={create_res.returncode} stderr={create_res.stderr!r} "
                    f"stdout={create_res.stdout!r}",
                    stderr=create_res.stderr,
                )

            # 3. Poll for the result file. Budget = op-batch timeout + buffer.
            deadline = time.monotonic() + float(timeout) + float(
                DEFAULT_SESSION1_BUFFER_S
            )
            raw: Optional[str] = None
            poll_cmd = (
                "powershell -NoProfile -Command "
                f"\"if (Test-Path '{res_path}') "
                f"{{ Get-Content -Raw -Encoding UTF8 '{res_path}' }}\""
            )
            while time.monotonic() < deadline:
                pr = self._ssh.run(poll_cmd, timeout=15)
                if pr.success and pr.stdout and pr.stdout.strip():
                    raw = pr.stdout
                    break
                time.sleep(self._session1_poll_interval)

            if raw is None:
                raise UiaError(
                    f"session1: timed out after {timeout + DEFAULT_SESSION1_BUFFER_S}s "
                    f"waiting for {res_path}",
                )

            try:
                result = json.loads(raw)
            except Exception as e:
                raise UiaError(
                    f"session1: could not parse result file as JSON: {e!r}; "
                    f"first 500 bytes: {raw[:500]!r}",
                ) from e
            return result
        finally:
            # Best-effort cleanup: delete task + temp files. Don't shadow a
            # primary error.
            try:
                cleanup = (
                    "powershell -NoProfile -Command \""
                    f"schtasks /Delete /F /TN '{task_name}' 2>$null | Out-Null; "
                    f"Remove-Item -Force -ErrorAction SilentlyContinue "
                    f"'{req_path}','{res_path}'\""
                )
                self._ssh.run(cleanup, timeout=15)
            except Exception:
                pass
            try:
                local_payload.unlink()
            except OSError:
                pass

    @staticmethod
    def _build_session1_launch_ps(
        *, task_name: str, req_path: str, res_path: str,
        worker_path: str = DEFAULT_WORKER_REMOTE_PATH,
    ) -> str:
        """Build the PowerShell script that creates+runs the transient
        schtasks. Same XML/InteractiveToken shape as
        `automation/vlm/input.py::launch`. Extracted as a static method so
        tests can assert the generated argv shape without invoking SSH.
        """
        # The worker accepts `file:<path>` as its argv[1] to read the
        # request from a file, and `--out <path>` to write the result
        # back to a file (stdout doesn't cross sessions).
        inner = (
            f"python '{worker_path}' 'file:{req_path}' --out '{res_path}'"
        )
        # PowerShell here-string. Escape the task name and paths into the XML.
        ps = (
            "$ProgressPreference='SilentlyContinue'\n"
            "$null = New-Item -Force -ItemType Directory C:\\tmp 2>$null\n"
            f"$inner = @'\n{inner}\n'@\n"
            "$enc = [Convert]::ToBase64String("
            "[System.Text.Encoding]::Unicode.GetBytes($inner))\n"
            "$xml = @\"\n"
            "<?xml version=\"1.0\" encoding=\"UTF-16\"?>\n"
            "<Task version=\"1.2\" xmlns=\"http://schemas.microsoft.com/"
            "windows/2004/02/mit/task\">\n"
            "  <Principals><Principal id=\"A\"><LogonType>InteractiveToken"
            "</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>"
            "</Principals>\n"
            "  <Settings><MultipleInstancesPolicy>IgnoreNew"
            "</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false"
            "</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false"
            "</StopIfGoingOnBatteries><ExecutionTimeLimit>PT5M"
            "</ExecutionTimeLimit></Settings>\n"
            "  <Actions Context=\"A\"><Exec><Command>powershell.exe"
            "</Command><Arguments>-ExecutionPolicy Bypass -EncodedCommand "
            "$enc</Arguments></Exec></Actions>\n"
            "</Task>\n"
            "\"@\n"
            f"$xmlPath = 'C:\\tmp\\mosdat_uia_{task_name}.xml'\n"
            "[System.IO.File]::WriteAllText($xmlPath, $xml, "
            "[System.Text.Encoding]::Unicode)\n"
            f"schtasks /Delete /F /TN \"{task_name}\" 2>$null | Out-Null\n"
            f"schtasks /Create /F /TN \"{task_name}\" /XML $xmlPath | "
            "Out-Null\n"
            f"schtasks /Run /TN \"{task_name}\" | Out-Null\n"
            "Write-Output 'uia-launch:ok'\n"
        )
        import base64 as _b64
        encoded = _b64.b64encode(ps.encode("utf-16-le")).decode()
        return (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass "
            f"-EncodedCommand {encoded}"
        )

    # ----- convenience wrappers --------------------------------------------

    def find(
        self,
        role: str,
        name: Optional[str] = None,
        name_substr: bool = False,
        *,
        app_filter: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> dict:
        """Locate a node by role + optional name. Raises on no match."""
        op = {
            "id": "1", "op": "find", "role": role, "name": name,
            "name_substr": bool(name_substr),
            "app_filter": app_filter or self._app_filter,
        }
        result = self.run_batch([op], timeout=timeout)
        node = result["results"].get("1", {})
        if not node.get("ok"):
            raise UiaError(
                f"find failed: role={role!r} name={name!r} "
                f"name_substr={name_substr} app_filter={op['app_filter']!r}",
                result=result)
        return node

    def find_all(
        self,
        role: str,
        name: Optional[str] = None,
        name_substr: bool = False,
        *,
        app_filter: Optional[str] = None,
        timeout: Optional[int] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Locate all role+name matches. Returns ordered list (visible first).

        Raises `UiaError` on transport failure; returns an empty list when
        the worker reports no matches. Consumed by `_click_via_pointer`'s
        candidate iteration so stale popovers (Chromium duplicate menu
        items with overlapping bboxes) can be rejected via verify-at-point
        and the NEXT candidate tried.
        """
        op = {
            "id": "1", "op": "find_all", "role": role, "name": name,
            "name_substr": bool(name_substr),
            "app_filter": app_filter or self._app_filter,
            "limit": int(limit),
        }
        result = self.run_batch([op], timeout=timeout)
        res = result["results"].get("1", {})
        if not res.get("ok"):
            raise UiaError(
                f"find_all failed: role={role!r} name={name!r} "
                f"name_substr={name_substr} "
                f"app_filter={op['app_filter']!r}",
                result=result)
        return list(res.get("candidates") or [])

    def click(
        self,
        role: str,
        name: Optional[str] = None,
        *,
        name_substr: bool = False,
        action_idx: int = 0,
        app_filter: Optional[str] = None,
        timeout: Optional[int] = None,
        via: str = "pointer",
        input_injector: Any = None,
        motion: Optional[str] = None,
        dwell_ms: Optional[int] = None,
    ) -> dict:
        """Click an accessible widget.

        via='pointer' (default): single op-batch on the VM — find →
            mouse.move → ElementFromPoint verify → mouse.click. Real OS
            cursor motion visible in RDP/VNC recordings. ``input_injector``
            is accepted but ignored (Linux carries it via VNC; Windows
            moves the cursor directly on the VM).
        via='action': semantic UIA pattern invocation (Invoke/Toggle/
            Select). No cursor motion. Use for fast smokes, off-screen
            widgets, or controls whose primary action is exposed only as
            a UIA pattern.
        via='hint': hybrid — move cursor to widget area + dwell, THEN
            invoke the UIA pattern. No real button event is emitted (so
            the click handler must be wired to the pattern, like Fuselage
            ToggleSwitch). Combines `action`'s reliability with
            `pointer`'s visual context. Uses ``clickable_extents`` (an
            ancestor bbox computed by the worker) when the target itself
            is zero-extent; default dwell is 300 ms.
        """
        if via == "pointer":
            return self._click_via_pointer(
                role, name, name_substr=name_substr,
                app_filter=app_filter, timeout=timeout,
                motion=motion, dwell_ms=dwell_ms,
            )
        if via == "action":
            return self._click_via_action(
                role, name, name_substr=name_substr,
                action_idx=action_idx, app_filter=app_filter,
                timeout=timeout,
            )
        if via == "hint":
            return self._click_via_hint(
                role, name, name_substr=name_substr,
                action_idx=action_idx, app_filter=app_filter,
                timeout=timeout, motion=motion, dwell_ms=dwell_ms,
            )
        raise UiaError(
            f"unknown via={via!r}; expected 'pointer', 'action', or 'hint'"
        )

    def _click_via_hint(
        self,
        role: str,
        name: Optional[str] = None,
        *,
        name_substr: bool = False,
        action_idx: int = 0,
        app_filter: Optional[str] = None,
        timeout: Optional[int] = None,
        motion: Optional[str] = None,
        dwell_ms: Optional[int] = None,
    ) -> dict:
        """Hint-mode click: visible cursor motion + dwell, then do_action.

        Single op-batch on the VM: find → move_cursor (with dwell) →
        do_action. Cursor motion is purely visual; no button event fires.
        """
        af = app_filter or self._app_filter
        find_res = self.find(
            role, name, name_substr=name_substr,
            app_filter=af, timeout=timeout,
        )
        bbox = find_res.get("clickable_extents") or find_res.get("extents")
        if not bbox:
            raise UiaError(
                "hint-mode: widget has no usable extents (neither target "
                f"nor 5-deep ancestor); use via='action' for role={role!r} "
                f"name={name!r}",
                result={"find": find_res},
            )
        cx = int(bbox["x"] + bbox["width"] / 2)
        cy = int(bbox["y"] + bbox["height"] / 2)
        hint_dwell = dwell_ms if dwell_ms is not None else 300

        ops = [
            {"id": "f", "op": "find", "role": role, "name": name,
             "name_substr": bool(name_substr), "app_filter": af},
            {"id": "m", "op": "move_cursor", "x": cx, "y": cy,
             "duration": 0.3, "dwell_ms": int(hint_dwell)},
            {"id": "a", "op": "do_action", "from_id": "f",
             "action_idx": int(action_idx), "app_filter": af},
        ]
        result = self.run_batch(ops, timeout=timeout)
        find2 = result["results"].get("f", {})
        act = result["results"].get("a", {})
        if not find2.get("ok"):
            raise UiaError(
                f"hint-mode action stage failed at find: role={role!r} "
                f"name={name!r}", result=result)
        if not act.get("ok"):
            raise UiaError(
                f"hint-mode action stage failed at do_action: role={role!r} "
                f"name={name!r} idx={action_idx}", result=result)
        return {
            "ok": True, "via": "hint",
            "hint_x": cx, "hint_y": cy,
            "dwell_ms": hint_dwell,
            "clickable_extents_used": "clickable_extents" in find_res
                and find_res["clickable_extents"] == bbox,
            "bbox": bbox,
            "find": find_res,
            "action": act,
        }

    def _click_via_action(
        self,
        role: str,
        name: Optional[str] = None,
        *,
        name_substr: bool = False,
        action_idx: int = 0,
        app_filter: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> dict:
        """Find + do_action in one round-trip. Returns the do_action result."""
        af = app_filter or self._app_filter
        ops = [
            {"id": "find", "op": "find", "role": role, "name": name,
             "name_substr": bool(name_substr), "app_filter": af},
            {"id": "act", "op": "do_action", "from_id": "find",
             "action_idx": int(action_idx), "app_filter": af},
        ]
        result = self.run_batch(ops, timeout=timeout)
        find_res = result["results"].get("find", {})
        act_res = result["results"].get("act", {})
        if not find_res.get("ok"):
            raise UiaError(
                f"click failed at find: role={role!r} name={name!r}",
                result=result)
        if not act_res.get("ok"):
            raise UiaError(
                f"click failed at do_action: role={role!r} name={name!r} "
                f"idx={action_idx}",
                result=result)
        out = dict(act_res)
        out["via"] = "action"
        return out

    def _click_via_pointer(
        self,
        role: str,
        name: Optional[str] = None,
        *,
        name_substr: bool = False,
        app_filter: Optional[str] = None,
        timeout: Optional[int] = None,
        motion: Optional[str] = None,
        dwell_ms: Optional[int] = None,
    ) -> dict:
        """Pointer-mode click: real cursor + verify + real button click.

        Algorithm (entirely VM-side, op-batches issued from host):
            1. find_all role+name matches (visible first).
            2. for each candidate, in order:
                 a. compute bbox center.
                 b. mouse.move(cx, cy) — visible in recording.
                 c. verify cursor lands on the expected element via
                    ElementFromPoint.
                 d. decision table:
                      * ok=false / no_node_at_point   → skip verify, click
                      * ok=true, empty path+role      → skip verify, click
                      * ok=true, subtree-match        → verified, click
                      * ok=true, OTHER widget         → REJECT, try next
            3. real button click via mouse.click(coords=(cx, cy)).
            4. if all candidates mismatch, raise with full iteration trace.

        Subtree match accepts exact path, ancestor or descendant (Chromium
        often returns a child label/icon under the cursor instead of the
        click target itself — still on the intended widget).

        The stale-popover guard (rejecting "OTHER widget") fixes the case
        where Chromium-based RC exposes a hidden popover menu item whose
        UIA bbox overlaps an unrelated UI region (e.g. composer): the
        cursor lands on the composer, get_at_point flags the mismatch,
        and we advance to the next role+name match instead of clicking
        the wrong widget.
        """
        af = app_filter or self._app_filter
        candidates = self.find_all(
            role, name, name_substr=name_substr,
            app_filter=af, timeout=timeout,
        )
        if not candidates:
            raise UiaError(
                f"pointer-mode: no candidates for role={role!r} name={name!r} "
                f"name_substr={name_substr} app_filter={af!r}",
            )

        duration = 0.3 if motion is None else (
            0.0 if motion == "instant" else 0.3
        )
        dwell = int(dwell_ms) if dwell_ms is not None else 0

        trace: list[dict] = []
        chosen: Optional[dict] = None
        chosen_cx = chosen_cy = 0
        chosen_extents: Optional[dict] = None
        chosen_verify: dict = {}
        chosen_verified = False
        chosen_skipped = False

        for idx, cand in enumerate(candidates):
            extents = cand.get("extents")
            if not extents:
                trace.append({"idx": idx, "skip": "no_extents",
                              "path": cand.get("path", "")})
                continue
            expected_path = cand.get("path") or ""
            cx = int(extents["x"] + extents["width"] / 2)
            cy = int(extents["y"] + extents["height"] / 2)

            verify_batch = self.run_batch(
                [
                    {"id": "m", "op": "move_cursor", "x": cx, "y": cy,
                     "duration": duration, "dwell_ms": dwell},
                    {"id": "v", "op": "get_at_point",
                     "x": cx, "y": cy, "app_filter": af},
                ],
                timeout=timeout,
            )
            v = verify_batch["results"].get("v", {})
            actual_path = v.get("path") or ""
            actual_role = v.get("role", "")
            actual_name = v.get("name", "")

            # Decision per spec:
            # 1. ok=false + no_node_at_point  → skip verify, click this cand.
            # 2. ok=true, empty path+role     → skip verify, click this cand.
            # 3. ok=true, subtree match       → verified, click this cand.
            # 4. ok=true, OTHER widget        → REJECT, try next.
            verify_unsupported = (
                (not v.get("ok")
                 and v.get("error") in ("no_node_at_point",
                                        "iuia_unavailable"))
                or (v.get("ok") and not actual_path and not actual_role)
            )
            ok_match = False
            if v.get("ok"):
                if actual_path == expected_path:
                    ok_match = True
                elif expected_path and actual_path.startswith(expected_path):
                    ok_match = True
                elif actual_path and expected_path.startswith(actual_path):
                    ok_match = True
                elif actual_role == role and (
                    not name or actual_name == name
                ) and v.get("under_app"):
                    ok_match = True

            entry = {
                "idx": idx, "path": expected_path,
                "cx": cx, "cy": cy,
                "visible": cand.get("visible"),
                "at_point": {"ok": v.get("ok"),
                             "error": v.get("error"),
                             "role": actual_role, "name": actual_name,
                             "path": actual_path},
            }
            if ok_match:
                entry["decision"] = "match"
                trace.append(entry)
                chosen = cand
                chosen_cx, chosen_cy = cx, cy
                chosen_extents = extents
                chosen_verify = {"role": actual_role, "name": actual_name,
                                 "path": actual_path}
                chosen_verified = True
                chosen_skipped = False
                break
            if verify_unsupported:
                entry["decision"] = "skip_verify"
                trace.append(entry)
                chosen = cand
                chosen_cx, chosen_cy = cx, cy
                chosen_extents = extents
                chosen_verify = {"role": actual_role, "name": actual_name,
                                 "path": actual_path}
                chosen_verified = False
                chosen_skipped = True
                break
            entry["decision"] = "reject_mismatch"
            trace.append(entry)
            # try next candidate

        if chosen is None:
            raise UiaError(
                f"pointer-mode: all {len(candidates)} candidates rejected "
                f"for role={role!r} name={name!r}: {trace}",
                result={"trace": trace, "candidates": candidates},
            )

        click_batch = self.run_batch(
            [{"id": "c", "op": "click_cursor",
              "x": chosen_cx, "y": chosen_cy, "button": 1}],
            timeout=timeout,
        )
        c = click_batch["results"].get("c", {})
        if not c.get("ok"):
            raise UiaError(
                f"click_cursor failed at ({chosen_cx},{chosen_cy}): "
                f"{c.get('error')}",
                result=click_batch)
        return {
            "ok": True, "via": "pointer", "x": chosen_cx, "y": chosen_cy,
            "verified": bool(chosen_verified),
            "verify_skipped": bool(chosen_skipped),
            "extents": chosen_extents,
            "find": chosen,
            "verify": chosen_verify,
            "candidate_idx": trace[-1]["idx"] if trace else 0,
            "candidate_count": len(candidates),
            "candidate_trace": trace,
        }

    def verify(
        self,
        role: str,
        name: Optional[str] = None,
        *,
        name_substr: bool = True,
        app_filter: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> bool:
        """Return True iff a matching node exists. Does NOT raise on no match."""
        op = {
            "id": "1", "op": "verify", "role": role, "name": name,
            "name_substr": bool(name_substr),
            "app_filter": app_filter or self._app_filter,
        }
        result = self.run_batch([op], timeout=timeout)
        return bool(result["results"].get("1", {}).get("ok"))

    def tree_dump(
        self,
        *,
        max_depth: int = 30,
        app_filter: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> list[dict]:
        """Walk the UIA tree under the matching app. Returns node list."""
        op = {
            "id": "1", "op": "tree_dump", "max_depth": int(max_depth),
            "app_filter": app_filter or self._app_filter,
        }
        result = self.run_batch([op], timeout=timeout)
        res = result["results"].get("1", {})
        if not res.get("ok"):
            raise UiaError(
                f"tree_dump failed: {res.get('error')}",
                result=result)
        return list(res.get("nodes") or [])

    def wait_for(
        self,
        any: Optional[list[dict]] = None,  # noqa: A002 — public API param name
        all: Optional[list[dict]] = None,  # noqa: A002
        timeout: float = DEFAULT_WAIT_FOR_TIMEOUT_S,
        interval: float = DEFAULT_WAIT_FOR_INTERVAL_S,
        *,
        app_filter: Optional[str] = None,
    ) -> dict:
        """Poll the tree until any/all conditions match or timeout."""
        if not any and not all:
            raise UiaError("wait_for requires `any` or `all`")
        op: dict[str, Any] = {
            "id": "1", "op": "wait_for",
            "timeout_ms": int(timeout * 1000),
            "interval_ms": int(interval * 1000),
            "app_filter": app_filter or self._app_filter,
        }
        if any:
            op["any"] = any
        if all:
            op["all"] = all
        ssh_timeout = int(timeout) + 30
        result = self.run_batch([op], timeout=ssh_timeout)
        res = result["results"].get("1", {})
        if not res.get("ok"):
            raise UiaError(
                f"wait_for timed out or failed: {res.get('error')}",
                result=result)
        return res
