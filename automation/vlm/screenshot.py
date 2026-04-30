"""Screenshot façade that delegates to a shared VncClient.

The VncClient is expected to be opened (entered as a context manager) by
the caller; Screenshotter just forwards `capture()` to it.
"""

from PIL import Image

from ..transport.vnc import VncClient, VncClientError


class ScreenshotError(Exception):
    pass


class Screenshotter:
    def __init__(self, vnc: VncClient):
        self._vnc = vnc

    def capture(self) -> tuple[Image.Image, tuple[int, int]]:
        try:
            return self._vnc.capture()
        except VncClientError as e:
            raise ScreenshotError(str(e)) from e
