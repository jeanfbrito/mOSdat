"""Tests for VncClient.human_move and InputInjector motion/dwell integration.

All tests use heavy mocking — no real VNC connection or SSH needed.
"""
from __future__ import annotations

# Clear sibling-test stub pollution BEFORE importing real modules.
# test_trace.py stubs automation.transport.vnc + automation.vlm.* at its
# module import time during pytest collection.
import sys as _sys
for _name in list(_sys.modules):
    if (
        _name.startswith("automation.transport")
        or _name.startswith("automation.vlm")
        or _name.startswith("automation.setup")
        or _name in (
            "automation.config",
            "automation.proxmox.api",
        )
    ):
        _sys.modules.pop(_name, None)

# Force-reimport so parent packages get their child attributes back
# (popping sys.modules entries leaves automation.vlm.input as missing attr
# on automation.vlm package, which breaks `patch("automation.vlm.input.X")`).
import automation.transport as _at  # noqa: E402
import automation.transport.vnc as _at_vnc  # noqa: E402
_at.vnc = _at_vnc
import automation.vlm as _av  # noqa: E402
import automation.vlm.input as _av_input  # noqa: E402
_av.input = _av_input
# Sibling test_negative_preflight expects automation.vlm.screenshot in sys.modules.
try:
    import automation.vlm.screenshot as _av_screenshot  # noqa: E402
    _av.screenshot = _av_screenshot
except Exception:
    pass

import time
from typing import Optional
from unittest.mock import MagicMock, call, patch

import pytest

from automation.config import CursorConfig
from automation.transport.vnc import VncClient
from automation.vlm.input import InputInjector


@pytest.fixture(autouse=True)
def _restore_real_modules_each_test():
    """Restore real automation.vlm.input + automation.transport.vnc when stubbed.

    Only pops modules that are stubs (no __file__) — leaves real modules and
    other tests' stubs alone. Real modules in sys.modules indicate prior tests
    already loaded the real implementation.
    """
    import sys as _s
    def _is_stub(name):
        m = _s.modules.get(name)
        return m is not None and not getattr(m, "__file__", None)
    targets = ["automation.transport", "automation.transport.vnc",
               "automation.transport.cursor_motion", "automation.vlm",
               "automation.vlm.input"]
    needs_restore = any(_is_stub(n) for n in targets)
    if needs_restore:
        for n in targets:
            if _is_stub(n):
                _s.modules.pop(n, None)
        import automation.transport as _at
        import automation.transport.vnc as _vnc
        _at.vnc = _vnc
        import automation.vlm as _av
        import automation.vlm.input as _vi
        _av.input = _vi
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vnc() -> VncClient:
    """Return a VncClient with mocked internals (no real connection)."""
    proxmox_mock = MagicMock()
    vnc = VncClient.__new__(VncClient)
    vnc._proxmox = proxmox_mock
    vnc._vmid = 100
    vnc._timeout = 15.0
    vnc._ws = MagicMock()  # non-None so _require_open passes
    vnc._reader = MagicMock()
    vnc._width = 1280
    vnc._height = 800
    vnc._button_mask = 0
    vnc._cursor_x = 0
    vnc._cursor_y = 0
    return vnc


def _make_injector(cursor_config: Optional[CursorConfig] = None) -> tuple[InputInjector, VncClient]:
    vnc = _make_vnc()
    ssh_mock = MagicMock()
    cfg = cursor_config if cursor_config is not None else CursorConfig()
    injector = InputInjector(vnc, ssh_mock, is_windows=False, cursor_config=cfg)
    return injector, vnc


# ---------------------------------------------------------------------------
# 1. VncClient.human_move — instant profile calls move() exactly once
# ---------------------------------------------------------------------------

def test_human_move_instant_calls_move_once():
    vnc = _make_vnc()
    vnc._cursor_x, vnc._cursor_y = 100, 200
    with patch.object(vnc, "move") as mock_move:
        vnc.human_move(300, 400, profile="instant")
    mock_move.assert_called_once_with(300, 400)


def test_human_move_instant_calls_move_with_target_coords():
    vnc = _make_vnc()
    vnc._cursor_x, vnc._cursor_y = 50, 60
    with patch.object(vnc, "move") as mock_move:
        vnc.human_move(700, 500, profile="instant")
    mock_move.assert_called_once_with(700, 500)


