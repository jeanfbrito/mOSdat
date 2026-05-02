"""Unit tests for Screenshotter.wait_for_stable (A1 perceptual gate).

Loads screenshot.py directly by file path to avoid the vlm/__init__ chain.
Uses tiny PIL images generated inline — no file I/O, no network.
"""

import sys
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import importlib as _importlib
import PIL.Image as _pil_image_mod
if getattr(_pil_image_mod, "Image", None) is object:
    _importlib.reload(_pil_image_mod)
from PIL import Image

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Stub the vnc transport before loading screenshot.py
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

# Load screenshot.py directly
_ss_spec = importlib.util.spec_from_file_location(
    "automation.vlm.screenshot",
    _PROJ / "automation" / "vlm" / "screenshot.py",
)
_ss_mod = importlib.util.module_from_spec(_ss_spec)
_ss_mod.__package__ = "automation.vlm"
sys.modules["automation.vlm.screenshot"] = _ss_mod
_ss_spec.loader.exec_module(_ss_mod)

Screenshotter = _ss_mod.Screenshotter
ScreenshotError = _ss_mod.ScreenshotError
# Use the VncClientError from the loaded module (may differ from stub if vnc was already loaded)
VncClientError = _ss_mod.VncClientError


# ---------------------------------------------------------------------------
# Tiny image helpers
# ---------------------------------------------------------------------------

def _solid(color=(100, 100, 100), size=(64, 64)) -> Image.Image:
    return Image.new("RGB", size, color)


# ---------------------------------------------------------------------------
# wait_for_stable: identical frames → stable
# ---------------------------------------------------------------------------

class TestWaitForStableIdentical:
    def test_identical_frames_returns_true(self):
        img = _solid()
        vnc = MagicMock()
        vnc.capture.side_effect = [(img, (64, 64)), (img.copy(), (64, 64))]
        ss = Screenshotter(vnc)
        with patch("time.sleep"):
            result = ss.wait_for_stable(max_seconds=5.0, interval=0.01)
        assert result is True

    def test_stable_detected_on_second_capture(self):
        img = _solid((128, 128, 128))
        vnc = MagicMock()
        vnc.capture.side_effect = [(img, (64, 64)), (img.copy(), (64, 64))]
        ss = Screenshotter(vnc)
        with patch("time.sleep"):
            result = ss.wait_for_stable(max_seconds=1.0, interval=0.1)
        assert result is True


# ---------------------------------------------------------------------------
# wait_for_stable: different frames → timeout → False
# ---------------------------------------------------------------------------

class TestWaitForStableDifferent:
    def test_never_stable_returns_false(self):
        img1, img2 = _solid((0, 0, 0)), _solid((255, 255, 255))
        frames = [(img1 if i % 2 == 0 else img2, (64, 64)) for i in range(20)]
        vnc = MagicMock()
        vnc.capture.side_effect = frames
        ss = Screenshotter(vnc)
        with patch("time.sleep"), \
             patch("time.monotonic", side_effect=[
                 0.0, 0.05, 0.06, 0.10, 0.11, 0.15, 0.16, 5.1, 5.2
             ]):
            result = ss.wait_for_stable(max_seconds=5.0, interval=0.1)
        assert result is False


# ---------------------------------------------------------------------------
# wait_for_stable: VNC error is skipped
# ---------------------------------------------------------------------------

class TestWaitForStableError:
    def test_vnc_error_skipped_then_stable(self):
        img = _solid((100, 150, 200))
        vnc = MagicMock()
        vnc.capture.side_effect = [
            VncClientError("timeout"),
            (img, (64, 64)),
            (img.copy(), (64, 64)),
        ]
        ss = Screenshotter(vnc)
        with patch("time.sleep"):
            result = ss.wait_for_stable(max_seconds=3.0, interval=0.01)
        assert result is True


# ---------------------------------------------------------------------------
# Screenshotter.capture
# ---------------------------------------------------------------------------

class TestScreenshotterCapture:
    def test_vnc_error_wraps_to_screenshot_error(self):
        vnc = MagicMock()
        vnc.capture.side_effect = VncClientError("connection lost")
        ss = Screenshotter(vnc)
        with pytest.raises(ScreenshotError):
            ss.capture()

    def test_successful_capture_passes_through(self):
        img = _solid((50, 50, 50))
        vnc = MagicMock()
        vnc.capture.return_value = (img, (64, 64))
        ss = Screenshotter(vnc)
        result_img, size = ss.capture()
        assert size == (64, 64) and result_img is img
