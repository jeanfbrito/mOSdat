"""Unit tests for ``automation.commands.vm_lifecycle`` (plan 003, workstream E).

No live VM: SSH is a fake that answers by command text.
"""

from __future__ import annotations

import base64
import json
import struct
import subprocess
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from automation.commands import vm_lifecycle as mod
from automation.commands.vm_lifecycle import (
    installed_app_info,
    readiness_hints,
    wait_ready,
    windows_disk_free_gb,
)


@dataclass
class SSHResult:
    """Local copy of the SSHResult shape.

    Other test files stub ``automation.transport.ssh`` in ``sys.modules``;
    a local type keeps these tests independent of file order.
    """

    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0


def _vm(name="ubuntu2404", os_type="linux", ip="192.168.13.82"):
    return SimpleNamespace(name=name, vmid=102, ip=ip, user="jean", os_type=os_type)


def _cfg(*vms, host="192.168.13.85"):
    return SimpleNamespace(
        proxmox=SimpleNamespace(host=host),
        vms=list(vms),
        vm_by_name={v.name: v for v in vms},
    )


class _FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


class _ProbeSSH:
    """``check_ssh`` runs ``echo OK``; answer from a scripted list."""

    def __init__(self, answers, clock=None, cost=0.0):
        self.answers = answers
        self.clock = clock
        self.cost = cost

    def run(self, command, timeout=None):
        if self.clock is not None:
            self.clock.now += self.cost
        ok = self.answers.pop(0) if self.answers else False
        if ok:
            return SSHResult(0, "OK\n", "")
        return SSHResult(255, "", "Permission denied (publickey,password).")


# ---------------------------------------------------------------------------
# E1 wait_ready
# ---------------------------------------------------------------------------


def test_wait_ready_returns_immediately_when_ssh_answers():
    fc = _FakeClock()
    ssh = _ProbeSSH([True])
    res = wait_ready(_vm(), ssh_factory=lambda vm: ssh, sleep=fc.sleep, clock=fc.clock)
    assert res["ready"] is True
    assert res["waited_s"] == 0
    assert fc.sleeps == []


def test_wait_ready_polls_until_ssh_answers():
    fc = _FakeClock()
    ssh = _ProbeSSH([False, False, True])
    res = wait_ready(
        _vm(), interval=5, ssh_factory=lambda vm: ssh, sleep=fc.sleep, clock=fc.clock,
    )
    assert res["ready"] is True
    assert fc.sleeps == [5, 5]
    assert res["waited_s"] == 10
    assert "3 attempt" in res["detail"]


def test_wait_ready_times_out_with_last_error():
    fc = _FakeClock()
    ssh = _ProbeSSH([], clock=fc, cost=1.0)
    res = wait_ready(
        _vm(), timeout=20, interval=5,
        ssh_factory=lambda vm: ssh, sleep=fc.sleep, clock=fc.clock,
    )
    assert res["ready"] is False
    assert res["waited_s"] <= 20
    assert "Permission denied" in res["detail"]
    assert "within 20s" in res["detail"]


def test_wait_ready_survives_factory_exception():
    fc = _FakeClock()
    calls = {"n": 0}

    def factory(vm):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("boom")
        return _ProbeSSH([True])

    res = wait_ready(_vm(), ssh_factory=factory, sleep=fc.sleep, clock=fc.clock)
    assert res["ready"] is True
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# E2 installed_app_info / windows_disk_free_gb
# ---------------------------------------------------------------------------


def _write_asar(path, files: dict[str, bytes]) -> None:
    """Write a minimal real asar: Chromium pickle header + JSON table + data."""
    table, blob, offset = {}, b"", 0
    for name, data in files.items():
        table[name] = {"size": len(data), "offset": str(offset)}
        blob += data
        offset += len(data)
    header = json.dumps({"files": table}).encode()
    padded = header + b"\0" * (-len(header) % 4)
    header_size = 8 + len(padded)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        struct.pack("<4I", 4, header_size, header_size - 4, len(header))
        + padded
        + blob
    )


