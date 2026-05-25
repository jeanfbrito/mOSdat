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
import time
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

_ATSPI_SESSION_PREAMBLE = """DESKTOP_ENV=$(mktemp)
for pid in $(pgrep -u $(id -u) 'plasmashell|gnome-shell|kwin_x11|kwin_wayland' 2>/dev/null); do
  tr '\\0' '\\n' < /proc/$pid/environ 2>/dev/null | grep -E '^(DISPLAY|XAUTHORITY|DBUS_SESSION_BUS_ADDRESS|AT_SPI_BUS_ADDRESS)=' > "$DESKTOP_ENV"
  grep -q '^DISPLAY=' "$DESKTOP_ENV" && grep -q '^XAUTHORITY=' "$DESKTOP_ENV" && break
done
[ -s "$DESKTOP_ENV" ] && . "$DESKTOP_ENV"
rm -f "$DESKTOP_ENV"
if [ -z "${AT_SPI_BUS_ADDRESS:-}" ] && command -v busctl >/dev/null 2>&1; then
  _MOSDAT_ATSPI=$(busctl --user call org.a11y.Bus /org/a11y/bus org.a11y.Bus GetAddress 2>/dev/null | sed -e 's/^s //' -e 's/^"//' -e 's/"$//')
  [ -n "$_MOSDAT_ATSPI" ] && export AT_SPI_BUS_ADDRESS="$_MOSDAT_ATSPI"
fi
export DISPLAY=${DISPLAY:-:0}
[ -n "${XAUTHORITY:-}" ] && export XAUTHORITY
"""

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

    def deploy_worker(self) -> None:
        """SCP the worker to the VM. Idempotent."""
        self._ensure_deployed()

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

    def _with_session_env(self, command: str) -> str:
        """Run the worker in the logged-in desktop session when available."""
        return _ATSPI_SESSION_PREAMBLE + command

    @staticmethod
    def _is_atspi_not_ready(result: dict) -> bool:
        errors = result.get("errors") or []
        if any("atspi_not_ready" in str(err) for err in errors):
            return True
        for item in (result.get("results") or {}).values():
            if isinstance(item, dict) and item.get("error") == "atspi_not_ready":
                return True
        return False

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
        remote_payload: Optional[str] = None
        local_payload: Optional[Path] = None

        if len(payload.encode("utf-8")) <= inline_max_bytes:
            cmd = f"python3 {shlex.quote(self._worker_path)} {shlex.quote(payload)}"
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
            finally:
                if local_payload is not None:
                    try:
                        local_payload.unlink()
                    except OSError:
                        pass

        try:
            for attempt in range(3):
                r = self._ssh.run(self._with_session_env(cmd), timeout=timeout)
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
                if not self._is_atspi_not_ready(result) or attempt == 2:
                    return result
                time.sleep(0.5 * (attempt + 1))
        finally:
            if remote_payload is not None:
                self._ssh.run(f"rm -f {shlex.quote(remote_payload)}", timeout=10)

        raise AtspiError("AT-SPI worker retry loop exited unexpectedly")

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
        via: str = "pointer",
        input_injector: Any = None,
        motion: Optional[str] = None,
        dwell_ms: Optional[int] = None,
    ) -> dict:
        """Click an accessible widget.

        via='pointer' (default): find widget → resolve bbox center → move
            cursor via ``input_injector`` → verify with
            ``get_accessible_at_point`` → real button click. Exercises the
            real input event chain; cursor visible in recordings. Requires
            ``input_injector`` and a widget with Component extents.
        via='action': legacy semantic ``do_action`` invocation; no cursor
            motion. Use for fast smokes, validation, warm-up, or off-screen
            widgets that lack Component extents.
        via='hint': hybrid — move cursor to widget area + dwell, THEN
            invoke ``do_action``. No real button event is emitted (so the
            click handler must be wired to the AT-SPI action, like
            Fuselage ToggleSwitch), but the recording shows the cursor
            arriving + pausing + state change. Combines ``action``'s
            reliability with ``pointer``'s visual context. Uses
            ``clickable_extents`` (an ancestor bbox computed by the worker)
            when the target itself is zero-extent; otherwise falls back to
            the target's own extents. Default dwell is 300 ms so the pause
            is visible in the recording.
        """
        if via == "pointer":
            if input_injector is None:
                raise AtspiError(
                    "pointer-mode click requires input_injector; pass "
                    "input_injector=runner.injector or use via='action'"
                )
            return self._click_via_pointer(
                role, name, name_substr=name_substr,
                app_filter=app_filter, timeout=timeout,
                input_injector=input_injector,
                motion=motion, dwell_ms=dwell_ms,
            )
        if via == "action":
            return self._click_via_action(
                role, name, name_substr=name_substr,
                action_idx=action_idx, app_filter=app_filter,
                timeout=timeout, input_injector=input_injector,
            )
        if via == "hint":
            if input_injector is None:
                raise AtspiError(
                    "hint-mode click requires input_injector; pass "
                    "input_injector=runner.injector or use via='action'"
                )
            return self._click_via_hint(
                role, name, name_substr=name_substr,
                action_idx=action_idx, app_filter=app_filter,
                timeout=timeout, input_injector=input_injector,
                motion=motion, dwell_ms=dwell_ms,
            )
        raise AtspiError(
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
        input_injector: Any,
        motion: Optional[str] = None,
        dwell_ms: Optional[int] = None,
    ) -> dict:
        """Hint-mode click: visible cursor motion + dwell, then do_action.

        Algorithm:
            1. find widget → prefer ``clickable_extents`` (parent walk
               computed by worker), else fall back to the target's own
               ``extents``.
            2. compute bbox center.
            3. move cursor via input_injector._position_cursor with a
               default 300 ms dwell so the reviewer SEES the cursor land.
            4. invoke do_action on the original target — this is what
               actually changes state. No VNC button event is emitted.

        Used for zero-extent widgets (e.g. Fuselage ToggleSwitch) where
        pointer-mode misses the 1×1 hidden input but action-mode would
        leave the reviewer with no visual context for the activation.
        """
        af = app_filter or self._app_filter
        find_res = self.find(
            role, name, name_substr=name_substr,
            app_filter=af, timeout=timeout,
        )
        bbox = find_res.get("clickable_extents") or find_res.get("extents")
        if not bbox:
            raise AtspiError(
                "hint-mode: widget has no usable extents (neither target "
                f"nor 5-deep ancestor); use via='action' for role={role!r} "
                f"name={name!r}",
                result={"find": find_res},
            )
        cx = int(bbox["x"] + bbox["width"] / 2)
        cy = int(bbox["y"] + bbox["height"] / 2)
        hint_dwell = dwell_ms if dwell_ms is not None else 300
        # Move cursor + dwell — purely visual; no button event fires here.
        input_injector._position_cursor(
            cx, cy, motion=motion, dwell_ms=hint_dwell,
        )
        # Semantic action on the original target (the part that actually
        # toggles state). Re-find inside the batch keeps the path resolution
        # atomic with the action invocation.
        ops = [
            {"id": "f", "op": "find", "role": role, "name": name,
             "name_substr": bool(name_substr), "app_filter": af},
            {"id": "a", "op": "do_action", "from_id": "f",
             "action_idx": int(action_idx), "app_filter": af},
        ]
        result = self.run_batch(ops, timeout=timeout)
        find2 = result["results"].get("f", {})
        act = result["results"].get("a", {})
        if not find2.get("ok"):
            raise AtspiError(
                f"hint-mode action stage failed at find: role={role!r} "
                f"name={name!r}", result=result)
        if not act.get("ok"):
            raise AtspiError(
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
        input_injector: Any = None,  # accepted + ignored for caller symmetry
    ) -> dict:
        """Wait for a widget and invoke its AT-SPI action in one batch.

        The wait op returns the matched accessible path, and do_action uses
        that same path. This avoids races where a separate wait_for step sees
        a transient renderer node, but a later action process cannot re-find it.
        """
        af = app_filter or self._app_filter
        wait_ms = 5000
        if timeout is not None:
            wait_ms = max(500, int(max(float(timeout) - 1.0, 0.5) * 1000))
        ops = [
            {
                "id": "find", "op": "wait_for",
                "any": [{
                    "role": role, "name": name,
                    "name_substr": bool(name_substr),
                    "app_filter": af,
                }],
                "timeout_ms": wait_ms, "interval_ms": 100,
                "app_filter": af,
            },
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
        # Tag the result so callers / tests can distinguish modes.
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
        input_injector: Any,
        motion: Optional[str] = None,
        dwell_ms: Optional[int] = None,
    ) -> dict:
        """Pointer-mode click: real cursor + verify + real button click.

        Algorithm:
            1. find widget → grab Component extents.
            2. compute bbox center.
            3. move cursor via input_injector._position_cursor.
            4. verify cursor lands on (subtree of) the expected node via
               get_accessible_at_point.
            5. on mismatch, retry once (re-find, re-move, re-verify).
            6. real button-1 click via input_injector.vnc.click.

        Verify accepts exact-path match OR ancestor/descendant subtree
        match (the top node under the cursor may be a child label/icon
        of the clickable widget, or the widget container above a probe
        target — both are still on the intended widget).
        """
        af = app_filter or self._app_filter
        last_err: Optional[str] = None
        for attempt in (1, 2):
            find_res = self.find(
                role, name, name_substr=name_substr,
                app_filter=af, timeout=timeout,
            )
            extents = find_res.get("extents")
            if not extents:
                raise AtspiError(
                    "widget has no Component extents — pointer-mode "
                    f"unsupported; use via='action' for role={role!r} "
                    f"name={name!r}",
                    result={"find": find_res},
                )
            expected_path = find_res.get("path") or ""
            cx = int(extents["x"] + extents["width"] / 2)
            cy = int(extents["y"] + extents["height"] / 2)

            input_injector._position_cursor(
                cx, cy, motion=motion, dwell_ms=dwell_ms,
            )

            verify_batch = self.run_batch(
                [{"id": "v", "op": "get_at_point",
                  "x": cx, "y": cy, "app_filter": af}],
                timeout=timeout,
            )
            v = verify_batch["results"].get("v", {})
            actual_path = v.get("path") or ""
            actual_role = v.get("role", "")
            actual_name = v.get("name", "")
            # Chromium ATK does not implement get_accessible_at_point — it
            # returns None for every renderer-process coordinate. Distinguish
            # "API unimplemented" (verify_skipped — proceed) from "API
            # returned a different node" (mismatch — retry/fail).
            verify_unsupported = (
                not v.get("ok")
                and v.get("error") == "no_node_at_point"
            )
            ok_match = False
            if v.get("ok"):
                if actual_path == expected_path:
                    ok_match = True
                elif expected_path and actual_path.startswith(expected_path):
                    ok_match = True
                elif actual_path and expected_path.startswith(actual_path):
                    ok_match = True
            if ok_match or verify_unsupported:
                input_injector.vnc.click(cx, cy, button=1)
                return {
                    "ok": True, "via": "pointer", "x": cx, "y": cy,
                    "verified": bool(ok_match),
                    "verify_skipped": bool(verify_unsupported),
                    "extents": extents,
                    "find": find_res,
                    "verify": {"role": actual_role, "name": actual_name,
                               "path": actual_path},
                }
            last_err = (
                f"cursor at ({cx},{cy}) reports "
                f"{actual_role!r}/{actual_name!r} path={actual_path!r}, "
                f"expected path={expected_path!r}"
            )

        raise AtspiError(
            f"pointer-mode verify failed after retry: {last_err}",
            result={"last_verify": v if 'v' in locals() else None},
        )

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
