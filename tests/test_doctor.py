"""Unit tests for automation/commands/doctor.py.

All SSH calls are mocked. Tests verify:
- Each check function returns the correct CheckResult status given SSH output.
- run_doctor() produces the correct summary counts and exit code.
"""

from __future__ import annotations


# Clear sibling-test stub pollution BEFORE importing automation.*.
# Sibling tests (test_negative, test_concurrent_safety, test_proxmox_vm) stub
# heavy modules in sys.modules at their module import time, which runs during
# pytest collection — before our imports. Pop those stubs so we get the real
# modules below.
import sys as _sys
for _name in list(_sys.modules):
    if (
        _name.startswith("automation.transport")
        or _name.startswith("automation.vlm")
        or _name.startswith("PIL")
        or _name in (
            "openai",
            "httpx",
            "automation.config",
            "automation.proxmox.api",
            "automation.proxmox.vm",
            "automation.reporting.report",
        )
    ):
        _sys.modules.pop(_name, None)

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stub setup so doctor.py can be imported without heavy deps
# ---------------------------------------------------------------------------

_PROJ = Path(__file__).parent.parent


def _stub(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules.setdefault(name, m)
    return m


# Stub automation.config so the module is importable
_stub("automation.config")
_stub("automation.transport.ssh")
_stub("automation.transport")

from automation.commands.doctor import (  # noqa: E402
    CheckResult,
    VMReport,
    check_asar,
    check_binary,
    check_deps,
    check_disk_home,
    check_disk_tmp,
    check_rc_processes,
    check_session,
    check_ssh,
    check_uptime,
    check_userdata_dirs,
    check_x11_cookie,
    host_check_gh,
    host_check_mosdat_version,
    host_check_proxmox,
    host_check_python,
    host_check_vlm,
    run_doctor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ssh(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    """Return a mock SSHClient whose .run() yields a single fixed SSHResult."""
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    result.stderr = stderr
    result.success = returncode == 0
    ssh = MagicMock()
    ssh.run.return_value = result
    return ssh


# ---------------------------------------------------------------------------
# check_ssh
# ---------------------------------------------------------------------------

class TestCheckSsh:
    def test_pass_when_echo_ok(self):
        r = check_ssh(_ssh("OK\n"))
        assert r.status == "PASS"

    def test_fail_when_connection_refused(self):
        r = check_ssh(_ssh("", returncode=255, stderr="Connection refused"))
        assert r.status == "FAIL"
        assert "Connection refused" in r.detail

    def test_fail_on_exception(self):
        ssh = MagicMock()
        ssh.run.side_effect = OSError("timeout")
        r = check_ssh(ssh)
        assert r.status == "FAIL"
        assert "timeout" in r.detail


# ---------------------------------------------------------------------------
# check_session
# ---------------------------------------------------------------------------

class TestCheckSession:
    def test_pass_gnome(self):
        r = check_session(_ssh("SESSION_TYPE=wayland DESKTOP=GNOME"))
        assert r.status == "PASS"
        assert "wayland" in r.detail

    def test_warn_empty_vars(self):
        r = check_session(_ssh("SESSION_TYPE= DESKTOP="))
        assert r.status == "WARN"

    def test_warn_on_ssh_failure(self):
        r = check_session(_ssh("", returncode=1))
        assert r.status == "WARN"


# ---------------------------------------------------------------------------
# check_x11_cookie
# ---------------------------------------------------------------------------

class TestCheckX11Cookie:
    def test_pass_when_mutter_file_found(self):
        r = check_x11_cookie(_ssh("/run/user/1000/.mutter-Xwaylandauth.ABCD\n"))
        assert r.status == "PASS"
        assert "mutter" in r.detail

    def test_warn_when_no_file(self):
        r = check_x11_cookie(_ssh(""))
        assert r.status == "WARN"


# ---------------------------------------------------------------------------
# check_deps
# ---------------------------------------------------------------------------

class TestCheckDeps:
    def _ssh_multi(self, results: list[tuple[str, int]]) -> MagicMock:
        """SSHClient that returns successive results for each .run() call."""
        mocks = []
        for stdout, rc in results:
            m = MagicMock()
            m.stdout = stdout
            m.returncode = rc
            m.success = rc == 0
            m.stderr = ""
            mocks.append(m)
        ssh = MagicMock()
        ssh.run.side_effect = mocks
        return ssh

    def test_all_present(self):
        deps = ["wmctrl", "xclip", "xdotool", "xdg-mime", "xdg-open", "ssh", "scp"]
        ssh = self._ssh_multi([("FOUND\n", 0)] * len(deps))
        results = check_deps(ssh)
        assert all(r.status == "PASS" for r in results)
        assert len(results) == len(deps)

    def test_one_missing(self):
        deps = ["wmctrl", "xclip", "xdotool", "xdg-mime", "xdg-open", "ssh", "scp"]
        outputs = [("FOUND\n", 0)] * (len(deps) - 1) + [("MISSING\n", 0)]
        ssh = self._ssh_multi(outputs)
        results = check_deps(ssh)
        assert results[-1].status == "FAIL"
        assert results[-1].label == "dep:scp"


# ---------------------------------------------------------------------------
# check_binary
# ---------------------------------------------------------------------------

class TestCheckBinary:
    def test_pass_when_found(self):
        r = check_binary(_ssh("FOUND\n"), "/opt/Rocket.Chat/rocketchat-desktop")
        assert r.status == "PASS"

    def test_fail_when_missing(self):
        r = check_binary(_ssh("MISSING\n"), "/opt/Rocket.Chat/rocketchat-desktop")
        assert r.status == "FAIL"

    def test_warn_when_file_glob_path(self):
        # Both install_path and default_path contain {file} → no candidates → WARN
        r = check_binary(_ssh(""), "/tmp/{file}", default_path="")
        assert r.status == "WARN"

    def test_uses_default_when_no_install_path(self):
        ssh = _ssh("FOUND\n")
        r = check_binary(ssh, "")
        assert r.status == "PASS"
        # Should have checked the default path
        call_args = ssh.run.call_args[0][0]
        assert "/opt/Rocket.Chat/rocketchat-desktop" in call_args


# ---------------------------------------------------------------------------
# check_asar
# ---------------------------------------------------------------------------

class TestCheckAsar:
    def test_pass_when_found(self):
        r = check_asar(_ssh("/opt/Rocket.Chat/resources/app.asar\n"))
        assert r.status == "PASS"

    def test_warn_when_not_found(self):
        r = check_asar(_ssh(""))
        assert r.status == "WARN"


# ---------------------------------------------------------------------------
# check_userdata_dirs
# ---------------------------------------------------------------------------

class TestCheckUserdataDirs:
    def test_pass_when_dirs_found(self):
        r = check_userdata_dirs(_ssh("/home/jean/.config/Rocket.Chat\n"), "Rocket.Chat")
        assert r.status == "PASS"
        assert "Rocket.Chat" in r.detail

    def test_warn_when_no_dirs(self):
        r = check_userdata_dirs(_ssh(""), "Rocket.Chat")
        assert r.status == "WARN"


# ---------------------------------------------------------------------------
# check_disk_tmp / check_disk_home
# ---------------------------------------------------------------------------

class TestCheckDisk:
    def test_tmp_pass(self):
        r = check_disk_tmp(_ssh("5G\n"))
        assert r.status == "PASS"
        assert "5" in r.detail

    def test_tmp_warn_low(self):
        r = check_disk_tmp(_ssh("0G\n"))
        assert r.status == "WARN"

    def test_home_pass(self):
        r = check_disk_home(_ssh("20G\n"))
        assert r.status == "PASS"

    def test_home_warn_low(self):
        r = check_disk_home(_ssh("0G\n"))
        assert r.status == "WARN"

    def test_warn_on_parse_error(self):
        r = check_disk_tmp(_ssh("N/A\n"))
        assert r.status == "WARN"


# ---------------------------------------------------------------------------
# check_rc_processes
# ---------------------------------------------------------------------------

class TestCheckRcProcesses:
    def test_pass_when_zero(self):
        r = check_rc_processes(_ssh("0\n"))
        assert r.status == "PASS"
        assert r.detail == "0"

    def test_warn_when_running(self):
        r = check_rc_processes(_ssh("3\n"))
        assert r.status == "WARN"
        assert "3" in r.detail

    def test_warn_on_parse_error(self):
        r = check_rc_processes(_ssh("error\n"))
        assert r.status == "WARN"


# ---------------------------------------------------------------------------
# check_uptime
# ---------------------------------------------------------------------------

class TestCheckUptime:
    def test_pass(self):
        r = check_uptime(_ssh(" 10:00:00 up 3 days,  1:23,  2 users,  load average: 0.5, 0.3, 0.2\n"))
        assert r.status == "PASS"

    def test_warn_on_failure(self):
        r = check_uptime(_ssh("", returncode=1))
        assert r.status == "WARN"


# ---------------------------------------------------------------------------
# Host checks
# ---------------------------------------------------------------------------

class TestHostChecks:
    def test_python_pass(self):
        r = host_check_python()
        # Current interpreter must be 3.10+
        assert r.status in ("PASS", "WARN")
        assert sys.version[:5] in r.detail

    def test_gh_missing(self):
        with patch("shutil.which", return_value=None):
            r = host_check_gh()
        assert r.status == "WARN"

    def test_gh_present(self):
        with patch("shutil.which", return_value="/usr/bin/gh"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="gh version 2.40.0\n", returncode=0)
            r = host_check_gh()
        assert r.status == "PASS"
        assert "gh version" in r.detail

    def test_proxmox_warn_no_host(self):
        r = host_check_proxmox("")
        assert r.status == "WARN"

    def test_proxmox_fail_on_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            r = host_check_proxmox("192.168.1.1")
        assert r.status == "FAIL"

    def test_vlm_warn_no_url(self):
        r = host_check_vlm("")
        assert r.status == "WARN"

    def test_vlm_fail_on_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            r = host_check_vlm("http://192.168.1.1:5001/v1")
        assert r.status == "FAIL"


# ---------------------------------------------------------------------------
# VMReport
# ---------------------------------------------------------------------------

class TestVMReport:
    def test_healthy_when_no_fails(self):
        report = VMReport("testvm")
        report.results = [CheckResult("x", "PASS"), CheckResult("y", "WARN")]
        assert report.healthy is True
        assert report.failed == 0
        assert report.warned == 1

    def test_not_healthy_when_fails(self):
        report = VMReport("testvm")
        report.results = [CheckResult("x", "FAIL")]
        assert report.healthy is False
        assert report.failed == 1


# ---------------------------------------------------------------------------
# run_doctor integration (mocked config + SSH)
# ---------------------------------------------------------------------------

class TestRunDoctor:
    """Integration tests for run_doctor() using a mock config and mock SSH."""

    def _make_vm(self, name: str, ip: str = "192.168.1.10") -> MagicMock:
        vm = MagicMock()
        vm.name = name
        vm.ip = ip
        vm.user = "jean"
        vm.is_windows = False
        pkg = MagicMock()
        pkg.app_path = "/opt/Rocket.Chat/rocketchat-desktop"
        vm.packages = [pkg]
        return vm

    def _make_config(self, vm_names: list[str]) -> MagicMock:
        vms = [self._make_vm(name) for name in vm_names]
        cfg = MagicMock()
        cfg.vms = vms
        cfg.vm_by_name = {vm.name: vm for vm in vms}
        cfg.app.name = "rocketchat"
        cfg.proxmox.host = "192.168.1.85"
        cfg.proxmox.port = 8006
        cfg.vlm.base_url = "http://192.168.1.62:5001/v1"
        return cfg

    def _make_args(self, vms: str = "ubuntu2204") -> MagicMock:
        args = MagicMock()
        args.config = Path(_PROJ / "examples/rocketchat.toml")
        args.vms = vms
        return args

    def _mock_ssh_all_pass(self) -> MagicMock:
        """SSH client that returns plausible passing output for all checks."""
        responses = {
            "echo OK": "OK\n",
            "echo SESSION_TYPE": "SESSION_TYPE=wayland DESKTOP=GNOME\n",
            "ls /run/user": "/run/user/1000/.mutter-Xwaylandauth.ABCD\n",
            'test -f': "FOUND\n",
            "find /opt": "/opt/Rocket.Chat/resources/app.asar\n",
            "ls -d ~/.config": "/home/jean/.config/Rocket.Chat\n",
            "df -BG /tmp": "5G\n",
            "df -BG ~": "20G\n",
            "pgrep": "0\n",
            "uptime": "10:00:00 up 3 days, load average: 0.1\n",
        }

        def _run(cmd: str, **kw):
            m = MagicMock()
            m.returncode = 0
            m.success = True
            m.stderr = ""
            for key, out in responses.items():
                if key in cmd:
                    m.stdout = out
                    return m
            m.stdout = "FOUND\n"
            return m

        ssh = MagicMock()
        ssh.run.side_effect = _run
        return ssh

    def test_run_doctor_healthy_vm(self):
        """run_doctor with all checks passing returns exit 0."""
        cfg = self._make_config(["ubuntu2204"])
        with patch("automation.commands.doctor.load_config", return_value=cfg), \
             patch("automation.commands.doctor.SSHClient", return_value=self._mock_ssh_all_pass()), \
             patch("automation.commands.doctor.host_check_proxmox",
                   return_value=CheckResult("Proxmox API", "PASS", "HTTP 200")), \
             patch("automation.commands.doctor.host_check_vlm",
                   return_value=CheckResult("VLM endpoint", "PASS", "HTTP 200")):
            args = self._make_args("ubuntu2204")
            rc = run_doctor(args)
        assert rc == 0

    def test_run_doctor_unknown_vm_returns_1(self):
        cfg = self._make_config(["ubuntu2204"])
        with patch("automation.commands.doctor.load_config", return_value=cfg):
            args = self._make_args("nonexistent_vm")
            rc = run_doctor(args)
        assert rc == 1

    def test_run_doctor_ssh_unreachable_marks_fail(self):
        """When SSH fails, VM is marked failed and exit code is 1."""
        bad_result = MagicMock()
        bad_result.stdout = ""
        bad_result.returncode = 255
        bad_result.stderr = "Connection refused"
        bad_result.success = False
        bad_ssh = MagicMock()
        bad_ssh.run.return_value = bad_result

        cfg = self._make_config(["ubuntu2204"])
        with patch("automation.commands.doctor.load_config", return_value=cfg), \
             patch("automation.commands.doctor.SSHClient", return_value=bad_ssh), \
             patch("automation.commands.doctor.host_check_proxmox",
                   return_value=CheckResult("Proxmox API", "PASS", "HTTP 200")), \
             patch("automation.commands.doctor.host_check_vlm",
                   return_value=CheckResult("VLM endpoint", "PASS", "HTTP 200")):
            args = self._make_args("ubuntu2204")
            rc = run_doctor(args)
        assert rc == 1
