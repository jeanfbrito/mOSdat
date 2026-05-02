"""Unit tests for advanced runner features.

Covers:
  B6 — launch poll deadline (2-call VLM cap)
  B7 — SSH + VNC health probe (_probe_vm_health)
  F1 — popup sweep (_sweep_popups)
  C1 — agent fallback loop bounds (on_failure_agent)

Loads functional.py directly by file path to avoid the broken __init__ chain.
"""

import sys
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import importlib as _importlib
import PIL.Image as _pil_image_mod
if getattr(_pil_image_mod, "Image", None) is object:
    _importlib.reload(_pil_image_mod)
from PIL import Image

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Force-replace stubs so runner re-executes with real PIL
# ---------------------------------------------------------------------------

# Also stub automation.vlm.agent — runner imports it lazily inside run_step
# for C1 agent fallback. The stale installed copy doesn't have AgentLoop.
_agent_mod = types.ModuleType("automation.vlm.agent")
_agent_mod.AgentLoop = MagicMock  # placeholder; tests patch it per-test
sys.modules["automation.vlm.agent"] = _agent_mod

for _sn in ("automation.vlm.client", "automation.vlm.input", "automation.vlm.screenshot"):
    _m = types.ModuleType(_sn)
    _m.VLMClient = object
    _m.InputInjector = object
    _m.Screenshotter = object
    _m.VLMError = Exception
    sys.modules[_sn] = _m

# Always reload runner after PIL restoration
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


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _fake_image():
    return Image.new("RGB", (1920, 1080), (128, 128, 128))


def _make_runner(vlm_verify=True, process_running=True, screenshot_dir=None):
    vlm = MagicMock()
    vlm.verify.return_value = vlm_verify
    vlm.localize.return_value = (480, 270)
    vlm.verify_consistent.return_value = (vlm_verify, ["yes"] * 3)

    ss = MagicMock()
    ss.capture.return_value = (_fake_image(), (1920, 1080))
    ss.wait_for_stable.return_value = True

    inj = MagicMock()
    inj.process_running.return_value = process_running
    inj.is_windows = False

    runner = FunctionalRunner(
        vlm=vlm, screenshotter=ss, injector=inj,
        screenshot_dir=screenshot_dir,
        log_fn=lambda m: None,
    )
    return runner, vlm, ss, inj


# ---------------------------------------------------------------------------
# B6: launch poll — VLM call cap
# ---------------------------------------------------------------------------

class TestLaunchPollVlmCap:
    def test_vlm_verify_capped_at_two(self):
        """MAX_LAUNCH_VLM_CALLS=2 prevents runaway VLM calls."""
        runner, vlm, ss, inj = _make_runner(vlm_verify=True, process_running=True)
        # Provide enough time values for the deadline loop without hitting it
        time_vals = [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 100]
        with patch("time.sleep"), patch("time.time", side_effect=time_vals):
            runner.run_step(
                FunctionalStep(launch="/usr/bin/myapp", wait=5, launch_timeout=5, retries=1),
                1,
            )
        assert vlm.verify.call_count <= 2

    def test_window_not_visible_raises_step_failed(self):
        runner, vlm, ss, inj = _make_runner(vlm_verify=False, process_running=True)
        with patch("time.sleep"):
            with pytest.raises(StepFailed, match="Launch failed"):
                runner.run_step(
                    FunctionalStep(launch="/usr/bin/app", wait=1, launch_timeout=1, retries=1),
                    1,
                )


# ---------------------------------------------------------------------------
# B7: SSH + VNC health probe
# ---------------------------------------------------------------------------

class TestProbeVmHealth:
    def test_both_ok_returns_true(self):
        runner, _, ss, inj = _make_runner()
        inj.shell.return_value = None
        ss.capture.return_value = (_fake_image(), (1920, 1080))
        assert runner._probe_vm_health() is True

    def test_ssh_failure_returns_false(self):
        runner, _, ss, inj = _make_runner()
        inj.shell.side_effect = RuntimeError("SSH timeout")
        assert runner._probe_vm_health() is False

    def test_vnc_failure_returns_false(self):
        runner, _, ss, inj = _make_runner()
        inj.shell.return_value = None
        ss.capture.side_effect = Exception("VNC dead")
        assert runner._probe_vm_health() is False

    def test_small_framebuffer_returns_false(self):
        runner, _, ss, inj = _make_runner()
        inj.shell.return_value = None
        tiny = Image.new("RGB", (50, 50))
        ss.capture.return_value = (tiny, (50, 50))
        assert runner._probe_vm_health() is False


# ---------------------------------------------------------------------------
# F1: popup sweep
# ---------------------------------------------------------------------------

class TestPopupSweep:
    def test_popup_found_escape_sent(self):
        runner, vlm, ss, inj = _make_runner()
        vlm.verify.side_effect = [True, False]
        with patch("time.sleep"):
            dismissed = runner._sweep_popups(step_num=1, max_attempts=2)
        assert dismissed == 1
        inj.key.assert_called_with("escape")

    def test_no_popup_zero_dismissed(self):
        runner, vlm, ss, inj = _make_runner()
        vlm.verify.return_value = False
        dismissed = runner._sweep_popups(step_num=1)
        assert dismissed == 0
        inj.key.assert_not_called()

    def test_vlm_error_swallowed(self):
        runner, vlm, ss, inj = _make_runner()
        vlm.verify.side_effect = RuntimeError("VLM down")
        dismissed = runner._sweep_popups(step_num=1)
        assert dismissed == 0


# ---------------------------------------------------------------------------
# C1: agent fallback
# ---------------------------------------------------------------------------

class TestAgentFallback:
    def test_fallback_invoked_after_verify_exhaustion(self):
        runner, vlm, ss, inj = _make_runner(vlm_verify=False)
        fake_result = MagicMock()
        fake_result.success = True
        fake_result.turns_used = 3
        agent_cls = MagicMock()
        agent_cls.return_value.run.return_value = fake_result
        # AgentLoop is imported lazily inside run_step; patch the stub module directly
        sys.modules["automation.vlm.agent"].AgentLoop = agent_cls

        with patch("time.sleep"):
            runner.run_step(FunctionalStep(
                verify="dashboard visible", verify_timeout=1, retries=1,
                on_failure_agent={"goal": "navigate to dashboard", "budget_turns": 5},
            ), 1)

        agent_cls.return_value.run.assert_called_once()

    def test_fallback_failure_raises_step_failed(self):
        runner, vlm, ss, inj = _make_runner(vlm_verify=False)
        fake_result = MagicMock()
        fake_result.success = False
        fake_result.turns_used = 5
        fake_result.reason = "gave up"
        agent_cls = MagicMock()
        agent_cls.return_value.run.return_value = fake_result
        sys.modules["automation.vlm.agent"].AgentLoop = agent_cls

        with patch("time.sleep"):
            with pytest.raises(StepFailed):
                runner.run_step(FunctionalStep(
                    verify="visible", verify_timeout=1, retries=1,
                    on_failure_agent={"goal": "fix it", "budget_turns": 3},
                ), 1)

    def test_no_fallback_config_raises_directly(self):
        runner, vlm, ss, inj = _make_runner(vlm_verify=False)
        with patch("time.sleep"):
            with pytest.raises(StepFailed):
                runner.run_step(FunctionalStep(
                    verify="something", verify_timeout=1, retries=1,
                ), 1)
