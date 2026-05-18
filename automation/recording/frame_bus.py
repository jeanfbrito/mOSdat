"""Thread-safe single-slot bus for the latest captured VNC frame.

Producer: SessionRecorder._capture_loop pushes after each VNC capture.
Consumer: Screenshotter.capture() reads the latest post-deadline frame
instead of issuing its own VNC pull when a recorder is running.
"""

from __future__ import annotations

import threading
from typing import Optional, Tuple

from PIL import Image


class LatestFrameBus:
    """Thread-safe single-slot frame bus.

    Holds the most recent ``(PIL.Image, capture_time_monotonic)`` tuple.
    A single producer pushes frames; multiple consumers may wait concurrently.
    """

    def __init__(self) -> None:
        self._lock = threading.Condition(threading.Lock())
        self._frame: Optional[Tuple[Image.Image, float]] = None

    def push(self, image: Image.Image, capture_time: float) -> None:
        """Store *image* captured at *capture_time* (``time.monotonic()``).

        Wakes all threads blocked in :meth:`get_after`.
        """
        with self._lock:
            self._frame = (image, capture_time)
            self._lock.notify_all()

    def get_after(
        self,
        deadline_monotonic: float,
        timeout_s: float = 5.0,
    ) -> Optional[Tuple[Image.Image, float]]:
        """Return the most recent frame whose capture_time >= *deadline_monotonic*.

        Blocks (via a :class:`threading.Condition`) until a qualifying frame
        arrives or *timeout_s* elapses.

        Returns:
            ``(image, capture_time)`` tuple when a fresh frame is available,
            or ``None`` on timeout.
        """
        def _fresh() -> bool:
            return self._frame is not None and self._frame[1] >= deadline_monotonic

        with self._lock:
            # Already have a qualifying frame (e.g. recorder is fast).
            if _fresh():
                return self._frame
            # Wait for the producer to push a fresh frame.
            self._lock.wait_for(_fresh, timeout=timeout_s)
            if _fresh():
                return self._frame
            return None
