"""Screenshot façade that delegates to a shared VncClient.

The VncClient is expected to be opened (entered as a context manager) by
the caller; Screenshotter just forwards `capture()` to it.

When a LatestFrameBus is attached (via attach_bus), capture() returns the
latest frame from the bus instead of issuing a direct VNC pull.  This
eliminates contention between the SessionRecorder's capture loop and the
scenario runner's ad-hoc captures.
"""

import logging
import time
import threading
from typing import Optional, TYPE_CHECKING

from PIL import Image

from ..transport.vnc import VncClient, VncClientError

if TYPE_CHECKING:
    from ..recording.frame_bus import LatestFrameBus

_log = logging.getLogger(__name__)


class ScreenshotError(Exception):
    pass


def _image_pixels(image: Image.Image) -> list[int]:
    get_flattened_data = getattr(image, "get_flattened_data", None)
    if get_flattened_data is not None:
        return list(get_flattened_data())
    return list(image.getdata())


class Screenshotter:
    def __init__(self, vnc: VncClient):
        self._vnc = vnc
        # VNC recv path is not re-entrant; serialize captures across threads.
        self._capture_lock = threading.Lock()
        self._bus: Optional["LatestFrameBus"] = None

    # ------------------------------------------------------------------
    # Bus wiring (called by SessionRecorder.start / _stop_capture)
    # ------------------------------------------------------------------

    def attach_bus(self, bus: "LatestFrameBus") -> None:
        """Bind a LatestFrameBus so capture() reads from it instead of VNC."""
        self._bus = bus

    def detach_bus(self) -> None:
        """Unbind the bus; subsequent capture() calls revert to direct VNC."""
        self._bus = None

    # ------------------------------------------------------------------
    # Core capture
    # ------------------------------------------------------------------

    def _capture_vnc_direct(self) -> tuple[Image.Image, tuple[int, int]]:
        """Issue a direct VNC capture, bypassing the frame bus.

        Used internally by both the bus-consumer fallback path and by
        SessionRecorder._capture_loop (which is the bus producer and must
        never read from the bus it writes to).
        """
        try:
            with self._capture_lock:
                return self._vnc.capture()
        except VncClientError as e:
            raise ScreenshotError(str(e)) from e

    def capture(self) -> tuple[Image.Image, tuple[int, int]]:
        bus = self._bus
        if bus is not None:
            t0 = time.monotonic()
            result = bus.get_after(t0, timeout_s=3.0)
            if result is not None:
                img, _capture_time = result
                return img, img.size
            # Bus timed out — fall through to direct VNC as defensive fallback.
            _log.debug(
                "LatestFrameBus.get_after timed out after 3 s; "
                "falling back to direct VNC capture"
            )
        return self._capture_vnc_direct()

    def wait_for_stable(
        self,
        max_seconds: float = 5.0,
        frames: int = 3,
        interval: float = 0.25,
        hash_tolerance: int = 2,
    ) -> bool:
        """Wait until the screen stops changing (perceptual stability gate).

        Captures frames at `interval` seconds apart, comparing consecutive
        pairs.  Returns True when the last two frames are perceptually
        identical (mean absolute pixel difference < `hash_tolerance` on a
        64×64 grayscale downscale), or False if `max_seconds` elapses first.

        Uses simple downscale+grayscale diff — no imagehash dependency.

        Args:
            max_seconds: Wall-time cap. Never blocks longer than this.
            frames: Minimum consecutive stable frames required (unused here;
                    kept for API compatibility — we check last-2 pairs).
            interval: Seconds between captures.
            hash_tolerance: Mean abs diff threshold (0-255 scale) below which
                two frames are considered identical.

        Returns:
            True if stable before timeout, False on timeout.
        """
        deadline = time.monotonic() + max_seconds
        prev_thumb: Image.Image | None = None

        while time.monotonic() < deadline:
            try:
                img, _ = self.capture()
            except ScreenshotError:
                time.sleep(interval)
                continue

            # Downscale to 64×64 grayscale for fast comparison.
            thumb = img.convert("L").resize((64, 64), Image.Resampling.BILINEAR)

            if prev_thumb is not None:
                # Compute mean absolute difference per pixel.
                prev_pixels = _image_pixels(prev_thumb)
                curr_pixels = _image_pixels(thumb)
                total_diff = sum(abs(a - b) for a, b in zip(prev_pixels, curr_pixels))
                mean_diff = total_diff / len(curr_pixels)
                if mean_diff < hash_tolerance:
                    return True

            prev_thumb = thumb
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))

        return False
