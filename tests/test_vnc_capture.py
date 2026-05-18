"""Unit tests for VncClient capture() — stale-buffer drain and one-message read.

Loads vnc.py directly by file path to avoid the heavy __init__ chain.
All external I/O is mocked: no real WebSocket, no network.
"""

from __future__ import annotations

import importlib.util
import struct
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import importlib as _importlib
import PIL.Image as _pil_image_mod
if getattr(_pil_image_mod, "Image", None) is object:
    _importlib.reload(_pil_image_mod)
from PIL import Image

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Stub heavy deps before loading vnc.py
# ---------------------------------------------------------------------------

def _stub(name, **attrs):
    if name not in sys.modules:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
    return sys.modules[name]


# DES is used only in auth; stub it out so pycryptodome is not required here.
_Crypto_Cipher = _stub("Crypto", Cipher=types.SimpleNamespace())
_Cipher_DES = _stub("Crypto.Cipher", DES=MagicMock())

# cursor_motion import
_transport_pkg = _stub("automation.transport")
_cursor_motion_mod = _stub("automation.transport.cursor_motion",
                            generate_path=MagicMock(return_value=[]))

# Load vnc.py directly
_vnc_spec = importlib.util.spec_from_file_location(
    "automation.transport.vnc",
    _PROJ / "automation" / "transport" / "vnc.py",
)
_vnc_mod = importlib.util.module_from_spec(_vnc_spec)
_vnc_mod.__package__ = "automation.transport"
sys.modules["automation.transport.vnc"] = _vnc_mod
_vnc_spec.loader.exec_module(_vnc_mod)

VncClient = _vnc_mod.VncClient
VncClientError = _vnc_mod.VncClientError
_Reader = _vnc_mod._Reader
_MSG_FB_UPDATE = _vnc_mod._MSG_FB_UPDATE
_MSG_BELL = _vnc_mod._MSG_BELL
_MSG_SERVER_CUT = _vnc_mod._MSG_SERVER_CUT


# ---------------------------------------------------------------------------
# RFB encoding helpers
# ---------------------------------------------------------------------------

def _fb_update_msg(rects: list[tuple[int, int, tuple[int, int, int]]]) -> bytes:
    """Build a raw FramebufferUpdate server message.

    rects: list of (x, y, color) where color is (R, G, B).
    Each rect is 1x1 pixel, encoding=0 (Raw), BGRX byte order.
    """
    n = len(rects)
    # msg_type=0, padding=0, n_rects
    header = struct.pack(">BBH", _MSG_FB_UPDATE, 0, n)
    body = b""
    for x, y, (r, g, b) in rects:
        w, h = 1, 1
        enc = 0
        body += struct.pack(">HHHHi", x, y, w, h, enc)
        # BGRX: B, G, R, padding
        body += bytes([b, g, r, 0])
    return header + body


def _bell_msg() -> bytes:
    return struct.pack(">B", _MSG_BELL)


def _server_cut_msg(text: bytes = b"") -> bytes:
    return struct.pack(">BBBBI", _MSG_SERVER_CUT, 0, 0, 0, len(text)) + text


# ---------------------------------------------------------------------------
# Mock transport helpers
# ---------------------------------------------------------------------------

def _make_client(width: int = 4, height: int = 4) -> VncClient:
    """Return a VncClient with _ws and _reader pre-wired (no real socket)."""
    proxmox = MagicMock()
    client = VncClient.__new__(VncClient)
    client._proxmox = proxmox
    client._vmid = 100
    client._timeout = 15.0
    client._width = width
    client._height = height
    client._button_mask = 0
    client._cursor_x = 0
    client._cursor_y = 0
    # _ws will be set per-test
    client._ws = MagicMock()
    client._ws.send = MagicMock()
    client._reader = None  # will be set per-test
    return client


def _reader_from_bytes(ws_mock, data: bytes, timeout: float = 15.0) -> _Reader:
    """Create a _Reader whose internal buffer is pre-filled with data."""
    r = _Reader(ws_mock, timeout)
    r._buf.extend(data)
    return r


# ---------------------------------------------------------------------------
# Test 1: stale buffered data is discarded before FBUR
# ---------------------------------------------------------------------------

