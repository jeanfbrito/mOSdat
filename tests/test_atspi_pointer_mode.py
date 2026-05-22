"""Unit tests for AtspiClient.click pointer/action mode selection.

Covers the new ``via=`` kwarg, pointer-mode bbox-center resolution, the
get_accessible_at_point verify subtree-match rule, retry/fail semantics,
and the error paths for missing input_injector / missing extents / bad via.

All SSH calls are mocked; no network and no real AT-SPI bus required.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Force-reload real automation.atspi.client. test_atspi_dump_cli.py installs
# a sys.modules stub for "automation.atspi" (as a flat module, not a package)
# and never restores it; if pytest collects that test file first we'd hit
# "automation.atspi is not a package". Load by file path to bypass the stub.
_PROJ = Path(__file__).parent.parent
# Drop polluted stubs (test_atspi_dump_cli installs a flat-module stub for
# "automation.atspi" and never restores it). Forcing re-import lets the real
# package __init__.py load with proper __path__.
for _name in list(sys.modules):
    if _name == "automation.atspi" or _name.startswith("automation.atspi."):
        sys.modules.pop(_name, None)

# Now real import works.
from automation.atspi.client import AtspiClient, AtspiError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(run_batch_side_effect=None, find_result=None):
    """Build an AtspiClient with run_batch + find patched.

    `find_result` is the dict returned by self.find() (excluding the
    {"ok": True} wrapper handled by find()); pass None to omit the
    .find patch and let run_batch handle find calls.
    """
    ssh = MagicMock()
    client = AtspiClient(ssh)
    client._worker_deployed = True  # skip deploy
    return client


def _injector():
    inj = MagicMock()
    inj.vnc = MagicMock()
    return inj


# ---------------------------------------------------------------------------
# via routing
# ---------------------------------------------------------------------------

class TestViaRouting:

    def test_default_via_is_pointer(self):
        client = _make_client()
        inj = _injector()
        with patch.object(client, "_click_via_pointer",
                          return_value={"ok": True}) as mock_p, \
             patch.object(client, "_click_via_action") as mock_a:
            client.click("push button", name="Login", input_injector=inj)
        mock_p.assert_called_once()
        mock_a.assert_not_called()

    def test_via_pointer_requires_input_injector(self):
        client = _make_client()
        with pytest.raises(AtspiError, match="requires input_injector"):
            client.click("push button", name="Login")  # via defaults to pointer

    def test_via_action_explicit(self):
        client = _make_client()
        inj = _injector()
        with patch.object(client, "_click_via_action",
                          return_value={"ok": True}) as mock_a, \
             patch.object(client, "_click_via_pointer") as mock_p:
            client.click("push button", name="Login", via="action",
                         input_injector=inj)
        mock_a.assert_called_once()
        # input_injector is forwarded but action-mode ignores it.
        kwargs = mock_a.call_args.kwargs
        assert kwargs.get("input_injector") is inj
        mock_p.assert_not_called()

    def test_via_unknown_raises(self):
        client = _make_client()
        with pytest.raises(AtspiError, match="unknown via="):
            client.click("push button", name="Login", via="banana",
                         input_injector=_injector())


# ---------------------------------------------------------------------------
# pointer-mode mechanics
# ---------------------------------------------------------------------------

class TestPointerMode:

    def _setup(self, *, find_extents=True, verify_match="exact",
               find_path="/0/1/2"):
        """Build a client whose .find() returns a fake widget and whose
        run_batch (for get_at_point) returns a configurable match status.
        """
        client = _make_client()
        inj = _injector()

        find_payload = {
            "ok": True, "role": "push button", "name": "Login",
            "path": find_path, "depth": 3,
            "n_actions": 1, "action_names": ["press"], "child_count": 0,
        }
        if find_extents:
            find_payload["extents"] = {
                "x": 100, "y": 200, "width": 40, "height": 20,
            }

        # Match modes:
        if verify_match == "exact":
            at_point_path = find_path
            at_point_role = "push button"
        elif verify_match == "child":
            at_point_path = find_path + "/0"   # descendant
            at_point_role = "label"
        elif verify_match == "ancestor":
            # at_point reports an ancestor — expected_path startswith actual.
            at_point_path = "/0/1"
            at_point_role = "panel"
        elif verify_match == "miss":
            at_point_path = "/9/9/9"
            at_point_role = "frame"
        else:
            raise ValueError(verify_match)

        def fake_run_batch(ops, **kwargs):
            op = ops[0]
            if op.get("op") == "find":
                return {"ok": True, "results": {"1": find_payload}}
            if op.get("op") == "get_at_point":
                return {"ok": True, "results": {"v": {
                    "ok": True, "role": at_point_role, "name": "x",
                    "path": at_point_path,
                    "x": op["x"], "y": op["y"], "under_app": True,
                }}}
            raise AssertionError(f"unexpected op {op}")

        client.run_batch = MagicMock(side_effect=fake_run_batch)
        return client, inj, find_payload

    def test_via_pointer_resolves_center_and_moves(self):
        client, inj, _ = self._setup(verify_match="exact")
        client.click("push button", name="Login", input_injector=inj)
        # Center of (100,200,40x20) = (120, 210).
        inj._position_cursor.assert_called_once_with(
            120, 210, motion=None, dwell_ms=None,
        )

    def test_via_pointer_verify_exact_match_clicks(self):
        client, inj, _ = self._setup(verify_match="exact")
        result = client.click("push button", name="Login", input_injector=inj)
        inj.vnc.click.assert_called_once_with(120, 210, button=1)
        assert result["ok"] is True
        assert result["via"] == "pointer"
        assert result["verified"] is True

    def test_via_pointer_verify_subtree_child_match_clicks(self):
        client, inj, _ = self._setup(verify_match="child")
        client.click("push button", name="Login", input_injector=inj)
        inj.vnc.click.assert_called_once_with(120, 210, button=1)

    def test_via_pointer_verify_ancestor_match_clicks(self):
        client, inj, _ = self._setup(verify_match="ancestor")
        client.click("push button", name="Login", input_injector=inj)
        inj.vnc.click.assert_called_once_with(120, 210, button=1)

    def test_via_pointer_verify_mismatch_retries_then_fails(self):
        client, inj, _ = self._setup(verify_match="miss")
        with pytest.raises(AtspiError, match="pointer-mode verify failed"):
            client.click("push button", name="Login", input_injector=inj)
        # 2 attempts × (find + get_at_point) = 4 batch calls.
        assert client.run_batch.call_count == 4
        # And vnc.click never fires on persistent mismatch.
        inj.vnc.click.assert_not_called()

    def test_via_pointer_verify_unsupported_no_node_at_point_proceeds(self):
        """Chromium ATK returns None from get_accessible_at_point. Treat
        the resulting `no_node_at_point` error as verify_skipped, not as a
        mismatch — clicking must still proceed."""
        client = _make_client()
        inj = _injector()
        find_payload = {
            "ok": True, "role": "push button", "name": "Login",
            "path": "/0/1/2", "depth": 3,
            "n_actions": 1, "action_names": ["press"], "child_count": 0,
            "extents": {"x": 100, "y": 200, "width": 40, "height": 20},
        }

        def fake_run_batch(ops, **kwargs):
            op = ops[0]
            if op.get("op") == "find":
                return {"ok": True, "results": {"1": find_payload}}
            if op.get("op") == "get_at_point":
                return {"ok": False, "results": {"v": {
                    "ok": False, "error": "no_node_at_point",
                    "x": op["x"], "y": op["y"],
                }}}
            raise AssertionError(op)

        client.run_batch = MagicMock(side_effect=fake_run_batch)
        result = client.click("push button", name="Login", input_injector=inj)
        # Click fires despite verify being unsupported.
        inj.vnc.click.assert_called_once_with(120, 210, button=1)
        assert result["ok"] is True
        assert result["verify_skipped"] is True
        assert result["verified"] is False
        # No retry — single find + single get_at_point.
        assert client.run_batch.call_count == 2

    def test_via_pointer_widget_without_extents_raises(self):
        client, inj, _ = self._setup(find_extents=False, verify_match="exact")
        with pytest.raises(AtspiError, match="no Component extents"):
            client.click("push button", name="Login", input_injector=inj)
        inj.vnc.click.assert_not_called()
