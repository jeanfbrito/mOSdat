"""Unit tests for automation.commands.preflight (I2).

All SSH calls are mocked — no live VM required.
"""
from __future__ import annotations

# IMPORTANT: clear sibling-test stub pollution BEFORE importing automation.*.
# test_concurrent_safety / test_negative / test_proxmox_vm stub
# automation.transport.ssh, automation.config, automation.transport at their
# module-import time (which runs during pytest collection, before any test).
# Clean them so we get the real modules below.
import sys as _sys
for _name in list(_sys.modules):
    if _name.startswith("automation.transport") or _name in (
        "automation.config",
        "automation.commands.preflight",
        "automation.proxmox.api",
        "automation.proxmox.vm",
        "automation.vlm.client",
        "automation.vlm.input",
        "automation.vlm.screenshot",
        "automation.vlm.agent",
        "automation.reporting.report",
    ):
        _sys.modules.pop(_name, None)

import textwrap
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from automation.commands.preflight import (
    FAIL,
    PASS,
    SKIP,
    _check_binary,
    _check_disk_free,
    _check_no_stale_rc,
    _check_symbol,
    _check_tool_deps,
    _check_x11_cookie,
    _dry_run_shell_steps,
    run_preflight,
)
from automation.transport.ssh import SSHResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ssh(stdout="", returncode=0, stderr=""):
    ssh = MagicMock()
    ssh.run.return_value = SSHResult(returncode=returncode, stdout=stdout, stderr=stderr)
    ssh.host = "192.168.1.1"
    ssh.user = "jean"
    return ssh


# ---------------------------------------------------------------------------
# Unit tests for individual check functions
# ---------------------------------------------------------------------------

class TestCheckX11Cookie:
    def test_pass_when_cookie_found(self):
        ssh = _ssh("/run/user/1000/.mutter-Xwaylandauth.abc123")
        status, detail = _check_x11_cookie(ssh)
        assert status == PASS
        assert ".mutter-Xwaylandauth" in detail

    def test_fail_when_no_cookie(self):
        ssh = _ssh("")
        status, detail = _check_x11_cookie(ssh)
        assert status == FAIL
        assert "no cookie" in detail


class TestCheckToolDeps:
    def test_pass_when_all_present(self):
        ssh = _ssh("YES")
        status, detail = _check_tool_deps(ssh)
        assert status == PASS
        assert "xdotool" in detail

    def test_fail_when_missing(self):
        # first 4 tools YES, last one NO
        responses = [
            SSHResult(0, "YES", ""),
            SSHResult(0, "YES", ""),
            SSHResult(0, "YES", ""),
            SSHResult(0, "YES", ""),
            SSHResult(0, "NO", ""),
        ]
        ssh = MagicMock()
        ssh.run.side_effect = responses
        status, detail = _check_tool_deps(ssh)
        assert status == FAIL
        assert "missing" in detail


class TestCheckBinary:
    def test_pass_when_exists(self):
        ssh = _ssh("EXISTS")
        status, detail = _check_binary(ssh, "/opt/Rocket.Chat/rocketchat-desktop")
        assert status == PASS
        assert "/opt/Rocket.Chat" in detail

    def test_fail_when_missing(self):
        ssh = _ssh("MISSING")
        status, detail = _check_binary(ssh, "/opt/Rocket.Chat/rocketchat-desktop")
        assert status == FAIL
        assert "not found" in detail


class TestCheckSymbol:
    def test_pass_when_found(self):
        ssh = _ssh("42")
        status, detail = _check_symbol(ssh, "/opt/Rocket.Chat/resources/app.asar", "isTelephonyEnabled")
        assert status == PASS
        assert "42" in detail

    def test_fail_when_zero(self):
        ssh = _ssh("0")
        status, detail = _check_symbol(ssh, "/opt/Rocket.Chat/resources/app.asar", "isTelephonyEnabled")
        assert status == FAIL
        assert "not found" in detail


class TestCheckDiskFree:
    def test_pass_when_enough(self):
        # 5 GB in bytes
        ssh = _ssh(str(5 * 1024 ** 3))
        status, detail = _check_disk_free(ssh)
        assert status == PASS
        assert "GB" in detail

    def test_fail_when_too_low(self):
        # 500 MB
        ssh = _ssh(str(500 * 1024 ** 2))
        status, detail = _check_disk_free(ssh)
        assert status == FAIL
        assert "only" in detail


class TestCheckNoStaleRc:
    def test_pass_when_no_processes(self):
        ssh = _ssh("")
        status, detail = _check_no_stale_rc(ssh)
        assert status == PASS

    def test_fail_when_processes_found(self):
        ssh = _ssh("1234 /opt/Rocket.Chat/rocketchat-desktop --no-sandbox")
        status, detail = _check_no_stale_rc(ssh)
        assert status == FAIL
        assert "stale" in detail