class _LinuxSSH:
    """Fake for path probes; runs the asar byte reads for real on a local file."""

    def __init__(self, asar=None, appimage=False):
        self.asar = asar
        self.appimage = appimage
        self.commands: list[str] = []

    def run(self, command, timeout=None):
        self.commands.append(command)
        if command.startswith("ls /opt/*/resources/app.asar"):
            out = "" if self.appimage or not self.asar else self.asar + "\n"
            return SSHResult(0, out, "")
        if "--appimage-extract" in command:
            if self.appimage:
                return SSHResult(
                    0, "/tmp/mosdat-appimage-asar/squashfs-root/resources/app.asar\n", "",
                )
            return SSHResult(1, "", "")
        if command.startswith(("set -- ", "tail -c +")):
            r = subprocess.run(["sh", "-c", command], capture_output=True)
            return SSHResult(
                r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode(),
            )
        if command.startswith("stat -c %Y"):
            return SSHResult(0, "1761220800\n", "")
        return SSHResult(1, "", "unexpected")


_DECOY = b'{"name":"semver","version":"23.7.20"}'
_APP_PKG = b'{"name":"rocketchat","productName":"Rocket.Chat","version":"4.17.0-alpha.1"}'


def _opt_asar(tmp_path):
    asar = tmp_path / "opt" / "Rocket.Chat" / "resources" / "app.asar"
    # A bundled file with its own "version" sits before the root package.json,
    # as in the real ubuntu2204 asar where a grep found 23.7.20.
    _write_asar(asar, {"node_modules/semver/package.json": _DECOY, "package.json": _APP_PKG})
    return asar


def test_installed_app_info_linux_deb(tmp_path):
    asar = _opt_asar(tmp_path)
    ssh = _LinuxSSH(asar=str(asar))
    info = installed_app_info(ssh, is_windows=False)
    assert info == {
        "found": True,
        "path": str(tmp_path / "opt" / "Rocket.Chat"),
        "asar_path": str(asar),
        "version": "4.17.0-alpha.1",
        "asar_mtime_utc": "2025-10-23T12:00:00Z",
    }
    assert any(c == f"stat -c %Y {asar}" for c in ssh.commands)


def test_asar_version_ignores_earlier_bundled_version(tmp_path):
    asar = _opt_asar(tmp_path)
    assert mod._asar_package_version(_LinuxSSH(), str(asar)) == "4.17.0-alpha.1"


def test_asar_version_none_when_not_an_asar(tmp_path):
    bogus = tmp_path / "app.asar"
    bogus.write_bytes(b"not an asar at all, just text")
    assert mod._asar_package_version(_LinuxSSH(), str(bogus)) is None


def test_installed_app_info_linux_appimage_stats_the_appimage(monkeypatch):
    monkeypatch.setattr(mod, "_asar_package_version", lambda ssh, asar: "4.17.0")
    ssh = _LinuxSSH(appimage=True)
    info = installed_app_info(ssh, is_windows=False)
    assert info["found"] is True
    assert info["path"] == "/opt/Rocket.Chat/rocketchat-desktop"
    assert info["asar_path"].startswith("/tmp/mosdat-appimage-asar/")
    assert any(
        c == "stat -c %Y /opt/Rocket.Chat/rocketchat-desktop" for c in ssh.commands
    )


def test_installed_app_info_linux_not_installed():
    ssh = _LinuxSSH(asar=None)
    info = installed_app_info(ssh, is_windows=False)
    assert info["found"] is False
    assert info["version"] is None


def _decode_ps(command: str) -> str:
    b64 = command.rsplit(" ", 1)[-1]
    return base64.b64decode(b64).decode("utf-16-le")


class _WinSSH:
    EXE = r"C:\Users\jean\AppData\Local\Programs\Rocket.Chat\Rocket.Chat.exe"

    def __init__(self, installed=True, free="53687091200"):
        self.installed = installed
        self.free = free
        self.scripts: list[str] = []

    def run(self, command, timeout=None):
        assert command.startswith("powershell -NoProfile -EncodedCommand ")
        script = _decode_ps(command)
        self.scripts.append(script)
        if "INSTALLED_AT=" in script:
            if not self.installed:
                return SSHResult(1, "", "NOT_FOUND")
            return SSHResult(0, f"INSTALLED_AT={self.EXE}\nVERSION=4.12.0.0\n", "")
        if "app.asar" in script:
            asar = self.EXE.rsplit("\\", 1)[0] + r"\resources\app.asar"
            return SSHResult(0, f"ASAR={asar}\nMTIME=2026-10-22T18:04:11Z\n", "")
        if "Get-PSDrive C" in script:
            if self.free is None:
                return SSHResult(1, "", "Get-PSDrive: drive not found")
            return SSHResult(0, f"FREE={self.free}\n", "")
        return SSHResult(1, "", "unexpected")


