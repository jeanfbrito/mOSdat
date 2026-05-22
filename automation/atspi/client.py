"""AT-SPI host-side client — drives the VM worker over SSH.

Wraps `automation.transport.ssh.SSHClient` to deploy and invoke
`automation/atspi/worker.py` on a remote Linux VM. The worker is single-shot
(see `worker.py` header for protocol shape); each public method on
`AtspiClient` builds a JSON op-batch, ships it via SSH, parses the result,
and raises `AtspiError` on any op failure.

Live test reference: see `.claude/mytasks/findings.md` section
"AT-SPI POC LIVE RESULT" — the POC this client was lifted from has been
run end-to-end against ubuntu2204@192.168.13.81 (RC.Electron 4.14.0,
push-button `Connect` resolved, do_action(0) flipped the framebuffer hash).

Op-batch shape and result shape are defined in `worker.py`. This module
holds no AT-SPI state — every call is stateless w.r.t. the bus.

Payload-passing strategy:
  * For small batches (default <8 KB serialized JSON), the payload is
    shell-escaped via `shlex.quote` and passed as `sys.argv[1]` to the
    worker. Single round-trip SSH call.
  * For larger payloads (e.g. dense `tree_dump` follow-ups, big `wait_for`
    `any/all` lists), the JSON is scp'd to a remote temp file and the
    worker is invoked with stdin redirection from that file. Avoids
    argv length limits and quoting hazards.

Threshold is `DEFAULT_PAYLOAD_INLINE_MAX_BYTES`; override per call via the
`inline_max_bytes=` kwarg on `run_batch`.
"""

from __future__ import annotations

import json
import shlex
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

from automation.transport.ssh import SSHClient


# --------------------------- module-level defaults ---------------------------

DEFAULT_WORKER_REMOTE_PATH = "/tmp/mosdat_atspi_worker.py"
DEFAULT_SETUP_REMOTE_PATH = "/tmp/mosdat_atspi_setup.sh"
DEFAULT_REMOTE_PAYLOAD_DIR = "/tmp"
DEFAULT_PAYLOAD_INLINE_MAX_BYTES = 8 * 1024  # 8 KB
DEFAULT_CALL_TIMEOUT_S = 60
DEFAULT_WAIT_FOR_TIMEOUT_S = 15.0
DEFAULT_WAIT_FOR_INTERVAL_S = 0.5
DEFAULT_APP_FILTER = "rocket"

_LOCAL_WORKER_PATH = Path(__file__).resolve().parent / "worker.py"
_LOCAL_SETUP_PATH = Path(__file__).resolve().parent / "setup.sh"


# --------------------------- exceptions --------------------------------------


class AtspiError(RuntimeError):
    """Raised when an AT-SPI op-batch returns a non-ok result, the SSH
    transport fails, or the worker output cannot be parsed.

    Carries the raw worker result (if available) in `.result` for debugging.
    """

    def __init__(self, message: str, *, result: Optional[dict] = None,
                 stderr: Optional[str] = None) -> None:
        super().__init__(message)
        self.result = result
        self.stderr = stderr


# --------------------------- client ------------------------------------------


