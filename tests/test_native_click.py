"""Tests for the native Windows click (003 workstream B).

A VNC pointer click on a Windows toast dismisses the toast. A click made
inside the desktop session (Session 1) with user32 activates it.
``click_at: {x, y, native: true}`` routes to ``InputInjector.native_click``.

Covers:
  - ClickAtCoords schema: ``native`` accepted (bool), default False, typos rejected
  - Runner routing: native -> native_click on Windows, default -> VNC click,
    Linux -> warning + VNC click, ``native: true`` in the click event
  - native_click PowerShell: DPI aware call, coordinates, button flags,
    own task name, wait for the marker file, Windows only
All SSH/VNC calls are mocked — no network.
"""

import base64
import importlib.util
import json
import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from pydantic import ValidationError

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Load scenario.py under a private name (no package imports, no stub clash)
# ---------------------------------------------------------------------------

_SCEN_NAME = "automation._scenario_native_click_test"
_sspec = importlib.util.spec_from_file_location(_SCEN_NAME, _PROJ / "automation" / "scenario.py")
_scenario_mod = importlib.util.module_from_spec(_sspec)
sys.modules[_SCEN_NAME] = _scenario_mod
_sspec.loader.exec_module(_scenario_mod)
ScenarioModel = _scenario_mod.ScenarioModel


def _validate(data):
    return ScenarioModel.model_validate(data)


# ---------------------------------------------------------------------------
# Load input.py with stubbed dependencies (sibling tests stub automation.*)
# ---------------------------------------------------------------------------

class _StubCursorConfig:
    profile = "linear"
    duration_ms = 0
    seed = None
    hover_dwell_ms = 0


def _load_input_module():
    # Stub the parent package too, so the real automation.transport is not
    # imported inside patch.dict and then dropped from sys.modules on exit.
    stubs = {}
    transport_pkg = types.ModuleType("automation.transport")
    transport_pkg.__path__ = []
    stubs["automation.transport"] = transport_pkg
    cfg = types.ModuleType("automation.config")
    cfg.CursorConfig = _StubCursorConfig
    stubs["automation.config"] = cfg
    ssh = types.ModuleType("automation.transport.ssh")
    ssh.SSHClient = object
    stubs["automation.transport.ssh"] = ssh
    vnc = types.ModuleType("automation.transport.vnc")
    vnc.VncClient = object
    stubs["automation.transport.vnc"] = vnc
    name = "automation.vlm._input_native_click_test"
    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location(name, _PROJ / "automation" / "vlm" / "input.py")
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "automation.vlm"
        spec.loader.exec_module(mod)
    return mod


_input_mod = _load_input_module()
InputInjector = _input_mod.InputInjector


def _decode_ps(cmd: str) -> str:
    """Decode the -EncodedCommand payload of a _ps_encoded() command."""
    m = re.search(r"-EncodedCommand (\S+)", cmd)
    assert m, cmd
    return base64.b64decode(m.group(1)).decode("utf-16-le")


def _win_injector(stdout="native_click:ok"):
    ssh = MagicMock()
    ssh.run.return_value = types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    vnc = MagicMock()
    return InputInjector(vnc, ssh, is_windows=True), ssh, vnc


# ---------------------------------------------------------------------------
# Load the runner (same pattern as test_runner_dispatch.py)
# ---------------------------------------------------------------------------

for _stub_name in (
    "automation.vlm.client",
    "automation.vlm.input",
    "automation.vlm.screenshot",
):
    _m = types.ModuleType(_stub_name)
    _m.VLMClient = object
    _m.InputInjector = object
    _m.Screenshotter = object
    _m.VLMError = Exception
    sys.modules[_stub_name] = _m

_runner_spec = importlib.util.spec_from_file_location(
    "automation.runners.functional",
    _PROJ / "automation" / "runners" / "functional.py",
)
_runner_mod = importlib.util.module_from_spec(_runner_spec)
_runner_mod.__package__ = "automation.runners"
sys.modules["automation.runners.functional"] = _runner_mod
_runner_spec.loader.exec_module(_runner_mod)

FunctionalRunner = _runner_mod.FunctionalRunner
FunctionalStep = _runner_mod.FunctionalStep


