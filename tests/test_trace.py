"""Unit tests for automation.commands.trace (F1b).

6 tests with mock SSH + mock screenshot diff.
No live VM or VNC connection required.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy deps before importing trace
# ---------------------------------------------------------------------------
for _mod in list(sys.modules):
    if any(_mod.startswith(p) for p in [
        "automation.transport.vnc",
        "automation.proxmox",
        "automation.vlm",
        "automation.config",
        "automation.transport.ssh",
    ]):
        sys.modules.pop(_mod, None)

# Stub SSHResult / SSHClient
_ssh_mod = types.ModuleType("automation.transport.ssh")

class _FakeSSHResult:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.success = returncode == 0

class _FakeSSHClient:
    def __init__(self, host, user, connect_timeout=10):
        self.host = host
        self.user = user
        self.connect_timeout = connect_timeout
        self._responses: dict = {}
        self._default_result = _FakeSSHResult(stdout="", returncode=0)

    def set_response(self, pattern: str, stdout: str = "", returncode: int = 0):
        self._responses[pattern] = _FakeSSHResult(stdout=stdout, returncode=returncode)

    def run(self, cmd, timeout=None, **kwargs):
        for pat, res in self._responses.items():
            if pat in cmd:
                return res
        return self._default_result

_ssh_mod.SSHResult = _FakeSSHResult
_ssh_mod.SSHClient = _FakeSSHClient
sys.modules["automation.transport.ssh"] = _ssh_mod

# Stub config
_config_mod = types.ModuleType("automation.config")

class _FakeApp:
    binary = "/opt/Rocket.Chat/rocketchat-desktop"
    name = "rocketchat"

class _FakeVLM:
    base_url = "http://localhost:5001/v1"
    model = "holo2-4b"
    verify_model = None

class _FakeProxmox:
    host = "192.168.1.1"
    port = 8006
    user = "root@pam"
    password = "secret"

class _FakeVM:
    name = "ubuntu2204"
    ip = "192.168.1.100"
    user = "jean"
    vmid = 105
    packages = []
    is_windows = False
    x11 = "auto"

class _FakeConfig:
    app = _FakeApp()
    vlm = _FakeVLM()
    proxmox = _FakeProxmox()
    vms = [_FakeVM()]
    vm_by_name = {"ubuntu2204": _FakeVM()}

def _fake_load_config(path):
    return _FakeConfig()

_config_mod.load_config = _fake_load_config
sys.modules["automation.config"] = _config_mod

# Stub VNC
_vnc_stub = types.ModuleType("automation.transport.vnc")
_vnc_stub.VncClient = MagicMock()
_vnc_stub._KEYSYMS = {}
_vnc_stub._MODIFIERS = {}
sys.modules["automation.transport.vnc"] = _vnc_stub

# Stub proxmox
for _sub in ["automation.proxmox", "automation.proxmox.api", "automation.proxmox.vm"]:
    _m = types.ModuleType(_sub)
    _m.ProxmoxAPI = MagicMock()
    sys.modules[_sub] = _m

# Stub vlm
for _sub in ["automation.vlm", "automation.vlm.client", "automation.vlm.screenshot"]:
    _m = types.ModuleType(_sub)
    _m.VLMClient = MagicMock()
    _m.Screenshotter = MagicMock()
    sys.modules[_sub] = _m

# Stub capability
_cap_mod = types.ModuleType("automation.setup.capability")
_cap_mod.get_for_vm = MagicMock(return_value="abc123def456abcd")
_cap_mod.write_manifest = MagicMock(return_value="/tmp/abc.json")
_cap_mod.build_manifest = MagicMock(return_value={"asar_sha": "abc123def456abcd"})
sys.modules["automation.setup"] = types.ModuleType("automation.setup")
sys.modules["automation.setup.capability"] = _cap_mod

from automation.commands.trace import (  # noqa: E402
    ProbeResult,
    TraceReport,
    _screenshot_diff,
    _probe_key,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_args(vm="ubuntu2204", write_manifest=False):
    args = MagicMock()
    args.config = "/tmp/fake.toml"
    args.vms = vm
    args.write_manifest = write_manifest
    return args


def _white_bmp(size=(2, 2)) -> bytes:
    """Minimal 1-bit BMP bytes for diff tests."""
    from PIL import Image
    import io
    img = Image.new("L", size, color=255)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def _black_bmp(size=(2, 2)) -> bytes:
    from PIL import Image
    import io
    img = Image.new("L", size, color=0)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScreenshotDiff:
    def test_identical_images_return_zero(self):
        img = _white_bmp()
        assert _screenshot_diff(img, img) < 0.01

    def test_black_vs_white_returns_large_diff(self):
        diff = _screenshot_diff(_white_bmp(), _black_bmp())
        assert diff > 50

    def test_invalid_bytes_return_zero(self):
        assert _screenshot_diff(b"garbage", b"garbage") == 0.0


class TestProbeResult:
    def test_manifest_accelerator_open(self):
        report = TraceReport(binary_sha="abc", vm="ubuntu2204")
        report.results = [
            ProbeResult(key="alt+f", status="OPEN"),
            ProbeResult(key="ctrl+comma", status="NO-ACCEL"),
            ProbeResult(key="alt+w", status="SWALLOWED"),
        ]
        accels = report.to_manifest_accelerators()
        assert accels["alt+f"] == "open"
        assert accels["ctrl+comma"] == "no_accel"
        assert accels["alt+w"] == "swallowed_in_webview"

    def test_probe_result_defaults(self):
        r = ProbeResult(key="ctrl+f", status="OPEN")
        assert r.detail == ""


class TestProbeKeyWithMock:
    def test_probe_returns_error_when_vnc_unavailable(self):
        """When VNC capture fails, probe returns ERROR status."""
        config = _FakeConfig()
        vm = _FakeVM()
        ssh = _FakeSSHClient("192.168.1.100", "jean")

        with patch(
            "automation.commands.trace._capture_vnc",
            return_value=None,
        ):
            result = _probe_key(ssh, config, vm, "alt+f", use_vnc=True)
        assert result.status == "ERROR"

    def test_probe_open_when_large_diff(self):
        """Large screenshot diff → OPEN."""
        config = _FakeConfig()
        vm = _FakeVM()
        ssh = _FakeSSHClient("192.168.1.100", "jean")

        call_count = [0]
        def _mock_capture(s, c, v):
            call_count[0] += 1
            if call_count[0] == 1:
                return _white_bmp()
            return _black_bmp()

        with patch("automation.commands.trace._capture_vnc", side_effect=_mock_capture):
            with patch("automation.commands.trace._send_key_vnc", return_value=True):
                with patch("automation.commands.trace.time.sleep"):
                    result = _probe_key(ssh, config, vm, "alt+f", use_vnc=True)
        assert result.status == "OPEN"

    def test_probe_swallowed_when_no_diff(self):
        """Zero diff → SWALLOWED."""
        config = _FakeConfig()
        vm = _FakeVM()
        ssh = _FakeSSHClient("192.168.1.100", "jean")

        with patch("automation.commands.trace._capture_vnc", return_value=_white_bmp()):
            with patch("automation.commands.trace._send_key_vnc", return_value=True):
                with patch("automation.commands.trace.time.sleep"):
                    result = _probe_key(ssh, config, vm, "alt+w", use_vnc=True)
        assert result.status == "SWALLOWED"