class AtspiClient:
    def __init__(
        self,
        ssh: SSHClient,
        worker_remote_path: str = DEFAULT_WORKER_REMOTE_PATH,
        *,
        setup_remote_path: str = DEFAULT_SETUP_REMOTE_PATH,
        remote_payload_dir: str = DEFAULT_REMOTE_PAYLOAD_DIR,
        app_filter: str = DEFAULT_APP_FILTER,
        call_timeout: int = DEFAULT_CALL_TIMEOUT_S,
    ) -> None:
        self._ssh = ssh
        self._worker_path = worker_remote_path
        self._setup_path = setup_remote_path
        self._payload_dir = remote_payload_dir
        self._app_filter = app_filter
        self._call_timeout = call_timeout
        self._worker_deployed = False

    # ----- deployment -------------------------------------------------------

    def _ensure_deployed(self) -> None:
        """SCP the worker to the VM on first use. Idempotent.

        The setup.sh is NOT auto-run here — it requires sudo and apt access;
        callers/orchestrators should run it once per VM (see
        `automation/atspi/setup.sh`). We only ship the worker.
        """
        if self._worker_deployed:
            return
        if not _LOCAL_WORKER_PATH.exists():
            raise AtspiError(
                f"local worker not found at {_LOCAL_WORKER_PATH}")
        r = self._ssh.scp_to(_LOCAL_WORKER_PATH, self._worker_path)
        if not r.success:
            raise AtspiError(
                f"failed to scp worker to {self._worker_path}: {r.stderr}",
                stderr=r.stderr)
        self._worker_deployed = True

    def deploy_setup_script(self) -> None:
        """Optional helper: SCP setup.sh to the VM. Caller must invoke it
        (it needs sudo). Used by orchestrators that wire VM provisioning."""
        if not _LOCAL_SETUP_PATH.exists():
            raise AtspiError(
                f"local setup script not found at {_LOCAL_SETUP_PATH}")
        r = self._ssh.scp_to(_LOCAL_SETUP_PATH, self._setup_path)
        if not r.success:
            raise AtspiError(
                f"failed to scp setup.sh to {self._setup_path}: {r.stderr}",
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

        Does NOT raise on per-op failure (worker batch may legitimately have
        a failing `verify` you want to inspect). Raises `AtspiError` only on
        transport/parse failure or a batch-level error from the worker.
        """
        self._ensure_deployed()
        batch = {"ops": ops, "init": init or {"force_atspi_init": True}}
        payload = json.dumps(batch, separators=(",", ":"))
        timeout = timeout if timeout is not None else self._call_timeout

        if len(payload.encode("utf-8")) <= inline_max_bytes:
            cmd = f"python3 {shlex.quote(self._worker_path)} {shlex.quote(payload)}"
            r = self._ssh.run(cmd, timeout=timeout)
        else:
            remote_payload = (
                f"{self._payload_dir.rstrip('/')}/mosdat_atspi_payload_"
                f"{uuid.uuid4().hex}.json"
            )
            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8",
            ) as tf:
                tf.write(payload)
                local_payload = Path(tf.name)
            try:
                scp_res = self._ssh.scp_to(local_payload, remote_payload)
                if not scp_res.success:
                    raise AtspiError(
                        f"failed to scp payload to {remote_payload}: "
                        f"{scp_res.stderr}", stderr=scp_res.stderr)
                cmd = (
                    f"python3 {shlex.quote(self._worker_path)} "
                    f"< {shlex.quote(remote_payload)}"
                )
                r = self._ssh.run(cmd, timeout=timeout)
                # Best-effort cleanup; failure not fatal.
                self._ssh.run(f"rm -f {shlex.quote(remote_payload)}",
                              timeout=10)
            finally:
                try:
                    local_payload.unlink()
                except OSError:
                    pass

        if not r.success and not r.stdout:
            raise AtspiError(
                f"worker ssh call failed (rc={r.returncode}): {r.stderr}",
                stderr=r.stderr)
        try:
            result = json.loads(r.stdout)
        except Exception as e:
            raise AtspiError(
                f"could not parse worker stdout as JSON: {e!r}; "
                f"first 500 bytes: {r.stdout[:500]!r}",
                stderr=r.stderr) from e
        return result

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
            raise AtspiError(
                f"find failed: role={role!r} name={name!r} "
                f"name_substr={name_substr} app_filter={op['app_filter']!r}",
                result=result)
        return node

    def click(
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
            raise AtspiError(
                f"click failed at find: role={role!r} name={name!r}",
                result=result)
        if not act_res.get("ok"):
            raise AtspiError(
                f"click failed at do_action: role={role!r} name={name!r} "
                f"idx={action_idx}",
                result=result)
        return act_res

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
        """Walk the AT-SPI tree under the matching app. Returns node list.

        Tree dumps can be 50 KB+ on the response side; the worker prints
        pretty JSON which is parsed here.
        """
        op = {
            "id": "1", "op": "tree_dump", "max_depth": int(max_depth),
            "app_filter": app_filter or self._app_filter,
        }
        # Tree dumps respond large but request is small — inline payload fine.
        result = self.run_batch([op], timeout=timeout)
        res = result["results"].get("1", {})
        if not res.get("ok"):
            raise AtspiError(
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
            raise AtspiError("wait_for requires `any` or `all`")
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
        # SSH timeout = wait_for timeout + generous buffer for round-trip.
        ssh_timeout = int(timeout) + 30
        result = self.run_batch([op], timeout=ssh_timeout)
        res = result["results"].get("1", {})
        if not res.get("ok"):
            raise AtspiError(
                f"wait_for timed out or failed: {res.get('error')}",
                result=result)
        return res