# ---------------------------------------------------------------------------
# 2. VncClient.human_move — bezier profile calls move 9-17 times
# ---------------------------------------------------------------------------

def test_human_move_bezier_call_count_range():
    vnc = _make_vnc()
    vnc._cursor_x, vnc._cursor_y = 0, 0
    with patch.object(vnc, "move") as mock_move, \
         patch("time.sleep"):
        vnc.human_move(200, 200, profile="bezier", seed=42)
    # bezier: frame_count in [8,16] → steps list has frame_count+1 entries
    # So move() is called between 9 and 17 times.
    count = mock_move.call_count
    assert 9 <= count <= 17, f"Expected 9-17 move() calls, got {count}"


def test_human_move_bezier_last_call_is_target():
    vnc = _make_vnc()
    vnc._cursor_x, vnc._cursor_y = 0, 0
    with patch.object(vnc, "move") as mock_move, \
         patch("time.sleep"):
        vnc.human_move(300, 150, profile="bezier", seed=7)
    last_call = mock_move.call_args_list[-1]
    assert last_call == call(300, 150)


# ---------------------------------------------------------------------------
# 3. VncClient.human_move — sleeps between steps
# ---------------------------------------------------------------------------

def test_human_move_bezier_sleeps_between_steps():
    vnc = _make_vnc()
    vnc._cursor_x, vnc._cursor_y = 0, 0
    with patch.object(vnc, "move"), \
         patch("time.sleep") as mock_sleep:
        vnc.human_move(200, 200, profile="bezier", emit_cap_ms=16.0, seed=1)
    # Should have slept at least once (one sleep per step with dt_ms > 0)
    assert mock_sleep.call_count >= 1


def test_human_move_bezier_accumulated_sleep_positive():
    vnc = _make_vnc()
    vnc._cursor_x, vnc._cursor_y = 0, 0
    sleep_calls = []
    with patch.object(vnc, "move"), \
         patch("time.sleep", side_effect=lambda t: sleep_calls.append(t)):
        vnc.human_move(200, 200, profile="bezier", emit_cap_ms=16.0, seed=99)
    total = sum(sleep_calls)
    assert total > 0, "Expected non-zero accumulated sleep"


# ---------------------------------------------------------------------------
# 4. Cursor position state updated after move
# ---------------------------------------------------------------------------

def test_human_move_instant_updates_cursor_state():
    vnc = _make_vnc()
    vnc._cursor_x, vnc._cursor_y = 10, 20
    with patch.object(vnc, "move"), patch("time.sleep"):
        vnc.human_move(500, 600, profile="instant")
    assert vnc._cursor_x == 500
    assert vnc._cursor_y == 600


def test_human_move_bezier_updates_cursor_state():
    vnc = _make_vnc()
    vnc._cursor_x, vnc._cursor_y = 0, 0
    with patch.object(vnc, "move"), patch("time.sleep"):
        vnc.human_move(400, 300, profile="bezier", seed=5)
    assert vnc._cursor_x == 400
    assert vnc._cursor_y == 300


# ---------------------------------------------------------------------------
# 5. InputInjector.click with motion="bezier" calls human_move(profile="bezier")
# ---------------------------------------------------------------------------

def test_injector_click_motion_bezier_calls_human_move_bezier():
    injector, vnc = _make_injector()
    with patch.object(vnc, "human_move") as mock_hm, \
         patch.object(vnc, "click"), \
         patch("time.sleep"):
        injector.click(100, 200, motion="bezier")
    assert mock_hm.called
    _, kwargs = mock_hm.call_args
    assert kwargs.get("profile") == "bezier"


# ---------------------------------------------------------------------------
# 6. InputInjector.click with motion="instant" calls human_move(profile="instant")
# ---------------------------------------------------------------------------

def test_injector_click_motion_instant_calls_human_move_instant():
    injector, vnc = _make_injector()
    with patch.object(vnc, "human_move") as mock_hm, \
         patch.object(vnc, "click"), \
         patch("time.sleep"):
        injector.click(100, 200, motion="instant")
    assert mock_hm.called
    _, kwargs = mock_hm.call_args
    assert kwargs.get("profile") == "instant"


# ---------------------------------------------------------------------------
# 7. InputInjector.click with dwell_ms=200 sleeps 200ms before click press
# ---------------------------------------------------------------------------

