"""Unit tests for AtspiClient.click via='hint' mode.

Hint mode = visible cursor motion + dwell BEFORE invoking do_action. For
zero-extent widgets (e.g. Fuselage ToggleSwitch) where pointer-mode misses
the 1×1 hidden input but action-mode leaves recordings devoid of visual
context. Worker exposes ``clickable_extents`` (an ancestor bbox) when
available; hint-mode prefers that and falls back to ``extents``.

All SSH calls are mocked; no network and no real AT-SPI bus required.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Force-reload real automation.atspi.client. test_atspi_dump_cli.py installs
# a sys.modules stub for "automation.atspi" (as a flat module, not a package)
# and never restores it; if pytest collects that test file first we'd hit
# "automation.atspi is not a package". Load by file path to bypass the stub.
_PROJ = Path(__file__).parent.parent
for _name in list(sys.modules):
    if _name == "automation.atspi" or _name.startswith("automation.atspi."):
        sys.modules.pop(_name, None)

from automation.atspi.client import AtspiClient, AtspiError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client():
    ssh = MagicMock()
    client = AtspiClient(ssh)
    client._worker_deployed = True
    return client


def _injector():
    inj = MagicMock()
    inj.vnc = MagicMock()
    return inj


def _make_fake_batch(find_payload, *, action_ok=True):
    """Return a fake run_batch matching the hint-mode call shapes.

    First find (called via self.find()): ops=[{op: "find"}] → returns
    {"results": {"1": find_payload}}.

    Then the find+do_action batch: ops=[{op:"find",id:"f"},{op:"do_action",id:"a"}]
    → returns find_payload at "f" and an action result at "a".
    """
    calls = {"first_find": False, "act_batch": False}

    def fake_run_batch(ops, **kwargs):
        if len(ops) == 1 and ops[0].get("op") == "find":
            calls["first_find"] = True
            return {"ok": True, "results": {"1": find_payload}}
        if (len(ops) == 2 and ops[0].get("op") == "find"
                and ops[1].get("op") == "do_action"):
            calls["act_batch"] = True
            act_res = (
                {"ok": True, "path": find_payload.get("path"),
                 "action_idx": ops[1].get("action_idx", 0),
                 "action_name": "check", "elapsed_ms": 12}
                if action_ok
                else {"ok": False, "error": "do_action_raised", "exc": "boom"}
            )
            return {
                "ok": action_ok,
                "results": {"f": find_payload, "a": act_res},
            }
        raise AssertionError(f"unexpected ops shape: {ops!r}")

    return fake_run_batch, calls


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------


class TestHintRouting:

    def test_via_hint_dispatches_to_hint(self):
        client = _make_client()
        inj = _injector()
        with patch.object(client, "_click_via_hint",
                          return_value={"ok": True}) as mock_h, \
             patch.object(client, "_click_via_pointer") as mock_p, \
             patch.object(client, "_click_via_action") as mock_a:
            client.click("check box", name="Telephony", via="hint",
                         input_injector=inj)
        mock_h.assert_called_once()
        mock_p.assert_not_called()
        mock_a.assert_not_called()

    def test_via_hint_requires_input_injector(self):
        client = _make_client()
        with pytest.raises(AtspiError, match="hint-mode click requires input_injector"):
            client.click("check box", name="Telephony", via="hint")

    def test_via_unknown_message_lists_hint(self):
        """The unknown-via error message must enumerate hint as a valid mode."""
        client = _make_client()
        with pytest.raises(AtspiError, match="'pointer', 'action', or 'hint'"):
            client.click("check box", name="Telephony", via="banana",
                         input_injector=_injector())


# ---------------------------------------------------------------------------
# hint-mode mechanics
# ---------------------------------------------------------------------------


class TestHintMode:

    def test_via_hint_uses_clickable_extents_when_available(self):
        """clickable_extents (ancestor bbox) wins over target extents."""
        client = _make_client()
        inj = _injector()
        find_payload = {
            "ok": True, "role": "check box", "name": "Telephony",
            "path": "/0/1/2", "depth": 3,
            "n_actions": 1, "action_names": ["check"], "child_count": 0,
            "extents": {"x": 120, "y": 209, "width": 1, "height": 1},
            "clickable_extents": {"x": 100, "y": 200, "width": 40, "height": 20},
        }
        fake, _ = _make_fake_batch(find_payload)
        client.run_batch = MagicMock(side_effect=fake)
        result = client.click("check box", name="Telephony", via="hint",
                              input_injector=inj)
        # Center of clickable_extents (100,200,40x20) = (120, 210)
        inj._position_cursor.assert_called_once_with(
            120, 210, motion=None, dwell_ms=300,
        )
        assert result["ok"] is True
        assert result["via"] == "hint"
        assert result["hint_x"] == 120
        assert result["hint_y"] == 210
        assert result["clickable_extents_used"] is True

    def test_via_hint_falls_back_to_extents_when_no_clickable(self):
        """When clickable_extents absent, hint uses target's own extents."""
        client = _make_client()
        inj = _injector()
        find_payload = {
            "ok": True, "role": "check box", "name": "Telephony",
            "path": "/0/1/2", "depth": 3,
            "n_actions": 1, "action_names": ["check"], "child_count": 0,
            "extents": {"x": 100, "y": 200, "width": 40, "height": 20},
        }
        fake, _ = _make_fake_batch(find_payload)
        client.run_batch = MagicMock(side_effect=fake)
        client.click("check box", name="Telephony", via="hint",
                     input_injector=inj)
        inj._position_cursor.assert_called_once_with(
            120, 210, motion=None, dwell_ms=300,
        )

    def test_via_hint_raises_when_no_usable_extents(self):
        """No extents and no clickable_extents → clear error pointing at
        via='action' as fallback."""
        client = _make_client()
        inj = _injector()
        find_payload = {
            "ok": True, "role": "check box", "name": "Telephony",
            "path": "/0/1/2", "depth": 3,
            "n_actions": 1, "action_names": ["check"], "child_count": 0,
        }
        fake, _ = _make_fake_batch(find_payload)
        client.run_batch = MagicMock(side_effect=fake)
        with pytest.raises(AtspiError, match="no usable extents"):
            client.click("check box", name="Telephony", via="hint",
                         input_injector=inj)
        # No cursor motion attempted.
        inj._position_cursor.assert_not_called()

    def test_via_hint_invokes_do_action_after_motion(self):
        """Cursor motion must happen BEFORE the do_action batch fires."""
        client = _make_client()
        inj = _injector()
        find_payload = {
            "ok": True, "role": "check box", "name": "Telephony",
            "path": "/0/1/2",
            "extents": {"x": 100, "y": 200, "width": 40, "height": 20},
        }

        call_order = []

        def fake_run_batch(ops, **kwargs):
            if len(ops) == 1 and ops[0].get("op") == "find":
                call_order.append("find_initial")
                return {"ok": True, "results": {"1": find_payload}}
            if (len(ops) == 2 and ops[1].get("op") == "do_action"):
                call_order.append("do_action_batch")
                assert ops[1].get("action_idx") == 0
                assert ops[0].get("role") == "check box"
                assert ops[0].get("name") == "Telephony"
                return {"ok": True, "results": {
                    "f": find_payload,
                    "a": {"ok": True, "path": "/0/1/2", "action_idx": 0},
                }}
            raise AssertionError(ops)

        def position_records(*a, **kw):
            call_order.append("position_cursor")

        inj._position_cursor.side_effect = position_records
        client.run_batch = MagicMock(side_effect=fake_run_batch)

        client.click("check box", name="Telephony", via="hint",
                     input_injector=inj)

        # find_initial → position_cursor → do_action_batch
        assert call_order == [
            "find_initial", "position_cursor", "do_action_batch",
        ], call_order

    def test_via_hint_default_dwell_300ms(self):
        client = _make_client()
        inj = _injector()
        find_payload = {
            "ok": True, "role": "check box", "name": "Telephony",
            "path": "/0/1/2",
            "extents": {"x": 100, "y": 200, "width": 40, "height": 20},
        }
        fake, _ = _make_fake_batch(find_payload)
        client.run_batch = MagicMock(side_effect=fake)
        client.click("check box", name="Telephony", via="hint",
                     input_injector=inj)
        # dwell_ms keyword should be 300, NOT None and NOT pointer-mode 0.
        kwargs = inj._position_cursor.call_args.kwargs
        assert kwargs["dwell_ms"] == 300

    def test_via_hint_passes_through_explicit_dwell(self):
        client = _make_client()
        inj = _injector()
        find_payload = {
            "ok": True, "role": "check box", "name": "Telephony",
            "path": "/0/1/2",
            "extents": {"x": 100, "y": 200, "width": 40, "height": 20},
        }
        fake, _ = _make_fake_batch(find_payload)
        client.run_batch = MagicMock(side_effect=fake)
        client.click("check box", name="Telephony", via="hint",
                     input_injector=inj, dwell_ms=750)
        kwargs = inj._position_cursor.call_args.kwargs
        assert kwargs["dwell_ms"] == 750

    def test_via_hint_do_action_failure_raises(self):
        """If do_action fails the call must raise AtspiError, not silently
        return ok."""
        client = _make_client()
        inj = _injector()
        find_payload = {
            "ok": True, "role": "check box", "name": "Telephony",
            "path": "/0/1/2",
            "extents": {"x": 100, "y": 200, "width": 40, "height": 20},
        }
        fake, _ = _make_fake_batch(find_payload, action_ok=False)
        client.run_batch = MagicMock(side_effect=fake)
        with pytest.raises(AtspiError, match="hint-mode action stage failed"):
            client.click("check box", name="Telephony", via="hint",
                         input_injector=inj)
        # Cursor motion still fired BEFORE the failed action — the user
        # actually saw where the click was attempted.
        inj._position_cursor.assert_called_once()
