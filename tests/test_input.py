"""Unit tests for VNC mouse/keyboard encoding and process_running probe.

Tests the RFB-level keysym mapping and character encoding in VncClient,
and how FunctionalRunner uses process_running to gate launch steps.
No network — VncClient._ws and SSH calls are mocked.
Loads vnc.py directly by file path to avoid the broken install chain.
"""

import sys
import struct
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import importlib as _importlib
import PIL.Image as _pil_image_mod
if getattr(_pil_image_mod, "Image", None) is object:
    _importlib.reload(_pil_image_mod)

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Stub heavy deps that vnc.py needs at import time
# ---------------------------------------------------------------------------

def _stub(name, **attrs):
    if name not in sys.modules:
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
    return sys.modules[name]


_stub("Crypto", Cipher=types.ModuleType("Crypto.Cipher"))
_stub("Crypto.Cipher")
# DES stub
_des_mod = types.ModuleType("Crypto.Cipher.DES")
_des_mod.new = MagicMock(return_value=MagicMock(encrypt=lambda x: b"\x00" * 8))
_des_mod.MODE_ECB = 1
sys.modules["Crypto.Cipher.DES"] = _des_mod
sys.modules["Crypto.Cipher"].DES = _des_mod

_stub("websockets")
_stub("websockets.sync")
_stub("websockets.sync.client", connect=MagicMock())

# Ensure automation.transport is a real package and load cursor_motion (vnc.py needs it).
import types as _types_mod
if "automation.transport" not in sys.modules or not hasattr(sys.modules["automation.transport"], "__path__"):
    _t_pkg = _types_mod.ModuleType("automation.transport")
    _t_pkg.__path__ = [str(_PROJ / "automation" / "transport")]
    sys.modules["automation.transport"] = _t_pkg
_cm_spec = importlib.util.spec_from_file_location(
    "automation.transport.cursor_motion",
    _PROJ / "automation" / "transport" / "cursor_motion.py",
)
_cm_mod = importlib.util.module_from_spec(_cm_spec)
_cm_mod.__package__ = "automation.transport"
sys.modules["automation.transport.cursor_motion"] = _cm_mod
_cm_spec.loader.exec_module(_cm_mod)
sys.modules["automation.transport"].cursor_motion = _cm_mod

# Load vnc.py directly
_vnc_spec = importlib.util.spec_from_file_location(
    "automation.transport.vnc",
    _PROJ / "automation" / "transport" / "vnc.py",
)
_vnc_mod = importlib.util.module_from_spec(_vnc_spec)
_vnc_mod.__package__ = "automation.transport"
sys.modules["automation.transport.vnc"] = _vnc_mod
_vnc_spec.loader.exec_module(_vnc_mod)
sys.modules["automation.transport"].vnc = _vnc_mod

VncClient = _vnc_mod.VncClient
VncClientError = _vnc_mod.VncClientError
_char_keysym = _vnc_mod._char_keysym
_KEYSYMS = _vnc_mod._KEYSYMS
_MODIFIERS = _vnc_mod._MODIFIERS


# ---------------------------------------------------------------------------
# _char_keysym
# ---------------------------------------------------------------------------

class TestCharKeysym:
    def test_ascii_letter(self):
        assert _char_keysym("a") == ord("a")
        assert _char_keysym("Z") == ord("Z")

    def test_digit(self):
        assert _char_keysym("5") == ord("5")

    def test_latin1(self):
        assert _char_keysym("\xe9") == 0xe9

    def test_non_latin1_high_plane(self):
        assert _char_keysym("中") == (0x01000000 | ord("中"))


# ---------------------------------------------------------------------------
# Key/modifier lookup tables
# ---------------------------------------------------------------------------

class TestKeyTable:
    def test_enter_keysym(self):
        assert _KEYSYMS["enter"] == 0xff0d

    def test_escape_alias(self):
        assert _KEYSYMS["escape"] == _KEYSYMS["esc"]

    def test_ctrl_alias(self):
        assert _MODIFIERS["ctrl"] == _MODIFIERS["control"]

    def test_super_present(self):
        assert "super" in _MODIFIERS


# ---------------------------------------------------------------------------
# VncClient.key encoding
# ---------------------------------------------------------------------------

def _open_client() -> VncClient:
    proxmox = MagicMock()
    c = VncClient.__new__(VncClient)
    c._proxmox = proxmox
    c._vmid = 105
    c._timeout = 15.0
    c._width = 1920
    c._height = 1080
    c._button_mask = 0
    c._settle_ms = 1000  # Initialize settle delay (required by new settle feature)
    ws = MagicMock()
    ws.send = MagicMock()
    c._ws = ws
    c._reader = MagicMock()
    return c


