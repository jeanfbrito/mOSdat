"""Unit tests for UiaClient.click pointer/action/hint mode selection.

Windows analog of `tests/test_atspi_pointer_mode.py`. Mirrors the same
test cases against the UIA driver. Covers the `via=` kwarg, pointer-mode
bbox-center resolution, the ElementFromPoint verify subtree-match rule,
retry/fail semantics, and the run_batch passthrough.

All SSH calls are mocked; no Windows VM and no real UIA tree required.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Force-reload real automation.uia.client in case a previous test stubbed
# the package. Mirrors the protection in test_atspi_pointer_mode.py.
_PROJ = Path(__file__).parent.parent
for _name in list(sys.modules):
    if _name == "automation.uia" or _name.startswith("automation.uia."):
        sys.modules.pop(_name, None)

from automation.uia.client import UiaClient, UiaError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(*, use_session1: bool = False):
    """Test helper. Defaults to use_session1=False because the click-routing
    tests below mock `run_batch` directly and the run_batch-passthrough test
    asserts the legacy argv shape. Session-1 transport has its own test class
    that opts in explicitly."""
    ssh = MagicMock()
    client = UiaClient(ssh, use_session1=use_session1)
    client._worker_deployed = True  # skip deploy
    return client


# ---------------------------------------------------------------------------
# via routing
# ---------------------------------------------------------------------------

class TestViaRouting:

    def test_default_via_is_pointer(self):
        client = _make_client()
        with patch.object(client, "_click_via_pointer",
                          return_value={"ok": True}) as mock_p, \
             patch.object(client, "_click_via_action") as mock_a:
            client.click("Button", name="Connect")
        mock_p.assert_called_once()
        mock_a.assert_not_called()

    def test_via_action_explicit(self):
        client = _make_client()
        with patch.object(client, "_click_via_action",
                          return_value={"ok": True}) as mock_a, \
             patch.object(client, "_click_via_pointer") as mock_p:
            client.click("Button", name="Connect", via="action")
        mock_a.assert_called_once()
        mock_p.assert_not_called()

    def test_via_hint_explicit(self):
        client = _make_client()
        with patch.object(client, "_click_via_hint",
                          return_value={"ok": True}) as mock_h, \
             patch.object(client, "_click_via_pointer") as mock_p:
            client.click("Button", name="Connect", via="hint")
        mock_h.assert_called_once()
        mock_p.assert_not_called()

    def test_via_unknown_raises(self):
        client = _make_client()
        with pytest.raises(UiaError, match="unknown via="):
            client.click("Button", name="Connect", via="banana")

    def test_input_injector_kwarg_ignored(self):
        """Windows pointer-mode moves cursor on the VM. The host-side
        `input_injector` kwarg is accepted for symmetry with AtspiClient
        but must NOT raise even when omitted, and must NOT be required."""
        client = _make_client()
        with patch.object(client, "_click_via_pointer",
                          return_value={"ok": True}) as mock_p:
            # Without input_injector — should work (unlike AtspiClient).
            client.click("Button", name="Connect")
            # With input_injector — also works (silently ignored).
            client.click("Button", name="Connect", input_injector=MagicMock())
        assert mock_p.call_count == 2


# ---------------------------------------------------------------------------
# pointer-mode mechanics
# ---------------------------------------------------------------------------

class TestPointerMode:

    def _setup(
        self, *, find_extents=True, verify_match="exact",
        find_path="/0/1/2", at_point_name="x",
        at_point_role_override=None
    ):
        """Build a client whose run_batch returns a configurable response.

        Uses the new `find_all` candidate-iteration wire shape: one
        candidate is returned, then the verify decision drives whether
        the click batch follows.
        """
        client = _make_client()

        cand_payload = {
            "role": "Button", "name": "Connect",
            "path": find_path, "depth": 3,
            "n_actions": 1, "action_names": ["invoke"], "child_count": 0,
            "visible": True,
        }
        if find_extents:
            cand_payload["extents"] = {
                "x": 100, "y": 200, "width": 40, "height": 20,
            }

        if verify_match == "exact":
            at_point_path = find_path
            at_point_role = "Button"
        elif verify_match == "child":
            at_point_path = find_path + "/0"
            at_point_role = "Text"
        elif verify_match == "ancestor":
            at_point_path = "/0/1"
            at_point_role = "Pane"
        elif verify_match == "miss":
            at_point_path = "/9/9/9"
            at_point_role = "Window"
        else:
            raise ValueError(verify_match)

        if at_point_role_override is not None:
            at_point_role = at_point_role_override

        def fake_run_batch(ops, **kwargs):
            # find_all batch (replaces self.find for candidate iteration)
            if len(ops) == 1 and ops[0].get("op") == "find_all":
                return {"ok": True, "results": {"1": {
                    "ok": True, "candidates": [cand_payload],
                }}}
            # legacy find batch (still used by hint/action modes)
            if len(ops) == 1 and ops[0].get("op") == "find":
                payload = {"ok": True, **cand_payload}
                return {"ok": True, "results": {"1": payload}}
            # move + verify combo (pointer mode middle stage)
            ids = {o.get("id") for o in ops}
            if ids == {"m", "v"}:
                v_op = next(o for o in ops if o["id"] == "v")
                return {"ok": True, "results": {
                    "m": {"ok": True, "x": v_op["x"], "y": v_op["y"]},
                    "v": {"ok": True, "role": at_point_role, "name": at_point_name,
                          "path": at_point_path,
                          "x": v_op["x"], "y": v_op["y"], "under_app": True},
                }}
            # click_cursor final stage
            if len(ops) == 1 and ops[0].get("op") == "click_cursor":
                return {"ok": True, "results": {"c": {
                    "ok": True, "x": ops[0]["x"], "y": ops[0]["y"],
                    "button": "left",
                }}}
            raise AssertionError(f"unexpected op batch {ops}")

        client.run_batch = MagicMock(side_effect=fake_run_batch)
        return client, cand_payload

    def test_via_pointer_resolves_center_and_clicks(self):
        client, _ = self._setup(verify_match="exact")
        result = client.click("Button", name="Connect")
        # Center of (100,200,40x20) = (120, 210).
        # Expect: 1 find batch + 1 move/verify batch + 1 click batch = 3 calls
        assert client.run_batch.call_count == 3
        assert result["ok"] is True
        assert result["via"] == "pointer"
        assert result["x"] == 120
        assert result["y"] == 210
        assert result["verified"] is True

    def test_via_pointer_subtree_child_match_clicks(self):
        client, _ = self._setup(verify_match="child")
        result = client.click("Button", name="Connect")
        assert result["ok"] is True
        assert result["verified"] is True

    def test_via_pointer_ancestor_match_clicks(self):
        client, _ = self._setup(verify_match="ancestor")
        result = client.click("Button", name="Connect")
        assert result["ok"] is True
        assert result["verified"] is True

    def test_via_pointer_same_role_honors_name_substr(self):
        client, _ = self._setup(
            verify_match="miss",
            at_point_role_override="Button",
            at_point_name="Southlogic rocketchat.jeanbrito.com",
        )
        result = client.click(
            "Button", name="rocketchat.jeanbrito.com", name_substr=True
        )
        assert result["ok"] is True
        assert result["verified"] is True

    def test_via_pointer_verify_mismatch_rejects_and_raises(self):
        """Single mismatching candidate → reject, raise (no retry; rely on
        candidate iteration when multiple matches exist)."""
        client, _ = self._setup(verify_match="miss")
        with pytest.raises(UiaError, match="all 1 candidates rejected"):
            client.click("Button", name="Connect")
        # find_all + 1 move/verify = 2 batch calls. No click.
        assert client.run_batch.call_count == 2

    def test_via_pointer_widget_without_extents_raises(self):
        """All candidates lacking extents → 'all candidates rejected' with
        no_extents trace entries."""
        client, _ = self._setup(find_extents=False)
        with pytest.raises(UiaError, match="all 1 candidates rejected"):
            client.click("Button", name="Connect")


# ---------------------------------------------------------------------------
# verify_unsupported (ElementFromPoint returns nothing — proceed anyway)
# ---------------------------------------------------------------------------

class TestVerifyUnsupported:

    def test_no_node_at_point_proceeds_with_click(self):
        """Chromium occasionally returns None from ElementFromPoint for
        renderer-process coordinates. Worker maps that to
        `error: no_node_at_point` which the client must treat as
        verify_skipped (proceed) rather than mismatch (retry/fail)."""
        client = _make_client()
        find_payload = {
            "ok": True, "role": "Button", "name": "Connect",
            "path": "/0/1/2", "depth": 3,
            "n_actions": 1, "action_names": ["invoke"], "child_count": 0,
            "extents": {"x": 100, "y": 200, "width": 40, "height": 20},
        }

        def fake_run_batch(ops, **kwargs):
            if len(ops) == 1 and ops[0].get("op") == "find_all":
                cand = {k: v for k, v in find_payload.items() if k != "ok"}
                return {"ok": True, "results": {"1": {
                    "ok": True, "candidates": [cand],
                }}}
            if len(ops) == 1 and ops[0].get("op") == "find":
                return {"ok": True, "results": {"1": find_payload}}
            ids = {o.get("id") for o in ops}
            if ids == {"m", "v"}:
                v_op = next(o for o in ops if o["id"] == "v")
                return {"ok": False, "results": {
                    "m": {"ok": True, "x": v_op["x"], "y": v_op["y"]},
                    "v": {"ok": False, "error": "no_node_at_point",
                          "x": v_op["x"], "y": v_op["y"]},
                }}
            if len(ops) == 1 and ops[0].get("op") == "click_cursor":
                return {"ok": True, "results": {"c": {
                    "ok": True, "x": ops[0]["x"], "y": ops[0]["y"],
                    "button": "left",
                }}}
            raise AssertionError(f"unexpected ops {ops}")

        client.run_batch = MagicMock(side_effect=fake_run_batch)
        result = client.click("Button", name="Connect")
        assert result["ok"] is True
        assert result["verify_skipped"] is True
        assert result["verified"] is False
        # find + move/verify + click = 3 calls (no retry).
        assert client.run_batch.call_count == 3

    def test_pointer_skip_verify_when_path_empty(self):
        """ElementFromPoint succeeds (ok: true) but returns empty path and
        empty role. Treat as verify_skipped (proceed with click) rather than
        mismatch (retry/fail)."""
        client = _make_client()
        find_payload = {
            "ok": True, "role": "Button", "name": "Connect",
            "path": "/0/1/2", "depth": 3,
            "n_actions": 1, "action_names": ["invoke"], "child_count": 0,
            "extents": {"x": 100, "y": 200, "width": 40, "height": 20},
        }

        def fake_run_batch(ops, **kwargs):
            if len(ops) == 1 and ops[0].get("op") == "find_all":
                cand = {k: v for k, v in find_payload.items() if k != "ok"}
                return {"ok": True, "results": {"1": {
                    "ok": True, "candidates": [cand],
                }}}
            if len(ops) == 1 and ops[0].get("op") == "find":
                return {"ok": True, "results": {"1": find_payload}}
            ids = {o.get("id") for o in ops}
            if ids == {"m", "v"}:
                v_op = next(o for o in ops if o["id"] == "v")
                return {"ok": True, "results": {
                    "m": {"ok": True, "x": v_op["x"], "y": v_op["y"]},
                    "v": {"ok": True, "role": "", "name": "x",
                          "path": "", "x": v_op["x"], "y": v_op["y"]},
                }}
            if len(ops) == 1 and ops[0].get("op") == "click_cursor":
                return {"ok": True, "results": {"c": {
                    "ok": True, "x": ops[0]["x"], "y": ops[0]["y"],
                    "button": "left",
                }}}
            raise AssertionError(f"unexpected ops {ops}")

        client.run_batch = MagicMock(side_effect=fake_run_batch)
        result = client.click("Button", name="Connect")
        assert result["ok"] is True
        assert result["verify_skipped"] is True
        assert result["verified"] is False
        # find + move/verify + click = 3 calls (no retry).
        assert client.run_batch.call_count == 3


# ---------------------------------------------------------------------------
# run_batch passthrough
# ---------------------------------------------------------------------------

class TestRunBatchPassthrough:

    def test_run_batch_forwards_ops_to_ssh(self):
        """Verify run_batch builds the right SSH command + payload."""
        client = _make_client()
        ssh = client._ssh
        ssh.run.return_value = MagicMock(
            success=True, returncode=0,
            stdout='{"ok": true, "results": {"1": {"ok": true}}}',
            stderr="",
        )
        result = client.run_batch(
            [{"id": "1", "op": "find", "role": "Button"}],
        )
        assert result["ok"] is True
        assert result["results"]["1"]["ok"] is True
        # The command must include `python` + the worker path, and the
        # JSON payload must contain "find" and "Button".
        called_cmd = ssh.run.call_args[0][0]
        assert "python" in called_cmd
        assert "mosdat_uia_worker.py" in called_cmd
        assert "find" in called_cmd
        assert "Button" in called_cmd


# ---------------------------------------------------------------------------
# session-1 schtasks transport (Session 0/1 fix)
# ---------------------------------------------------------------------------

class TestRunBatchSession1:
    """Verifies the schtasks InteractiveToken transport that crosses the
    Session 0 (SSH) / Session 1 (interactive desktop) boundary.

    Why this matters: Windows OpenSSH lands commands in Session 0;
    pywinauto.Desktop(backend='uia').windows() bound to that session sees
    zero windows. The client must spawn the worker in Session 1 via
    schtasks and read its result file back.
    """

    def _make_session1_client(self):
        ssh = MagicMock()
        client = UiaClient(ssh, use_session1=True,
                           session1_poll_interval=0.0)
        client._worker_deployed = True
        return client, ssh

    def test_session1_scps_request_then_creates_task_then_polls_result(self):
        client, ssh = self._make_session1_client()
        ssh.scp_to.return_value = MagicMock(success=True, returncode=0,
                                            stdout="", stderr="")

        result_json = '{"ok": true, "results": {"1": {"ok": true}}}'
        # ssh.run is invoked 3+ times in order:
        #   1. schtasks Create/Run encoded PS script
        #   2..N. poll for the result file (first miss returns empty stdout)
        #   final. cleanup
        run_results = [
            # 1. schtasks Create/Run succeeds
            MagicMock(success=True, returncode=0,
                      stdout="uia-launch:ok\n", stderr=""),
            # 2. first poll: file not present yet (Get-Content emits "")
            MagicMock(success=True, returncode=0, stdout="", stderr=""),
            # 3. second poll: result file populated
            MagicMock(success=True, returncode=0,
                      stdout=result_json, stderr=""),
            # 4. cleanup (Remove-Item + schtasks Delete)
            MagicMock(success=True, returncode=0, stdout="", stderr=""),
        ]
        ssh.run.side_effect = run_results

        out = client.run_batch(
            [{"id": "1", "op": "find", "role": "Button"}],
        )
        assert out["ok"] is True
        assert out["results"]["1"]["ok"] is True

        # The request JSON was scp'd to a `uia-req-<uuid>.json` path.
        scp_dest = ssh.scp_to.call_args[0][1]
        assert "uia-req-" in scp_dest
        assert scp_dest.endswith(".json")

        # The first ssh.run() was a base64-encoded PowerShell command. Decode
        # it and verify it contains the schtasks XML w/ InteractiveToken and
        # the `--out` flag pointing at the matching result path.
        first_cmd = ssh.run.call_args_list[0][0][0]
        assert first_cmd.startswith("powershell.exe")
        assert "-EncodedCommand" in first_cmd
        import base64
        enc = first_cmd.rsplit(" ", 1)[-1]
        decoded = base64.b64decode(enc).decode("utf-16-le")
        assert "InteractiveToken" in decoded
        assert "schtasks /Create" in decoded
        assert "schtasks /Run" in decoded
        assert "file:" in decoded
        assert "--out" in decoded
        # uuid token threads through req+res+task name
        token = scp_dest.split("uia-req-")[1].split(".json")[0]
        assert token in decoded
        assert f"uia-res-{token}.json" in decoded
        assert f"mosdat-uia-{token}" in decoded

        # Cleanup ran after success.
        cleanup_cmd = ssh.run.call_args_list[-1][0][0]
        assert "schtasks /Delete" in base64.b64decode(
            cleanup_cmd.rsplit(" ", 1)[-1]
        ).decode("utf-16-le", errors="ignore") or \
            "schtasks /Delete" in cleanup_cmd or \
            "Remove-Item" in cleanup_cmd

    def test_session1_scp_failure_raises(self):
        client, ssh = self._make_session1_client()
        ssh.scp_to.return_value = MagicMock(
            success=False, returncode=1, stdout="", stderr="perm denied",
        )
        # Cleanup may still be attempted; ssh.run is fine.
        ssh.run.return_value = MagicMock(
            success=True, returncode=0, stdout="", stderr="",
        )
        with pytest.raises(UiaError, match="failed to scp request"):
            client.run_batch(
                [{"id": "1", "op": "find", "role": "Button"}],
            )

    def test_session1_schtasks_create_failure_raises(self):
        client, ssh = self._make_session1_client()
        ssh.scp_to.return_value = MagicMock(success=True, returncode=0,
                                            stdout="", stderr="")
        # First ssh.run = schtasks Create/Run = fails.
        # Subsequent runs are cleanup attempts.
        ssh.run.side_effect = [
            MagicMock(success=False, returncode=1, stdout="",
                      stderr="ERROR: Access denied"),
            MagicMock(success=True, returncode=0, stdout="", stderr=""),
        ]
        with pytest.raises(UiaError, match="schtasks Create/Run failed"):
            client.run_batch(
                [{"id": "1", "op": "find", "role": "Button"}],
            )

    def test_session1_result_poll_timeout_raises(self, monkeypatch):
        client, ssh = self._make_session1_client()
        ssh.scp_to.return_value = MagicMock(success=True, returncode=0,
                                            stdout="", stderr="")
        # Create OK, all polls return empty (file never appears).
        ssh.run.return_value = MagicMock(
            success=True, returncode=0, stdout="", stderr="",
        )
        # Cut both the per-op timeout and the buffer so the test is fast.
        import automation.uia.client as _client_mod
        monkeypatch.setattr(_client_mod, "DEFAULT_SESSION1_BUFFER_S", 0)
        with pytest.raises(UiaError, match="timed out"):
            client.run_batch(
                [{"id": "1", "op": "find", "role": "Button"}],
                timeout=0,
            )