def test_installed_app_info_windows():
    ssh = _WinSSH()
    info = installed_app_info(ssh, is_windows=True)
    assert info == {
        "found": True,
        "path": _WinSSH.EXE,
        "asar_path": r"C:\Users\jean\AppData\Local\Programs\Rocket.Chat\resources\app.asar",
        "version": "4.12.0.0",
        "asar_mtime_utc": "2026-10-22T18:04:11Z",
    }
    # The asar probe is anchored on the exe that the install probe found.
    assert _WinSSH.EXE in ssh.scripts[1]


def test_installed_app_info_windows_probes_all_candidates():
    from automation.commands.build import _WINDOWS_RC_CANDIDATES

    ssh = _WinSSH()
    installed_app_info(ssh, is_windows=True)
    for cand in _WINDOWS_RC_CANDIDATES:
        assert cand in ssh.scripts[0]


def test_installed_app_info_windows_not_installed():
    ssh = _WinSSH(installed=False)
    info = installed_app_info(ssh, is_windows=True)
    assert info["found"] is False
    assert len(ssh.scripts) == 1


def test_windows_disk_free_gb():
    assert windows_disk_free_gb(_WinSSH(free="53687091200")) == 50.0


def test_windows_disk_free_gb_raises_on_failure():
    with pytest.raises(RuntimeError, match="drive not found"):
        windows_disk_free_gb(_WinSSH(free=None))


# ---------------------------------------------------------------------------
# E3 readiness_hints
# ---------------------------------------------------------------------------


_U2204 = _vm("ubuntu2204", ip="192.168.13.81")
_U2404 = _vm("ubuntu2404")
_F42 = _vm("fedora42", ip="192.168.13.80")
_W10 = _vm("windows10", os_type="windows", ip="192.168.13.87")
_W11 = _vm("windows11", os_type="windows", ip="192.168.13.86")
_CFG = _cfg(_F42, _U2204, _U2404, _W10, _W11)


def test_hints_empty_when_all_ok():
    assert readiness_hints(_U2404, _CFG, ssh_ok=True, proxmox_ok=True) == []


def test_hints_proxmox_down_is_the_only_hint():
    hints = readiness_hints(_U2404, _CFG, ssh_ok=False, proxmox_ok=False)
    assert hints == [
        "Proxmox host 192.168.13.85 is not reachable from this machine: the lab "
        "is off or off-network; no VM can run until it is back"
    ]


def test_hints_ssh_down_linux_names_same_family_first():
    hints = readiness_hints(_U2404, _CFG, ssh_ok=False, proxmox_ok=True)
    first = hints[0]
    assert "ubuntu2204 (same family: ubuntu)" in first
    assert "fedora42 (same OS type: linux)" in first
    assert first.index("ubuntu2204") < first.index("fedora42")
    assert "windows" not in first
    assert "ubuntu2404 (" not in first
    assert any("QEMU guest agent" in h for h in hints)
    assert not any("console" in h for h in hints)


def test_hints_ssh_down_windows_names_other_windows_and_console():
    hints = readiness_hints(_W10, _CFG, ssh_ok=False, proxmox_ok=True)
    assert "windows11 (same family: windows)" in hints[0]
    assert "ubuntu" not in hints[0]
    assert any("mosdat_ssh_bootstrap" in h and "console" in h for h in hints)


def test_hints_ssh_down_no_alternative_vm():
    hints = readiness_hints(_U2404, _cfg(_U2404, _W10), ssh_ok=False, proxmox_ok=True)
    assert not any(h.startswith("SSH to") for h in hints)
    assert any("mosdat_ssh_bootstrap" in h for h in hints)


def test_family_strips_version_suffix():
    assert mod._family("ubuntu2404") == "ubuntu"
    assert mod._family("windows10") == "windows"
    assert mod._family("opensuse") == "opensuse"
