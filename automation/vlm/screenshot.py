"""Screenshot façade that delegates to a shared VncClient.

The VncClient is expected to be opened (entered as a context manager) by
the caller; Screenshotter just forwards `capture()` to it.
"""

import time
import threading

from PIL import Image

from ..transport.vnc import VncClient, VncClientError


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

    def capture(self) -> tuple[Image.Image, tuple[int, int]]:
        try:
            with self._capture_lock:
                return self._vnc.capture()
        except VncClientError as e:
            raise ScreenshotError(str(e)) from e

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