class TestVncClientKey:
    def test_unknown_key_raises(self):
        c = _open_client()
        with pytest.raises(VncClientError, match="Unknown key"):
            c.key("notakey")

    def test_unknown_modifier_raises(self):
        c = _open_client()
        with pytest.raises(VncClientError, match="Unknown modifier"):
            c.key("badmod+a")

    def test_enter_sends_two_key_events(self):
        c = _open_client()
        sent = []
        c._ws.send.side_effect = lambda d: sent.append(d)
        with patch("time.sleep"):
            c.key("enter")
        key_events = [d for d in sent if len(d) == 8]
        assert len(key_events) == 2
        _, down, _, keysym = struct.unpack(">BBHI", key_events[0])
        assert down == 1 and keysym == 0xff0d

    def test_ctrl_a_sends_four_key_events(self):
        c = _open_client()
        sent = []
        c._ws.send.side_effect = lambda d: sent.append(d)
        with patch("time.sleep"):
            c.key("ctrl+a")
        key_events = [d for d in sent if len(d) == 8]
        assert len(key_events) == 4
        _, _, _, sym0 = struct.unpack(">BBHI", key_events[0])
        assert sym0 == _MODIFIERS["ctrl"]

    def test_type_uppercase_sends_shift(self):
        c = _open_client()
        sent = []
        c._ws.send.side_effect = lambda d: sent.append(d)
        with patch("time.sleep"):
            c.type_text("A")
        key_events = [d for d in sent if len(d) == 8]
        assert len(key_events) == 4
        _, _, _, sym0 = struct.unpack(">BBHI", key_events[0])
        assert sym0 == _MODIFIERS["shift"]


# ---------------------------------------------------------------------------
# process_running probe via runner
# ---------------------------------------------------------------------------

# Always reload runner after PIL restoration so it gets real PIL, not the stub
for _sn in ("automation.vlm.client", "automation.vlm.input", "automation.vlm.screenshot"):
    _m = types.ModuleType(_sn)
    _m.VLMClient = object
    _m.InputInjector = object
    _m.Screenshotter = object
    _m.VLMError = Exception
    sys.modules[_sn] = _m

_fspec = importlib.util.spec_from_file_location(
    "automation.runners.functional",
    _PROJ / "automation" / "runners" / "functional.py",
)
_fmod = importlib.util.module_from_spec(_fspec)
_fmod.__package__ = "automation.runners"
sys.modules["automation.runners.functional"] = _fmod
_fspec.loader.exec_module(_fmod)

_fmod = sys.modules["automation.runners.functional"]
FunctionalRunner = _fmod.FunctionalRunner
FunctionalStep = _fmod.FunctionalStep
StepFailed = _fmod.StepFailed


class TestProcessRunning:
    def test_process_running_true_proceeds_to_vlm_verify(self):
        from PIL import Image
        vlm = MagicMock()
        vlm.verify.return_value = True
        ss = MagicMock()
        ss.capture.return_value = (Image.new("RGB", (1920, 1080)), (1920, 1080))
        inj = MagicMock()
        inj.process_running.return_value = True
        inj.is_windows = False

        runner = FunctionalRunner(vlm=vlm, screenshotter=ss, injector=inj,
                                  screenshot_dir=None, log_fn=lambda m: None)
        with patch("time.sleep"), patch("time.time", side_effect=[
            0, 0, 0, 0.5, 1.0, 1.5, 100
        ]):
            runner.run_step(FunctionalStep(launch="/usr/bin/app", wait=5, launch_timeout=5, retries=1), 1)

        inj.process_running.assert_called()
        vlm.verify.assert_called()

    def test_process_running_false_raises_step_failed(self):
        from PIL import Image
        vlm = MagicMock()
        vlm.verify.return_value = False
        ss = MagicMock()
        ss.capture.return_value = (Image.new("RGB", (1920, 1080)), (1920, 1080))
        inj = MagicMock()
        inj.process_running.return_value = False
        inj.is_windows = False

        runner = FunctionalRunner(vlm=vlm, screenshotter=ss, injector=inj,
                                  screenshot_dir=None, log_fn=lambda m: None)
        with patch("time.sleep"):
            with pytest.raises(StepFailed):
                runner.run_step(FunctionalStep(launch="/usr/bin/app", wait=1, launch_timeout=1, retries=1), 1)
