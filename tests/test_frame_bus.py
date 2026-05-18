"""Unit tests for LatestFrameBus.

All four required bus integration tests live here, plus
test_screenshotter_falls_back_when_bus_unbound which exercises Screenshotter.
Tests are fully in-process — no VNC, no network.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Load LatestFrameBus directly (avoids broken __init__ chain)
# ---------------------------------------------------------------------------

def _load_frame_bus():
    path = _PROJ / "automation" / "recording" / "frame_bus.py"
    spec = importlib.util.spec_from_file_location(
        "automation.recording.frame_bus", path
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "automation.recording"
    sys.modules["automation.recording.frame_bus"] = mod
    spec.loader.exec_module(mod)
    return mod


_bus_mod = _load_frame_bus()
LatestFrameBus = _bus_mod.LatestFrameBus


# ---------------------------------------------------------------------------
# Load Screenshotter (stubs vnc transport)
# ---------------------------------------------------------------------------

def _stub(name, **attrs):
    if name not in sys.modules:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
    return sys.modules[name]


VncClientError_stub = type("VncClientError", (Exception,), {})
_stub("automation.transport.vnc",
      VncClient=object,
      VncClientError=VncClientError_stub)

_ss_spec = importlib.util.spec_from_file_location(
    "automation.vlm.screenshot",
    _PROJ / "automation" / "vlm" / "screenshot.py",
)
_ss_mod = importlib.util.module_from_spec(_ss_spec)
_ss_mod.__package__ = "automation.vlm"
sys.modules["automation.vlm.screenshot"] = _ss_mod
_ss_spec.loader.exec_module(_ss_mod)

Screenshotter = _ss_mod.Screenshotter


def _solid(color=(100, 100, 100)) -> Image.Image:
    return Image.new("RGB", (8, 8), color)


# ---------------------------------------------------------------------------
# Test 1: bus blocks until a frame with capture_time >= deadline arrives
# ---------------------------------------------------------------------------

def test_bus_blocks_until_fresh_frame():
    """Push old frame (t=0); consumer calls get_after(t=1); push new frame (t=1.2);
    assert returned frame is the new one."""
    bus = LatestFrameBus()
    old_img = _solid((10, 10, 10))
    new_img = _solid((200, 200, 200))

    # Pre-push an old frame (capture_time = 0.0).
    bus.push(old_img, 0.0)

    result_holder: list = []

    def consumer():
        # Deadline is 1.0 — the old frame (t=0.0) should not satisfy this.
        result = bus.get_after(1.0, timeout_s=5.0)
        result_holder.append(result)

    t = threading.Thread(target=consumer)
    t.start()
    # Brief sleep so the consumer thread enters wait_for before we push.
    time.sleep(0.05)
    bus.push(new_img, 1.2)
    t.join(timeout=3.0)

    assert len(result_holder) == 1
    assert result_holder[0] is not None
    returned_img, returned_ts = result_holder[0]
    assert returned_img is new_img
    assert returned_ts == 1.2


# ---------------------------------------------------------------------------
# Test 2: bus times out when no fresh frame is pushed
# ---------------------------------------------------------------------------

def test_bus_times_out():
    """No producer pushes after the deadline; get_after returns None within timeout."""
    bus = LatestFrameBus()
    # Pre-load an old frame that won't satisfy a future deadline.
    bus.push(_solid(), 0.0)

    start = time.monotonic()
    result = bus.get_after(deadline_monotonic=9999.0, timeout_s=0.2)
    elapsed = time.monotonic() - start

    assert result is None
    # Timeout respected: should return in ~0.2 s (allow 0.5 s slack for CI).
    assert elapsed < 0.5


# ---------------------------------------------------------------------------
# Test 3: Screenshotter falls back to direct VNC when bus is unbound
# ---------------------------------------------------------------------------

def test_screenshotter_falls_back_when_bus_unbound():
    """attach_bus then detach_bus; capture() goes back to direct VncClient."""
    img = _solid((50, 50, 50))
    vnc = MagicMock()
    vnc.capture.return_value = (img, (8, 8))

    ss = Screenshotter(vnc)

    bus = LatestFrameBus()
    ss.attach_bus(bus)
    assert ss._bus is bus

    ss.detach_bus()
    assert ss._bus is None

    # After detach, capture() must hit vnc.capture directly.
    result_img, size = ss.capture()
    vnc.capture.assert_called_once()
    assert result_img is img


# ---------------------------------------------------------------------------
# Test 4: recorder publishes to bus; after stop, bus is unbound from screenshotter
# ---------------------------------------------------------------------------

def _load_session_recorder():
    path = _PROJ / "automation" / "recording" / "session_recorder.py"
    spec = importlib.util.spec_from_file_location(
        "automation.recording.session_recorder", path
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "automation.recording"
    sys.modules["automation.recording.session_recorder"] = mod
    spec.loader.exec_module(mod)
    return mod


_rec_mod = _load_session_recorder()
SessionRecorder = _rec_mod.SessionRecorder


def test_recorder_publishes_to_bus(tmp_path):
    """Start recorder with a fake screenshotter that returns predictable frames;
    assert bus content matches what the recorder captured; after stop, bus is
    unbound from Screenshotter."""
    # Use MagicMock images so that img.save() doesn't depend on PIL format
    # registry state (which may be polluted by other test modules in the
    # full suite).
    img_a = MagicMock(spec=Image.Image)
    img_a.size = (8, 8)
    img_b = MagicMock(spec=Image.Image)
    img_b.size = (8, 8)

    # Use a real Screenshotter so attach_bus/detach_bus round-trips properly.
    vnc = MagicMock()
    vnc.capture.side_effect = [
        (img_a, (8, 8)),
        (img_b, (8, 8)),
    ] * 10  # plenty of frames for the loop

    ss = Screenshotter(vnc)

    rec = SessionRecorder(
        screenshotter=ss,
        recording_dir=tmp_path / "rec",
        fps=50.0,  # fast so at least one frame fires quickly
    )

    rec.start()
    assert ss._bus is not None, "bus should be attached after start()"

    # Wait for at least one frame to be captured.
    # Use deadline=0 so we accept the very first frame pushed by the recorder,
    # regardless of when the thread schedules (avoids a startup-timing race).
    bus = ss._bus
    frame = bus.get_after(deadline_monotonic=0, timeout_s=5.0)
    assert frame is not None, "recorder should have pushed at least one frame"

    # Bus frame must be one of the predictable images.
    captured_img, _ts = frame
    assert captured_img is img_a or captured_img is img_b

    rec.stop_and_export()

    # After stop, bus must be unbound from screenshotter.
    assert ss._bus is None, "bus should be detached after stop_and_export()"