def _make_runner(is_windows, tmp_path=None):
    vlm = MagicMock()
    ss = MagicMock()
    # A mock image, not PIL: other test files stub PIL, which leaves .png
    # without a writer and makes the click-screenshot save fail.
    img = MagicMock(size=(1920, 1080))
    img.copy.return_value = img
    ss.capture.return_value = (img, (1920, 1080))
    ss.wait_for_stable.return_value = True
    inj = MagicMock()
    inj.process_running.return_value = True
    inj.is_windows = is_windows
    logs = []
    runner = FunctionalRunner(
        vlm=vlm, screenshotter=ss, injector=inj,
        screenshot_dir=tmp_path,
        log_fn=logs.append,
    )
    return runner, inj, logs


def _click_events(tmp_path):
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if json.loads(line)["event"] == "click"]


# ---------------------------------------------------------------------------
# B2 schema
# ---------------------------------------------------------------------------

class TestClickAtNativeSchema:
    def test_native_true_accepted(self):
        model = _validate({"steps": [{"click_at": {"x": 10, "y": 20, "native": True}}]})
        assert model.steps[0].click_at.native is True

    def test_native_defaults_false(self):
        model = _validate({"steps": [{"click_at": {"x": 10, "y": 20}}]})
        assert model.steps[0].click_at.native is False

    def test_native_with_right_click_accepted(self):
        model = _validate({"steps": [{"click_at": {"x": 10, "y": 20, "native": True}, "click": "right"}]})
        assert model.steps[0].click_at.native is True
        assert model.steps[0].click == "right"

    def test_native_non_bool_rejected(self):
        with pytest.raises(ValidationError):
            _validate({"steps": [{"click_at": {"x": 10, "y": 20, "native": "sometimes"}}]})

    def test_native_typo_still_rejected(self):
        # extra="forbid" stays on ClickAtCoords
        with pytest.raises(ValidationError):
            _validate({"steps": [{"click_at": {"x": 10, "y": 20, "nativ": True}}]})


# ---------------------------------------------------------------------------
# B2 runner routing
# ---------------------------------------------------------------------------

class TestNativeClickRouting:
    def test_native_on_windows_calls_native_click_not_vnc(self, tmp_path):
        runner, inj, _ = _make_runner(is_windows=True, tmp_path=tmp_path)
        step = FunctionalStep(click_at={"x": 50, "y": 75, "native": True}, retries=1)
        runner.run_step(step, 1)
        inj.native_click.assert_called_once_with(50, 75, button=1)
        inj.click.assert_not_called()

    def test_native_right_click_on_windows_passes_button_3(self, tmp_path):
        runner, inj, _ = _make_runner(is_windows=True, tmp_path=tmp_path)
        step = FunctionalStep(click_at={"x": 50, "y": 75, "native": True}, click="right", retries=1)
        runner.run_step(step, 1)
        inj.native_click.assert_called_once_with(50, 75, button=3)
        inj.click.assert_not_called()

    def test_native_click_event_has_native_true(self, tmp_path):
        runner, _, _ = _make_runner(is_windows=True, tmp_path=tmp_path)
        step = FunctionalStep(click_at={"x": 50, "y": 75, "native": True}, retries=1)
        runner.run_step(step, 1)
        events = _click_events(tmp_path)
        assert len(events) == 1
        assert events[0]["native"] is True
        assert (events[0]["x"], events[0]["y"], events[0]["button"]) == (50, 75, "left")

    def test_default_on_windows_uses_vnc_click(self, tmp_path):
        runner, inj, _ = _make_runner(is_windows=True, tmp_path=tmp_path)
        step = FunctionalStep(click_at={"x": 50, "y": 75}, retries=1)
        runner.run_step(step, 1)
        inj.click.assert_called_once_with(50, 75, button=1, motion=None, dwell_ms=None)
        inj.native_click.assert_not_called()
        assert "native" not in _click_events(tmp_path)[0]

    def test_native_false_on_windows_uses_vnc_click(self, tmp_path):
        runner, inj, _ = _make_runner(is_windows=True, tmp_path=tmp_path)
        step = FunctionalStep(click_at={"x": 50, "y": 75, "native": False}, retries=1)
        runner.run_step(step, 1)
        inj.click.assert_called_once_with(50, 75, button=1, motion=None, dwell_ms=None)
        inj.native_click.assert_not_called()

    def test_native_on_linux_warns_and_uses_vnc_click(self, tmp_path):
        runner, inj, logs = _make_runner(is_windows=False, tmp_path=tmp_path)
        step = FunctionalStep(click_at={"x": 50, "y": 75, "native": True}, retries=1)
        runner.run_step(step, 1)
        inj.click.assert_called_once_with(50, 75, button=1, motion=None, dwell_ms=None)
        inj.native_click.assert_not_called()
        assert any("native click is Windows-only; using VNC" in m for m in logs)
        assert "native" not in _click_events(tmp_path)[0]

    def test_native_with_hover_keeps_hover(self, tmp_path):
        runner, inj, _ = _make_runner(is_windows=True, tmp_path=tmp_path)
        step = FunctionalStep(click_at={"x": 50, "y": 75, "native": True}, hover=True, retries=1)
        runner.run_step(step, 1)
        inj.hover.assert_called_once_with(50, 75, motion=None, dwell_ms=None)
        inj.native_click.assert_not_called()
        inj.click.assert_not_called()