class TestCaptureDrainsStaleBuffer:
    def test_capture_discards_stale_buffered_data(self):
        """Pre-load _Reader._buf with a red FB update. Queue green on mock WS.

        capture() must return GREEN — proving it drained the stale red bytes
        and used the fresh response.
        """
        client = _make_client(width=1, height=1)

        # Stale data: red pixel (255, 0, 0) — sits in _Reader._buf
        stale_msg = _fb_update_msg([(0, 0, (255, 0, 0))])

        # Fresh data: green pixel (0, 255, 0) — returned by ws.recv
        fresh_msg = _fb_update_msg([(0, 0, (0, 255, 0))])

        # ws.recv behavior:
        #   - First call (timeout=0): TimeoutError — no extra WS frames pending.
        #   - Subsequent calls (during _grab_framebuffer via _Reader.read):
        #     return fresh_msg chunk.
        ws = MagicMock()
        recv_calls = [0]
        def _ws_recv(timeout=None):
            if timeout == 0:
                # drain check — nothing pending
                raise TimeoutError
            # normal read: hand out fresh_msg byte-by-byte chunks
            # (easier: return it all at once)
            if recv_calls[0] == 0:
                recv_calls[0] += 1
                return fresh_msg
            raise TimeoutError
        ws.recv.side_effect = _ws_recv
        ws.send = MagicMock()

        client._ws = ws
        # Pre-load _Reader._buf with stale red message
        client._reader = _Reader(ws, 15.0)
        client._reader._buf.extend(stale_msg)

        img, size = client.capture()

        assert size == (1, 1)
        pixel = img.getpixel((0, 0))
        # Should be green — the fresh response — not red (stale)
        assert pixel == (0, 255, 0), (
            f"Expected green (0,255,0) but got {pixel}. "
            "Stale buffer was NOT drained before FBUR."
        )


# ---------------------------------------------------------------------------
# Test 2: exactly one FB_UPDATE message consumed per capture()
# ---------------------------------------------------------------------------

class TestCaptureReadsExactlyOneFbUpdate:
    def test_capture_reads_exactly_one_fb_update_message(self):
        """Queue two FB_UPDATE messages back-to-back.

        capture() must return the FIRST message's pixels; the second must
        remain unconsumed (i.e. still readable on the next capture() call).
        """
        client = _make_client(width=1, height=1)

        # First: blue pixel (0, 0, 255)
        first_msg = _fb_update_msg([(0, 0, (0, 0, 255))])
        # Second: yellow pixel (255, 255, 0)
        second_msg = _fb_update_msg([(0, 0, (255, 255, 0))])

        both = first_msg + second_msg

        ws = MagicMock()
        ws.send = MagicMock()
        # drain check: no pending WS frames
        ws.recv.side_effect = [TimeoutError(), both]

        client._ws = ws
        client._reader = _Reader(ws, 15.0)

        img, size = client.capture()

        assert size == (1, 1)
        pixel = img.getpixel((0, 0))
        assert pixel == (0, 0, 255), (
            f"Expected blue (0,0,255) from first message but got {pixel}."
        )

        # The second message bytes must still be buffered in _reader._buf
        remaining = bytes(client._reader._buf)
        # At minimum the second message should be there
        assert len(remaining) >= len(second_msg), (
            "Second FB_UPDATE was over-consumed — reader should have it buffered."
        )
        # Verify it decodes as the second (yellow) message by parsing the pixel
        # Offset: 1 (msg_type) + 1 (padding) + 2 (n_rects) + 12 (rect header) = 16
        assert remaining[16:20] == bytes([0, 255, 255, 0]), (
            "Second message pixel bytes (BGRX for yellow) not found in buffer."
        )


# ---------------------------------------------------------------------------
# Test 3: Bell and ServerCut messages before FB_UPDATE are silently skipped
# ---------------------------------------------------------------------------

class TestCaptureSkipsBellAndServerCut:
    def test_capture_skips_bell_and_servercut_between_fb_updates(self):
        """Queue: Bell, ServerCut("hello"), then FB_UPDATE (cyan pixel).

        capture() must succeed and return the cyan pixel.
        """
        client = _make_client(width=1, height=1)

        bell = _bell_msg()
        cut = _server_cut_msg(b"hello")
        fb = _fb_update_msg([(0, 0, (0, 255, 255))])

        payload = bell + cut + fb

        ws = MagicMock()
        ws.send = MagicMock()
        # drain: nothing pending; then hand out payload
        ws.recv.side_effect = [TimeoutError(), payload]

        client._ws = ws
        client._reader = _Reader(ws, 15.0)

        img, size = client.capture()

        assert size == (1, 1)
        pixel = img.getpixel((0, 0))
        assert pixel == (0, 255, 255), (
            f"Expected cyan (0,255,255) but got {pixel}. "
            "Bell/ServerCut skipping may have broken frame read."
        )
