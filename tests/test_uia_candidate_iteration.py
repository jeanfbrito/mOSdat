"""Unit tests for UIA pointer-mode candidate iteration.

Covers the stale-popover bug: Chromium-based RC exposes duplicate
`role + name` UIA nodes (visible widget + a hidden popover copy) whose
bboxes can overlap unrelated regions. `_click_via_pointer` must:

  * pull all matches via `find_all` (visible first per the visibility
    filter)
  * for each candidate, move cursor + `get_at_point`
  * decision table:
      - `ok=false, error=no_node_at_point`        → skip verify, click
      - `ok=true, empty path + empty role`        → skip verify, click
      - `ok=true, subtree-match (exact/anc/desc)` → verified, click
      - `ok=true, OTHER widget`                   → REJECT, try next
  * raise with the full per-candidate trace if all rejected.

All SSH calls are mocked; no Windows VM required.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_PROJ = Path(__file__).parent.parent
for _name in list(sys.modules):
    if _name == "automation.uia" or _name.startswith("automation.uia."):
        sys.modules.pop(_name, None)

from automation.uia.client import UiaClient, UiaError  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_client():
    ssh = MagicMock()
    client = UiaClient(ssh, use_session1=False)
    client._worker_deployed = True
    return client


def _cand(*, path: str, x: int, y: int, w: int = 40, h: int = 20,
          visible: bool = True, role: str = "Button",
          name: str = "Settings") -> dict:
    return {
        "role": role, "name": name, "path": path, "depth": path.count("/"),
        "n_actions": 1, "action_names": ["invoke"], "child_count": 0,
        "extents": {"x": x, "y": y, "width": w, "height": h},
        "visible": visible, "is_offscreen": not visible,
    }


def _install_fake_batch(client, *, candidates: list[dict],
                        verify_returns: list[dict]) -> MagicMock:
    """Wire run_batch to return:
      * the given `candidates` list to find_all batches,
      * the next entry from `verify_returns` for each move+verify batch,
      * a generic OK click_cursor result.
    Returns the MagicMock so call sites can assert call_count etc.
    """
    verify_iter = iter(verify_returns)

    def fake_run_batch(ops, **kwargs):
        if len(ops) == 1 and ops[0].get("op") == "find_all":
            return {"ok": True, "results": {"1": {
                "ok": True, "candidates": candidates,
            }}}
        ids = {o.get("id") for o in ops}
        if ids == {"m", "v"}:
            v_op = next(o for o in ops if o["id"] == "v")
            v_payload = next(verify_iter)
            return {"ok": True, "results": {
                "m": {"ok": True, "x": v_op["x"], "y": v_op["y"]},
                "v": v_payload,
            }}
        if len(ops) == 1 and ops[0].get("op") == "click_cursor":
            return {"ok": True, "results": {"c": {
                "ok": True, "x": ops[0]["x"], "y": ops[0]["y"],
                "button": "left",
            }}}
        raise AssertionError(f"unexpected ops {ops}")

    mock = MagicMock(side_effect=fake_run_batch)
    client.run_batch = mock
    return mock


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestFirstCandidateMatch:

    def test_first_candidate_subtree_match_clicks(self):
        """Single candidate, subtree match → click without iterating."""
        client = _make_client()
        cands = [_cand(path="/0/1/2", x=100, y=200)]
        mock = _install_fake_batch(
            client, candidates=cands,
            verify_returns=[{
                "ok": True, "role": "Button", "name": "Settings",
                "path": "/0/1/2", "under_app": True,
            }],
        )
        result = client.click("Button", name="Settings")
        assert result["ok"] is True
        assert result["verified"] is True
        assert result["verify_skipped"] is False
        # find_all + 1 verify batch + click = 3 calls
        assert mock.call_count == 3
        assert result["candidate_count"] == 1
        assert result["candidate_idx"] == 0
        trace = result["candidate_trace"]
        assert len(trace) == 1
        assert trace[0]["decision"] == "match"


class TestCandidateIteration:

    def test_first_candidate_mismatch_advances_to_second(self):
        """Stale popover (candidate 0) reports a DIFFERENT widget under
        cursor → reject. Candidate 1 reports subtree match → click."""
        client = _make_client()
        cands = [
            _cand(path="/popover/Settings", x=143, y=627),   # stale
            _cand(path="/sidebar/Settings", x=300, y=400),   # real
        ]
        mock = _install_fake_batch(
            client, candidates=cands,
            verify_returns=[
                # candidate 0 — cursor lands on the chat composer
                {"ok": True, "role": "document web",
                 "name": "Message composer", "path": "/chat/composer",
                 "under_app": True},
                # candidate 1 — exact subtree match
                {"ok": True, "role": "Button", "name": "Settings",
                 "path": "/sidebar/Settings", "under_app": True},
            ],
        )
        result = client.click("Button", name="Settings")
        assert result["ok"] is True
        assert result["verified"] is True
        # Clicked the SECOND candidate.
        assert result["x"] == 320  # 300 + 40/2
        assert result["y"] == 410
        assert result["candidate_idx"] == 1
        assert result["candidate_count"] == 2
        trace = result["candidate_trace"]
        assert [e["decision"] for e in trace] == [
            "reject_mismatch", "match",
        ]
        # find_all + 2 verify batches + click = 4 calls
        assert mock.call_count == 4

    def test_all_candidates_mismatch_raises(self):
        """Every candidate's verify points to an unrelated widget → raise
        with the full trace; no click issued."""
        client = _make_client()
        cands = [
            _cand(path="/popover/Settings", x=143, y=627),
            _cand(path="/popover2/Settings", x=200, y=700),
        ]
        mock = _install_fake_batch(
            client, candidates=cands,
            verify_returns=[
                {"ok": True, "role": "document web", "name": "composer",
                 "path": "/chat/composer", "under_app": True},
                {"ok": True, "role": "Text", "name": "other",
                 "path": "/elsewhere", "under_app": True},
            ],
        )
        with pytest.raises(UiaError, match="all 2 candidates rejected"):
            client.click("Button", name="Settings")
        # find_all + 2 verify batches = 3 calls. No click.
        assert mock.call_count == 3


class TestVerifySkipDecisions:

    def test_empty_at_point_skips_verify_on_first_candidate(self):
        """ok=false + no_node_at_point on first candidate → skip verify,
        click (don't advance to next candidate)."""
        client = _make_client()
        cands = [
            _cand(path="/A/Settings", x=100, y=200),
            _cand(path="/B/Settings", x=300, y=400),
        ]
        mock = _install_fake_batch(
            client, candidates=cands,
            verify_returns=[
                {"ok": False, "error": "no_node_at_point",
                 "x": 120, "y": 210},
                # Second verify intentionally NOT consumed.
            ],
        )
        result = client.click("Button", name="Settings")
        assert result["ok"] is True
        assert result["verified"] is False
        assert result["verify_skipped"] is True
        assert result["candidate_idx"] == 0
        # find_all + 1 verify batch + click = 3 calls (no advance).
        assert mock.call_count == 3
        assert result["candidate_trace"][0]["decision"] == "skip_verify"

    def test_empty_path_and_role_skips_verify(self):
        """ok=true but empty path + empty role → skip verify, click first
        candidate."""
        client = _make_client()
        cands = [_cand(path="/A/Settings", x=100, y=200)]
        mock = _install_fake_batch(
            client, candidates=cands,
            verify_returns=[{
                "ok": True, "role": "", "name": "anything",
                "path": "", "under_app": True,
            }],
        )
        result = client.click("Button", name="Settings")
        assert result["ok"] is True
        assert result["verify_skipped"] is True
        assert result["candidate_trace"][0]["decision"] == "skip_verify"
        assert mock.call_count == 3


class TestVisibilityOrdering:

    def test_visibility_filter_orders_candidates(self):
        """find_all returns visible candidates first per the visibility
        filter. Pointer-mode iterates in that order: the visible
        candidate is checked BEFORE the offscreen fallback."""
        client = _make_client()
        cands = [
            _cand(path="/visible/Settings", x=300, y=400, visible=True),
            _cand(path="/hidden/Settings", x=999, y=999, visible=False),
        ]
        mock = _install_fake_batch(
            client, candidates=cands,
            verify_returns=[
                # First (visible) candidate gets exact match.
                {"ok": True, "role": "Button", "name": "Settings",
                 "path": "/visible/Settings", "under_app": True},
            ],
        )
        result = client.click("Button", name="Settings")
        assert result["ok"] is True
        assert result["verified"] is True
        # Clicked visible candidate at (320, 410), not offscreen one.
        assert result["x"] == 320
        assert result["y"] == 410
        assert result["candidate_idx"] == 0
        assert result["find"]["visible"] is True
        assert mock.call_count == 3