# ---------------------------------------------------------------------------
# B1 InputInjector.native_click
# ---------------------------------------------------------------------------

class TestNativeClickPowerShell:
    def test_inner_script_dpi_aware_before_set_cursor_pos(self):
        ps = _input_mod._native_click_inner_ps(200, 300, 1)
        assert "[MosdatClick]::SetProcessDPIAware()" in ps
        assert "[MosdatClick]::SetCursorPos(200, 300)" in ps
        assert ps.index("::SetProcessDPIAware()") < ps.index("::SetCursorPos(")

    def test_inner_script_left_button_flags(self):
        ps = _input_mod._native_click_inner_ps(10, 20, 1)
        assert "mouse_event(0x0002," in ps
        assert "mouse_event(0x0004," in ps
        assert ps.index("0x0002") < ps.index("0x0004")
        assert "0x0008" not in ps and "0x0010" not in ps

    def test_inner_script_right_button_flags(self):
        ps = _input_mod._native_click_inner_ps(10, 20, 3)
        assert "mouse_event(0x0008," in ps
        assert "mouse_event(0x0010," in ps
        assert ps.index("0x0008") < ps.index("0x0010")
        assert "0x0002" not in ps and "0x0004" not in ps

    def test_inner_script_writes_marker_after_button_up(self):
        ps = _input_mod._native_click_inner_ps(10, 20, 1)
        assert ps.index("mosdat_click.done") > ps.index("0x0004")

    def test_unsupported_button_raises(self):
        with pytest.raises(ValueError):
            _input_mod._native_click_inner_ps(10, 20, 2)

    def test_native_click_runs_session1_task_and_waits(self):
        inj, ssh, vnc = _win_injector()
        inj.native_click(200, 300)
        assert ssh.run.call_count == 1
        outer = _decode_ps(ssh.run.call_args.args[0])
        assert "<LogonType>InteractiveToken</LogonType>" in outer
        assert 'schtasks /Run /TN "mosdat-click"' in outer
        # waits for the marker file, then deletes the task and the temp files
        assert "Test-Path $done" in outer
        assert outer.index("Test-Path $done") > outer.index("schtasks /Run")
        assert outer.rindex('schtasks /Delete /F /TN "mosdat-click"') > outer.index("Test-Path $done")
        assert "mosdat_click.xml" in outer.split("Test-Path $done")[-1]
        # the task runs the inner click script
        m = re.search(r"<Arguments>[^<]*-EncodedCommand (\S+)</Arguments>", outer)
        assert m
        inner = base64.b64decode(m.group(1)).decode("utf-16-le")
        assert inner == _input_mod._native_click_inner_ps(200, 300, 1)

    def test_native_click_does_not_touch_vnc(self):
        inj, _, vnc = _win_injector()
        inj.native_click(200, 300, button=3)
        assert vnc.mock_calls == []

    def test_native_click_timeout_raises(self):
        inj, _, _ = _win_injector(stdout="native_click:timeout")
        with pytest.raises(RuntimeError, match="did not complete"):
            inj.native_click(200, 300)

    def test_native_click_linux_raises(self):
        ssh = MagicMock()
        inj = InputInjector(MagicMock(), ssh, is_windows=False)
        with pytest.raises(RuntimeError, match="Windows-only"):
            inj.native_click(1, 2)
        ssh.run.assert_not_called()
