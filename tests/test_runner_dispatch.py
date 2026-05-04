"""Unit tests for FunctionalRunner step dispatch paths.

Covers: key, type, shell, launch, verify, localize-click, sleep (wait),
checkpoint.  All VLM/VNC/SSH calls are mocked — no network.
"""

import sys
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load runner module directly by file path (avoid broken __init__ chain)
# ---------------------------------------------------------------------------
_PROJ = Path(__file__).parent.parent

# Force-replace stubs so the runner re-executes with real PIL, not the stub
# left by test_if_visible.py.
for _stub_name in (
    "automation.vlm.client",
    "automation.vlm.input",
    "automation.vlm.screenshot",
):
    m = types.ModuleType(_stub_name)
    m.VLMClient = object
    m.InputInjector = object
    m.Screenshotter = object
    m.VLMError = Exception
    sys.modules[_stub_name] = m

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
StepFailed = _runner_mod.StepFailed


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

class FakeImage:
    pass


def _make_runner(vlm_verify=True, vlm_coords=(200, 300), screenshot_dir=None):
    vlm = MagicMock()
    vlm.verify.return_value = vlm_verify
    vlm.localize.return_value = vlm_coords
    vlm.localize_verified.return_value = vlm_coords
    vlm.localize_consistent.return_value = vlm_coords
    vlm.verify_consistent.return_value = (vlm_verify, ["yes"] * 3)

    ss = MagicMock()
    ss.capture.return_value = (FakeImage(), (1920, 1080))
    ss.wait_for_stable.return_value = True

    inj = MagicMock()
    inj.process_running.return_value = True
    inj.is_windows = False

    runner = FunctionalRunner(
        vlm=vlm, screenshotter=ss, injector=inj,
        screenshot_dir=screenshot_dir,
        log_fn=lambda m: None,
    )
    return runner, vlm, ss, inj


# ---------------------------------------------------------------------------
# Key dispatch
# ---------------------------------------------------------------------------

class TestKeyStep:
    def test_then_key_dispatched(self):
        runner, _, _, inj = _make_runner()
        step = FunctionalStep(then_key="enter")
        runner.run_step(step, 1)
        inj.key.assert_called_with("enter")

    def test_then_key_pre_dispatched(self):
        runner, _, _, inj = _make_runner()
        with patch("time.sleep"):
            runner.run_step(FunctionalStep(then_key_pre="super"), 1)
        inj.key.assert_called_with("super")

    def test_key_pre_then_type_then_key_order(self):
        runner, _, _, inj = _make_runner()
        calls = []
        inj.key.side_effect = lambda k: calls.append(("key", k))
        inj.type_text.side_effect = lambda t: calls.append(("type", t))
        with patch("time.sleep"):
            runner.run_step(FunctionalStep(then_key_pre="ctrl+a", then_type="hello", then_key="enter"), 1)
        assert calls == [("key", "ctrl+a"), ("type", "hello"), ("key", "enter")]


# ---------------------------------------------------------------------------
# Type dispatch
# ---------------------------------------------------------------------------

class TestTypeStep:
    def test_type_text_dispatched(self):
        runner, _, _, inj = _make_runner()
        with patch("time.sleep"):
            runner.run_step(FunctionalStep(then_type="hello world"), 1)
        inj.type_text.assert_called_with("hello world")


# ---------------------------------------------------------------------------
# Shell dispatch
# ---------------------------------------------------------------------------

class TestShellStep:
    def test_shell_dispatched(self):
        runner, _, _, inj = _make_runner()
        runner.run_step(FunctionalStep(shell="echo hi"), 1)
        inj.shell.assert_called_with("echo hi")


# ---------------------------------------------------------------------------
# Localize + click
# ---------------------------------------------------------------------------

class TestLocalizeStep:
    def test_localize_issues_click(self):
        runner, vlm, _, inj = _make_runner()
        runner.run_step(FunctionalStep(localize="the Submit button", retries=1), 1)
        vlm.localize.assert_called_once()
        inj.click.assert_called_once_with(200, 300, button=1)

    def test_localize_issues_right_click(self):
        runner, vlm, _, inj = _make_runner()
        runner.run_step(FunctionalStep(localize="the Submit button", click="right", retries=1), 1)
        vlm.localize.assert_called_once()
        inj.click.assert_called_once_with(200, 300, button=3)

    def test_localize_issues_hover_without_click(self):
        runner, vlm, _, inj = _make_runner()
        runner.run_step(FunctionalStep(localize="help icon", hover=True, retries=1), 1)
        vlm.localize.assert_called_once()
        inj.move.assert_called_once_with(200, 300)
        inj.click.assert_not_called()

    def test_localize_then_type_then_key(self):
        runner, _, _, inj = _make_runner()
        with patch("time.sleep"):
            runner.run_step(FunctionalStep(
                localize="the input field", then_type="abc", then_key="enter", retries=1
            ), 1)
        inj.click.assert_called_once()
        inj.type_text.assert_called_once_with("abc")
        inj.key.assert_called_with("enter")


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

class TestVerifyStep:
    def test_verify_true_passes(self):
        runner, _, _, _ = _make_runner(vlm_verify=True)
        with patch("time.sleep"):
            runner.run_step(FunctionalStep(verify="dashboard visible", verify_timeout=1, retries=1), 1)

    def test_verify_false_raises_step_failed(self):
        runner, _, _, _ = _make_runner(vlm_verify=False)
        with patch("time.sleep"):
            with pytest.raises(StepFailed):
                runner.run_step(FunctionalStep(verify="dashboard visible", verify_timeout=1, retries=1), 1)


# ---------------------------------------------------------------------------
# Wait
# ---------------------------------------------------------------------------

class TestWaitStep:
    def test_wait_calls_sleep(self):
        runner, _, _, _ = _make_runner()
        with patch("time.sleep") as mock_sleep:
            runner.run_step(FunctionalStep(then_key_pre="ctrl+a", wait=2), 1)
        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        assert 2 in sleep_args


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

class TestCheckpointStep:
    def test_checkpoint_step_no_vlm_no_click(self):
        runner, vlm, _, inj = _make_runner()
        runner.run_step(FunctionalStep(checkpoint="snap-before-install"), 1)
        vlm.localize.assert_not_called()
        inj.click.assert_not_called()