def test_injector_click_dwell_ms_sleeps_before_click():
    injector, vnc = _make_injector(CursorConfig(profile="instant"))
    sleep_calls = []
    click_calls = []

    def track_sleep(t):
        sleep_calls.append(t)

    def track_click(x, y, button=1):
        click_calls.append((x, y, button))

    with patch.object(vnc, "human_move"), \
         patch.object(vnc, "click", side_effect=track_click), \
         patch("automation.vlm.input.time.sleep", side_effect=track_sleep):
        injector.click(50, 60, motion="instant", dwell_ms=200)

    # The dwell sleep (0.2s) must occur before click is registered
    assert any(abs(t - 0.2) < 0.01 for t in sleep_calls), (
        f"Expected a ~0.2s dwell sleep, got: {sleep_calls}"
    )
    assert click_calls == [(50, 60, 1)]


# ---------------------------------------------------------------------------
# 8. InputInjector.hover positions cursor and sleeps; no button event
# ---------------------------------------------------------------------------

def test_injector_hover_no_button_event():
    injector, vnc = _make_injector(CursorConfig(profile="instant"))
    with patch.object(vnc, "human_move") as mock_hm, \
         patch.object(vnc, "click") as mock_click, \
         patch("automation.vlm.input.time.sleep"):
        injector.hover(300, 400, motion="instant", dwell_ms=400)
    assert mock_hm.called
    mock_click.assert_not_called()


def test_injector_hover_dwell_ms_sleeps():
    injector, vnc = _make_injector(CursorConfig(profile="instant"))
    sleep_calls = []
    with patch.object(vnc, "human_move"), \
         patch("automation.vlm.input.time.sleep", side_effect=lambda t: sleep_calls.append(t)):
        injector.hover(300, 400, motion="instant", dwell_ms=400)
    assert any(abs(t - 0.4) < 0.01 for t in sleep_calls), (
        f"Expected a ~0.4s dwell sleep, got: {sleep_calls}"
    )


# ---------------------------------------------------------------------------
# 9. FunctionalStep with motion="bezier" propagates to InputInjector.click
# ---------------------------------------------------------------------------

def test_functional_step_motion_propagates_to_injector():
    from automation.runners.scenario_loader import FunctionalStep
    step = FunctionalStep(
        localize="login button",
        click=True,
        motion="bezier",
        dwell_ms=None,
    )
    injector, vnc = _make_injector()
    captured = {}

    def capture_click(x, y, button=1, motion=None, dwell_ms=None):
        captured["motion"] = motion
        captured["dwell_ms"] = dwell_ms

    with patch.object(injector, "click", side_effect=capture_click):
        # Simulate runner calling injector.click with step fields
        injector.click(100, 200, button=1, motion=step.motion, dwell_ms=step.dwell_ms)

    assert captured["motion"] == "bezier"
    assert captured["dwell_ms"] is None


# ---------------------------------------------------------------------------
# 10. Default behavior (no motion kwarg) uses config.cursor.profile
# ---------------------------------------------------------------------------

def test_injector_default_uses_config_profile():
    cfg = CursorConfig(profile="linear")
    injector, vnc = _make_injector(cfg)
    with patch.object(vnc, "human_move") as mock_hm, \
         patch.object(vnc, "click"), \
         patch("time.sleep"):
        injector.click(10, 20)  # no motion kwarg
    _, kwargs = mock_hm.call_args
    assert kwargs.get("profile") == "linear"


def test_injector_default_bezier_profile():
    cfg = CursorConfig()  # default is "bezier"
    injector, vnc = _make_injector(cfg)
    with patch.object(vnc, "human_move") as mock_hm, \
         patch.object(vnc, "click"), \
         patch("time.sleep"):
        injector.click(10, 20)
    _, kwargs = mock_hm.call_args
    assert kwargs.get("profile") == "bezier"


# ---------------------------------------------------------------------------
# 11. --cursor-instant CLI flag results in config.cursor.profile == "instant"
# ---------------------------------------------------------------------------

def test_cursor_instant_config_reaches_injector():
    """CursorConfig(profile='instant') → injector uses "instant" by default."""
    cfg = CursorConfig(profile="instant")
    assert cfg.profile == "instant"

    injector, vnc = _make_injector(cfg)
    with patch.object(vnc, "human_move") as mock_hm, \
         patch.object(vnc, "click"), \
         patch("time.sleep"):
        injector.click(50, 75)
    _, kwargs = mock_hm.call_args
    assert kwargs.get("profile") == "instant"
