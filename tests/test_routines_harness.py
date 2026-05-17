"""R3: tests for automation.routines.harness."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from automation.routines.fixtures import Fixture
from automation.routines.harness import build_synthetic_scenario, run_routine_test


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_ROUTINE_YAML = """\
name: cleanup-rocketchat
description: Kill RC and wipe userData
steps:
  - shell: echo cleanup
"""

_FIXTURE_YAML = """\
name: rc-killed-userdata-wiped
description: RC killed and wiped
vm_state:
  rc_killed: true
  userdata_wiped: true
  launched: false
setup_steps:
  - shell: echo setup
teardown_steps:
  - shell: echo teardown
"""

_FIXTURE_WITH_CONFIG_YAML = """\
name: rc-launched-2-server
description: 2-server launched
vm_state:
  rc_killed: true
  launched: true
  config:
    isTelephonyEnabled: false
    servers:
      - url: "https://open.rocket.chat/"
        title: Open
setup_steps:
  - shell: echo setup
teardown_steps: []
"""


def _make_fixture(yaml_str: str) -> Fixture:
    import yaml
    return Fixture.model_validate(yaml.safe_load(yaml_str))


def _make_routine_dir(tmp_path: Path) -> Path:
    rdir = tmp_path / "routines"
    rdir.mkdir()
    (rdir / "cleanup-rocketchat.yaml").write_text(_ROUTINE_YAML)
    return rdir


# ---------------------------------------------------------------------------
# 1. build_synthetic_scenario — no fixture
# ---------------------------------------------------------------------------

def test_build_synthetic_no_fixture(tmp_path):
    rdir = _make_routine_dir(tmp_path)
    with patch("automation.routines.loader.routines_dir", return_value=rdir):
        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        steps = build_synthetic_scenario("cleanup-rocketchat", fixture=None)
    # event_step + shell step from routine (no setup/teardown)
    assert len(steps) >= 1
    shell_steps = [s for s in steps if s.get("shell") == "echo cleanup"]
    assert shell_steps, "expected routine shell step in output"


# ---------------------------------------------------------------------------
# 2. build_synthetic_scenario — with fixture (setup + teardown bracketing)
# ---------------------------------------------------------------------------

def test_build_synthetic_with_fixture(tmp_path):
    rdir = _make_routine_dir(tmp_path)
    fx = _make_fixture(_FIXTURE_YAML)
    with patch("automation.routines.loader.routines_dir", return_value=rdir):
        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        steps = build_synthetic_scenario("cleanup-rocketchat", fixture=fx)
    shells = [s.get("shell", "") for s in steps]
    setup_idx = next(i for i, s in enumerate(shells) if "echo setup" in s)
    cleanup_idx = next(i for i, s in enumerate(shells) if "echo cleanup" in s)
    teardown_idx = next(i for i, s in enumerate(shells) if "echo teardown" in s)
    assert setup_idx < cleanup_idx < teardown_idx


# ---------------------------------------------------------------------------
# 3. build_synthetic_scenario — routine not found → FileNotFoundError
# ---------------------------------------------------------------------------

def test_build_synthetic_routine_not_found(tmp_path):
    rdir = tmp_path / "routines"
    rdir.mkdir()
    with patch("automation.routines.loader.routines_dir", return_value=rdir):
        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        with pytest.raises(FileNotFoundError):
            build_synthetic_scenario("no-such-routine")


# ---------------------------------------------------------------------------
# 4. run_routine_test — routine not found returns exit 1
# ---------------------------------------------------------------------------

def test_run_routine_test_routine_not_found(tmp_path):
    rdir = tmp_path / "routines"
    rdir.mkdir()
    fdir = tmp_path / "fixtures"
    fdir.mkdir()
    with patch("automation.routines.loader.routines_dir", return_value=rdir), \
         patch("automation.routines.fixtures.fixtures_dir", return_value=fdir):
        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        rc = run_routine_test("no-such-routine", vm_name="ubuntu2204")
    assert rc == 1


# ---------------------------------------------------------------------------
# 5. run_routine_test — fixture not found returns exit 2
# ---------------------------------------------------------------------------

def test_run_routine_test_fixture_not_found(tmp_path):
    rdir = _make_routine_dir(tmp_path)
    fdir = tmp_path / "fixtures"
    fdir.mkdir()
    with patch("automation.routines.loader.routines_dir", return_value=rdir), \
         patch("automation.routines.fixtures.fixtures_dir", return_value=fdir):
        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        from automation.routines.fixtures import load_fixture as lf; lf.cache_clear()
        rc = run_routine_test(
            "cleanup-rocketchat",
            vm_name="ubuntu2204",
            fixture_name="no-such-fixture",
        )
    assert rc == 2


# ---------------------------------------------------------------------------
# 6. run_routine_test — VM unreachable returns exit 3
# ---------------------------------------------------------------------------

def test_run_routine_test_vm_unreachable(tmp_path):
    rdir = _make_routine_dir(tmp_path)
    with patch("automation.routines.loader.routines_dir", return_value=rdir), \
         patch("automation.routines.harness._probe_ssh", return_value=False):
        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        # Supply a fake config_path so the harness attempts SSH probe
        with patch("automation.routines.harness.Path") as _p, \
             patch("automation.config.load_config") as mock_cfg:
            mock_vm = MagicMock()
            mock_vm.ip = "192.168.1.99"
            mock_vm.user = "user"
            mock_vm.packages = []
            mock_cfg.return_value.vm_by_name = {"ubuntu2204": mock_vm}
            rc = run_routine_test(
                "cleanup-rocketchat",
                vm_name="ubuntu2204",
                config_path="/fake/config.toml",
            )
    assert rc == 3


# ---------------------------------------------------------------------------
# 7. run_routine_test — mocked FunctionalRunner pass → returns 0
# ---------------------------------------------------------------------------

def _make_mock_cfg(tmp_path=None):
    """Build a mock config object matching the real config shape used by harness."""
    mock_cfg = MagicMock()
    mock_vm = MagicMock()
    mock_vm.ip = "192.168.1.10"
    mock_vm.user = "user"
    mock_vm.vmid = 100
    mock_vm.packages = []
    mock_vm.is_windows = False
    mock_cfg.vm_by_name = {"ubuntu2204": mock_vm}
    mock_cfg.proxmox = MagicMock()
    mock_cfg.vlm.model = "holo2-4b"
    mock_cfg.vlm.verify_model = "qwen"
    mock_cfg.vlm.base_url = "http://localhost"
    return mock_cfg, mock_vm


def test_run_routine_test_runner_pass(tmp_path):
    rdir = _make_routine_dir(tmp_path)
    screenshot_dir = tmp_path / "shots"
    mock_cfg, mock_vm = _make_mock_cfg()

    mock_vnc = MagicMock()
    mock_vnc.__enter__ = lambda s: s
    mock_vnc.__exit__ = MagicMock(return_value=False)

    mock_runner = MagicMock()
    mock_runner.run_test.return_value = (True, "ok")

    with patch("automation.routines.loader.routines_dir", return_value=rdir), \
         patch("automation.routines.harness._probe_ssh", return_value=True), \
         patch("automation.config.load_config", return_value=mock_cfg), \
         patch("automation.proxmox.api.ProxmoxAPI"), \
         patch("automation.transport.vnc.VncClient", return_value=mock_vnc), \
         patch("automation.vlm.client.VLMClient"), \
         patch("automation.vlm.input.InputInjector"), \
         patch("automation.vlm.screenshot.Screenshotter"), \
         patch("automation.runners.functional.FunctionalRunner", return_value=mock_runner), \
         patch("automation.runners.scenario_loader.parse_step", side_effect=lambda s: s):

        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        rc = run_routine_test(
            "cleanup-rocketchat",
            vm_name="ubuntu2204",
            config_path="/fake/config.toml",
            screenshot_dir=screenshot_dir,
        )

    assert rc == 0


# ---------------------------------------------------------------------------
# 8. run_routine_test — mocked FunctionalRunner fail → returns 1
# ---------------------------------------------------------------------------

def test_run_routine_test_runner_fail(tmp_path):
    rdir = _make_routine_dir(tmp_path)
    screenshot_dir = tmp_path / "shots"
    mock_cfg, mock_vm = _make_mock_cfg()

    mock_vnc = MagicMock()
    mock_vnc.__enter__ = lambda s: s
    mock_vnc.__exit__ = MagicMock(return_value=False)

    mock_runner = MagicMock()
    mock_runner.run_test.return_value = (False, "step 2 failed")

    with patch("automation.routines.loader.routines_dir", return_value=rdir), \
         patch("automation.routines.harness._probe_ssh", return_value=True), \
         patch("automation.config.load_config", return_value=mock_cfg), \
         patch("automation.proxmox.api.ProxmoxAPI"), \
         patch("automation.transport.vnc.VncClient", return_value=mock_vnc), \
         patch("automation.vlm.client.VLMClient"), \
         patch("automation.vlm.input.InputInjector"), \
         patch("automation.vlm.screenshot.Screenshotter"), \
         patch("automation.runners.functional.FunctionalRunner", return_value=mock_runner), \
         patch("automation.runners.scenario_loader.parse_step", side_effect=lambda s: s):

        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        rc = run_routine_test(
            "cleanup-rocketchat",
            vm_name="ubuntu2204",
            config_path="/fake/config.toml",
            screenshot_dir=screenshot_dir,
        )

    assert rc == 1