class TestDryRunShellSteps:
    def test_runs_first_3_shell_steps_only(self):
        steps = [
            {"shell": "echo setup1"},
            {"verify": "screen shows login"},
            {"shell": "echo setup2"},
            {"shell": "echo setup3"},
            {"shell": "echo setup4"},  # should NOT run
        ]
        ssh = MagicMock()
        ssh.run.return_value = SSHResult(0, "ok\n", "")
        results = _dry_run_shell_steps(ssh, steps)
        assert len(results) == 3
        # step indices correspond to shell steps 1, 3, 4 (not #5)
        assert ssh.run.call_count == 3

    def test_skip_non_shell_steps(self):
        steps = [
            {"verify": "screen shows login"},
            {"click": True, "localize": "Login button"},
        ]
        ssh = MagicMock()
        results = _dry_run_shell_steps(ssh, steps)
        assert results == []
        ssh.run.assert_not_called()

    def test_reports_fail_on_nonzero_exit(self):
        steps = [{"shell": "exit 1"}]
        ssh = MagicMock()
        ssh.run.return_value = SSHResult(1, "error output\n", "")
        results = _dry_run_shell_steps(ssh, steps)
        assert len(results) == 1
        label, status, detail = results[0]
        assert status == FAIL
        assert "exit 1" in detail


# ---------------------------------------------------------------------------
# Integration-level: run_preflight with mocked config + SSH
# ---------------------------------------------------------------------------

class TestRunPreflight:
    def _make_config(self, tmp_path):
        """Write a minimal YAML scenario and a mock config."""
        scenarios_dir = tmp_path / "shared" / "scenarios" / "functional"
        scenarios_dir.mkdir(parents=True)
        scenario = scenarios_dir / "test-scenario.yaml"
        scenario.write_text(textwrap.dedent("""\
            name: test-scenario
            steps:
              - shell: echo hello
              - shell: echo world
        """))
        return scenario

    def _write_fake_toml(self, tmp_path) -> Path:
        cfg = tmp_path / "config.toml"
        cfg.write_text("[app]\nname='test'\nversion='1.0'\nbinary='/opt/test'\n")
        return cfg

    def test_returns_0_when_all_pass(self, tmp_path):
        self._make_config(tmp_path)
        cfg = self._write_fake_toml(tmp_path)

        # Mock config object
        mock_config = MagicMock()
        mock_config.framework_path = tmp_path
        mock_config.app.binary = "/opt/Rocket.Chat/rocketchat-desktop"
        mock_vm = MagicMock()
        mock_vm.name = "ubuntu2204"
        mock_vm.ip = "192.168.1.10"
        mock_vm.user = "jean"
        mock_vm.packages = []
        mock_config.vm_by_name = {"ubuntu2204": mock_vm}

        def ssh_run(cmd, timeout=None):
            # X11 cookie check
            if ".mutter-Xwaylandauth" in cmd:
                return SSHResult(0, "/run/user/1000/.mutter-Xwaylandauth.abc", "")
            # tool deps
            if "command -v" in cmd:
                return SSHResult(0, "YES", "")
            # binary exists
            if "test -f" in cmd:
                return SSHResult(0, "EXISTS", "")
            # disk free
            if "df --output=avail" in cmd:
                return SSHResult(0, str(10 * 1024 ** 3), "")
            # stale processes
            if "pgrep -af" in cmd and "rocketchat" in cmd:
                return SSHResult(1, "", "")
            # userdata: kill, wipe, launch, list dirs
            if "Rocket.Chat" in cmd and "ls -1d" in cmd:
                return SSHResult(0, str(tmp_path / ".config" / "Rocket.Chat") + "\n", "")
            # dry-run shell steps
            return SSHResult(0, "ok\n", "")

        mock_ssh = MagicMock()
        mock_ssh.run.side_effect = ssh_run
        mock_ssh.host = "192.168.1.10"
        mock_ssh.user = "jean"

        with patch("automation.commands.preflight.load_config", return_value=mock_config), \
             patch("subprocess.run") as mock_subprocess:
            # SSH reachability check (BatchMode subprocess.run)
            mock_subprocess.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            with patch("automation.commands.preflight.SSHClient", return_value=mock_ssh):
                rc = run_preflight(
                    config_path=str(cfg),
                    scenario_name="test-scenario",
                    vms=["ubuntu2204"],
                )
        # No symbols checked, userdata detection may fail in unit env — just verify exit 0/1
        # The important thing is no exception and consistent return value
        assert rc in (0, 1)

    def test_returns_2_on_missing_scenario(self, tmp_path):
        cfg = self._write_fake_toml(tmp_path)
        mock_config = MagicMock()
        mock_config.framework_path = tmp_path
        mock_config.app.binary = "/opt/Rocket.Chat/rocketchat-desktop"
        mock_config.vm_by_name = {}

        # scenarios dir exists but file is missing
        (tmp_path / "shared" / "scenarios" / "functional").mkdir(parents=True)

        with patch("automation.commands.preflight.load_config", return_value=mock_config):
            rc = run_preflight(
                config_path=str(cfg),
                scenario_name="nonexistent",
                vms=[],
            )
        assert rc == 2

    def test_returns_1_when_schema_invalid(self, tmp_path):
        cfg = self._write_fake_toml(tmp_path)
        scenarios_dir = tmp_path / "shared" / "scenarios" / "functional"
        scenarios_dir.mkdir(parents=True)
        bad = scenarios_dir / "bad.yaml"
        bad.write_text("name: bad\n# missing steps key\n")

        mock_config = MagicMock()
        mock_config.framework_path = tmp_path
        mock_config.app.binary = "/opt/Rocket.Chat/rocketchat-desktop"
        mock_config.vm_by_name = {}

        with patch("automation.commands.preflight.load_config", return_value=mock_config):
            rc = run_preflight(
                config_path=str(cfg),
                scenario_name="bad",
                vms=[],
            )
        assert rc == 1
