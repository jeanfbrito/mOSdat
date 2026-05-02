"""RFB-over-WebSocket client for Proxmox QEMU VMs.

Full control (screenshot + mouse + keyboard) via the Proxmox VNC WebSocket
proxy — no guest-side tools required, works on any guest OS, even during
boot or at the login screen.

Wire protocol:
  POST /nodes/{node}/qemu/{vmid}/vncproxy?websocket=1
    → { ticket, port, user, upid }
  WSS  /nodes/{node}/qemu/{vmid}/vncwebsocket?port={port}&vncticket={ticket}
    with Cookie: PVEAuthCookie=<user ticket>
    → binary WebSocket tunnelling raw RFB 3.8 bytes.

Use as a context manager:
    with VncClient(proxmox, vmid=105) as vnc:
        img, size = vnc.capture()
        vnc.click(640, 400)
        vnc.type_text("hello")
        vnc.key("enter")
"""

from __future__ import annotations

import ssl
import struct
import time
import urllib.parse
from typing import TYPE_CHECKING

from Crypto.Cipher import DES
from PIL import Image
from websockets.sync.client import connect

if TYPE_CHECKING:
    from ..proxmox.api import ProxmoxAPI


class VncClientError(Exception):
    pass


# Server → client message types
_MSG_FB_UPDATE = 0
_MSG_BELL = 2
_MSG_SERVER_CUT = 3

# Client → server message types
_CMSG_SET_PIXEL_FORMAT = 0
_CMSG_SET_ENCODINGS = 2
_CMSG_FB_UPDATE_REQUEST = 3
_CMSG_KEY_EVENT = 4
_CMSG_POINTER_EVENT = 5

# Security types
_SEC_NONE = 1
_SEC_VNC_AUTH = 2


# X11 keysyms for named keys (subset that mosdat tests actually use).
# Full list: /usr/include/X11/keysymdef.h
_KEYSYMS = {
    "enter": 0xff0d, "return": 0xff0d,
    "tab": 0xff09,
    "escape": 0xff1b, "esc": 0xff1b,
    "backspace": 0xff08,
    "delete": 0xffff,
    "home": 0xff50, "end": 0xff57,
    "pgup": 0xff55, "pgdn": 0xff56,
    "up": 0xff52, "down": 0xff54, "left": 0xff51, "right": 0xff53,
    "insert": 0xff63,
    "space": 0x0020,
    "f1":  0xffbe, "f2":  0xffbf, "f3":  0xffc0, "f4":  0xffc1,
    "f5":  0xffc2, "f6":  0xffc3, "f7":  0xffc4, "f8":  0xffc5,
    "f9":  0xffc6, "f10": 0xffc7, "f11": 0xffc8, "f12": 0xffc9,
}

_MODIFIERS = {
    "shift":   0xffe1,
    "ctrl":    0xffe3, "control": 0xffe3,
    "alt":     0xffe9,
    "meta":    0xffe7,
    "super":   0xffeb, "win": 0xffeb,
}

_SHIFTED = set('~!@#$%^&*()_+{}|:"<>?')


def _reverse_bits(b: int) -> int:
    """Reverse the 8 bits of a byte — RFB VNC-auth quirk."""
    r = 0
    for i in range(8):
        if b & (1 << i):
            r |= 1 << (7 - i)
    return r


def _vnc_auth_response(ticket: str, challenge: bytes) -> bytes:
    key = ticket.encode("ascii")[:8].ljust(8, b"\x00")
    key = bytes(_reverse_bits(b) for b in key)
    return DES.new(key, DES.MODE_ECB).encrypt(challenge)


def _char_keysym(ch: str) -> int:
    """Map a single character to an X11 keysym.

    For printable ASCII/Latin-1, keysym == code point.
    For other Unicode, use the 0x01000000 | codepoint convention.
    """
    cp = ord(ch)
    if cp <= 0xff:
        return cp
    return 0x01000000 | cp


class _Reader:
    """Buffered reader over a synchronous WebSocket — serves exact-length reads."""

    def __init__(self, ws, timeout: float):
        self._ws = ws
        self._buf = bytearray()
        self._timeout = timeout

    def read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._ws.recv(timeout=self._timeout)
            if isinstance(chunk, str):
                chunk = chunk.encode("latin-1")
            if not chunk:
                raise VncClientError("WebSocket closed mid-RFB message")
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


class VncClient:
    def __init__(self, proxmox: "ProxmoxAPI", vmid: int, timeout: float = 15.0):
        self._proxmox = proxmox
        self._vmid = vmid
        self._timeout = timeout
        self._ws = None
        self._reader: _Reader | None = None
        self._width = 0
        self._height = 0
        self._button_mask = 0  # current mouse button state

    # ---- context-manager lifecycle ----

    def __enter__(self) -> "VncClient":
        self._open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    @property
    def size(self) -> tuple[int, int]:
        return self._width, self._height

    # ---- public actions ----

    def capture(self) -> tuple[Image.Image, tuple[int, int]]:
        self._require_open()
        self._send(struct.pack(">BBHHHH", _CMSG_FB_UPDATE_REQUEST, 0, 0, 0, self._width, self._height))
        return self._grab_framebuffer(), (self._width, self._height)

    def move(self, x: int, y: int) -> None:
        """Move mouse to absolute pixel coordinates (no button change)."""
        self._require_open()
        self._pointer_event(x, y, self._button_mask)

    def click(self, x: int, y: int, button: int = 1) -> None:
        """Press+release a mouse button at (x, y). button: 1=left, 2=middle, 3=right."""
        self._require_open()
        bit = 1 << (button - 1)
        self._pointer_event(x, y, self._button_mask)               # move
        self._pointer_event(x, y, self._button_mask | bit)         # press
        time.sleep(0.03)
        self._pointer_event(x, y, self._button_mask & ~bit)        # release

    def type_text(self, text: str, delay: float = 0.01) -> None:
        """Type a string by emitting keydown+keyup per character."""
        self._require_open()
        for ch in text:
            keysym = _char_keysym(ch)
            shift = ch.isupper() or ch in _SHIFTED
            if shift:
                self._key_event(_MODIFIERS["shift"], True)
            self._key_event(keysym, True)
            self._key_event(keysym, False)
            if shift:
                self._key_event(_MODIFIERS["shift"], False)
            if delay:
                time.sleep(delay)

    def key(self, name: str) -> None:
        """Press a named key or a combo like 'ctrl+a', 'alt+f4'."""
        self._require_open()
        parts = [p.strip().lower() for p in name.split("+")]
        mods, main = parts[:-1], parts[-1]
        mod_syms = []
        for m in mods:
            if m not in _MODIFIERS:
                raise VncClientError(f"Unknown modifier: {m!r}")
            mod_syms.append(_MODIFIERS[m])
        if main in _KEYSYMS:
            main_sym = _KEYSYMS[main]
        elif main in _MODIFIERS:
            main_sym = _MODIFIERS[main]
        elif len(main) == 1:
            main_sym = _char_keysym(main)
        else:
            raise VncClientError(f"Unknown key: {name!r}")
        for m in mod_syms:
            self._key_event(m, True)
        self._key_event(main_sym, True)
        time.sleep(0.02)
        self._key_event(main_sym, False)
        for m in reversed(mod_syms):
            self._key_event(m, False)

    # ---- low-level RFB messages ----

    def _pointer_event(self, x: int, y: int, button_mask: int) -> None:
        x = max(0, min(self._width - 1, int(x)))
        y = max(0, min(self._height - 1, int(y)))
        self._button_mask = button_mask & 0xff
        self._send(struct.pack(">BBHH", _CMSG_POINTER_EVENT, self._button_mask, x, y))

    def _key_event(self, keysym: int, down: bool) -> None:
        self._send(struct.pack(">BBHI", _CMSG_KEY_EVENT, 1 if down else 0, 0, keysym))

    def _send(self, data: bytes) -> None:
        assert self._ws is not None
        self._ws.send(data)

    def _require_open(self) -> None:
        if self._ws is None:
            raise VncClientError("VncClient not open — use as context manager")

    # ---- connection + handshake ----

    def _open(self) -> None:
        cfg = self._proxmox.config
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        # vncproxy tickets are single-use; a stale session on the VM can make
        # the first attempt fail at the WebSocket handshake ("did not receive
        # a valid HTTP response"). Retry a few times with a fresh ticket.
        last_err: Exception | None = None
        for attempt in range(4):
            if attempt:
                time.sleep(1.0 + attempt)

            vnc = self._proxmox.vncproxy(self._vmid)
            ticket = vnc.get("ticket")
            port = vnc.get("port")
            if not ticket or not port:
                raise VncClientError(f"vncproxy response missing ticket/port: {vnc!r}")
            pveticket = self._proxmox.auth_ticket

            uri = (
                f"wss://{cfg.host}:{cfg.port}"
                f"/api2/json/nodes/{cfg.node}/qemu/{self._vmid}/vncwebsocket"
                f"?port={port}&vncticket={urllib.parse.quote(ticket, safe='')}"
            )

            try:
                self._ws = connect(
                    uri,
                    ssl=ssl_ctx,
                    additional_headers={"Cookie": f"PVEAuthCookie={pveticket}"},
                    subprotocols=["binary"],
                    open_timeout=self._timeout,
                    max_size=None,
                    compression=None,
                    ping_interval=20,
                    ping_timeout=20,
                )
                self._reader = _Reader(self._ws, timeout=self._timeout)
                self._width, self._height = self._rfb_handshake(ticket)
                self._wake_display()
                return
            except Exception as e:
                last_err = e
                self.close()

        raise VncClientError(f"Failed to open VNC session after retries: {last_err}")

    def _wake_display(self) -> None:
        """Nudge the mouse and tap Shift to wake a sleeping guest display.

        QEMU VNC happily captures a framebuffer that the guest has stopped
        updating (monitor off → all-black). A no-op input event wakes the
        guest's display subsystem. Shift is used because it produces no
        character even if a text field currently has focus.
        """
        cx, cy = self._width // 2, self._height // 2
        try:
            self._pointer_event(cx, cy, 0)
            self._pointer_event(cx + 1, cy + 1, 0)
            self._pointer_event(cx, cy, 0)
            self._key_event(_MODIFIERS["shift"], True)
            self._key_event(_MODIFIERS["shift"], False)
        except Exception:
            pass
        time.sleep(0.4)

    # ---- RFB 3.8 handshake ----

    def _rfb_handshake(self, ticket: str) -> tuple[int, int]:
        r = self._reader
        assert r is not None

        greeting = r.read(12)
        if not greeting.startswith(b"RFB "):
            raise VncClientError(f"Unexpected RFB greeting: {greeting!r}")
        self._send(b"RFB 003.008\n")

        (n_types,) = struct.unpack(">B", r.read(1))
        if n_types == 0:
            (reason_len,) = struct.unpack(">I", r.read(4))
            reason = r.read(reason_len).decode("utf-8", "replace")
            raise VncClientError(f"Server refused connection: {reason}")
        types = set(r.read(n_types))

        if _SEC_VNC_AUTH in types:
            chosen = _SEC_VNC_AUTH
        elif _SEC_NONE in types:
            chosen = _SEC_NONE
        else:
            raise VncClientError(f"No supported security type offered: {sorted(types)}")
        self._send(bytes([chosen]))

        if chosen == _SEC_VNC_AUTH:
            self._send(_vnc_auth_response(ticket, r.read(16)))

        (result,) = struct.unpack(">I", r.read(4))
        if result != 0:
            reason = b""
            try:
                (reason_len,) = struct.unpack(">I", r.read(4))
                reason = r.read(reason_len)
            except Exception:
                pass
            raise VncClientError(
                f"VNC authentication failed (code {result}): {reason.decode('utf-8', 'replace')}"  # type: ignore[attr-defined]  # reason is bytes (b"" or r.read()); mypy loses track after try/except reassignment
            )

        self._send(b"\x01")  # ClientInit shared=1

        init_head = r.read(24)
        width, height = struct.unpack(">HH", init_head[:4])
        (name_len,) = struct.unpack(">I", init_head[20:24])
        if name_len:
            r.read(name_len)
        if width == 0 or height == 0:
            raise VncClientError(f"Server reported bogus framebuffer size: {width}x{height}")

        pixel_format = struct.pack(
            ">BBBBHHHBBB3x",
            32, 24, 0, 1,
            255, 255, 255,
            16, 8, 0,
        )
        self._send(struct.pack(">B3x", _CMSG_SET_PIXEL_FORMAT) + pixel_format)
        self._send(struct.pack(">BBHi", _CMSG_SET_ENCODINGS, 0, 1, 0))

        return width, height

    # ---- Framebuffer grab ----

    def _grab_framebuffer(self) -> Image.Image:
        r = self._reader
        assert r is not None
        W, H = self._width, self._height
        canvas = Image.new("RGB", (W, H))
        painted = 0
        target = W * H
        max_messages = 32

        while painted < target and max_messages > 0:
            max_messages -= 1
            (msg_type,) = struct.unpack(">B", r.read(1))

            if msg_type == _MSG_FB_UPDATE:
                r.read(1)  # padding
                (n_rects,) = struct.unpack(">H", r.read(2))
                for _ in range(n_rects):
                    x, y, w, h, enc = struct.unpack(">HHHHi", r.read(12))
                    if enc != 0:
                        raise VncClientError(
                            f"Server used encoding {enc} despite SetEncodings([Raw])"
                        )
                    if w == 0 or h == 0:
                        continue
                    raw = r.read(w * h * 4)
                    tile = Image.frombytes("RGB", (w, h), raw, "raw", "BGRX")
                    canvas.paste(tile, (x, y))
                    painted += w * h

            elif msg_type == _MSG_BELL:
                continue

            elif msg_type == _MSG_SERVER_CUT:
                r.read(3)
                (length,) = struct.unpack(">I", r.read(4))
                if length:
                    r.read(length)

            else:
                raise VncClientError(f"Unknown server message type: {msg_type}")

        if painted < target:
            raise VncClientError(f"Framebuffer incomplete: painted {painted}/{target} pixels")
        return canvas


# Back-compat aliases for the old module name
VncScreenshotError = VncClientError
VncScreenshotClient = VncClient
