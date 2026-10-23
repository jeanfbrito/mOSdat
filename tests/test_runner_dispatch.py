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
    # I14: verify_with_meta returns (verdict, raw_response, cache_hit).
    # Sync via side_effect so tests that mutate `vlm.verify.return_value`
    # mid-flight still take effect.
    def _verify_meta(*args, **kwargs):
        result = vlm.verify(*args, **kwargs)
        return (result, "yes" if result else "no", False)
    vlm.verify_with_meta.side_effect = _verify_meta
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
        # I14: shell dispatch goes through shell_result() (richer return)
        inj.shell_result.assert_called_with("echo hi")


# ---------------------------------------------------------------------------
# Localize + click
# ---------------------------------------------------------------------------

class TestLocalizeStep:
    def test_localize_issues_click(self):
        runner, vlm, _, inj = _make_runner()
        runner.run_step(FunctionalStep(localize="the Submit button", retries=1), 1)
        vlm.localize.assert_called_once()
        inj.click.assert_called_once_with(200, 300, button=1, motion=None, dwell_ms=None)

    def test_localize_issues_right_click(self):
        runner, vlm, _, inj = _make_runner()
        runner.run_step(FunctionalStep(localize="the Submit button", click="right", retries=1), 1)
        vlm.localize.assert_called_once()
        inj.click.assert_called_once_with(200, 300, button=3, motion=None, dwell_ms=None)

    def test_localize_issues_hover_without_click(self):
        runner, vlm, _, inj = _make_runner()
        runner.run_step(FunctionalStep(localize="help icon", hover=True, retries=1), 1)
        vlm.localize.assert_called_once()
        inj.hover.assert_called_once_with(200, 300, motion=None, dwell_ms=None)
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


# ---------------------------------------------------------------------------
# Stage 1D: atspi: + verify_atspi: dispatch
# ---------------------------------------------------------------------------

def _make_runner_with_atspi(verify_result=True, click_raises=None):
    """Variant of _make_runner that wires a mocked AtspiClient."""
    runner, vlm, ss, inj = _make_runner()
    atspi = MagicMock()
    if click_raises is not None:
        atspi.click.side_effect = click_raises
    else:
        atspi.click.return_value = {"ok": True}
    atspi.verify.return_value = verify_result
    runner.atspi = atspi
    return runner, vlm, ss, inj, atspi


class TestAtspiStep:
    """Stage 1D: AT-SPI dispatch replaces VLM-localize+injector.click."""

    def test_atspi_step_skips_vlm_localize(self):
        runner, vlm, _, inj, atspi = _make_runner_with_atspi()
        step = FunctionalStep(
            atspi={"role": "push button", "name": "Connect"},
            retries=1,
        )
        runner.run_step(step, 1)
        atspi.click.assert_called_once_with(
            role="push button", name="Connect", input_injector=inj,
        )
        vlm.localize.assert_not_called()
        inj.click.assert_not_called()

    def test_atspi_step_without_runner_raises_runtime_error(self):
        # self.atspi is None → loud failure. The retry loop wraps the
        # underlying RuntimeError in StepFailed, but the message still
        # surfaces the AtspiClient wiring hint (no silent fallthrough).
        runner, _, _, _ = _make_runner()
        step = FunctionalStep(
            atspi={"role": "push button", "name": "Connect"},
            retries=1,
        )
        with pytest.raises(StepFailed, match="AtspiClient"):
            runner.run_step(step, 1)

    def test_atspi_failure_without_localize_propagates(self):
        from automation.atspi import AtspiError
        runner, vlm, _, inj, atspi = _make_runner_with_atspi(
            click_raises=AtspiError("find failed")
        )
        step = FunctionalStep(
            atspi={"role": "push button", "name": "Connect"},
            retries=1,
        )
        with pytest.raises(StepFailed):
            runner.run_step(step, 1)
        # No VLM fallback — localize unused.
        vlm.localize.assert_not_called()

    def test_atspi_failure_with_localize_falls_back_to_vlm(self):
        from automation.atspi import AtspiError
        runner, vlm, _, inj, atspi = _make_runner_with_atspi(
            click_raises=AtspiError("find failed")
        )
        # localize: provides the VLM fallback target.
        step = FunctionalStep(
            atspi={"role": "push button", "name": "Connect"},
            localize="the Connect button",
            retries=1,
        )
        runner.run_step(step, 1)
        atspi.click.assert_called_once()
        # VLM path engaged after atspi failure.
        vlm.localize.assert_called_once()
        inj.click.assert_called_once_with(200, 300, button=1, motion=None, dwell_ms=None)


class TestVerifyAtspiStep:
    """Stage 1D: verify_atspi: short-circuits the VLM verify path."""

    def test_verify_atspi_true_passes_without_vlm(self):
        runner, vlm, _, _, atspi = _make_runner_with_atspi(verify_result=True)
        step = FunctionalStep(
            verify_atspi={"role": "frame", "name": "Rocket.Chat"},
            verify_timeout=1,
            retries=1,
        )
        with patch("time.sleep"):
            runner.run_step(step, 1)
        atspi.verify.assert_called_once_with(role="frame", name="Rocket.Chat")
        vlm.verify.assert_not_called()

    def test_verify_atspi_false_raises_step_failed(self):
        runner, vlm, _, _, atspi = _make_runner_with_atspi(verify_result=False)
        step = FunctionalStep(
            verify_atspi={"role": "frame", "name": "Nope"},
            verify_timeout=1,
            retries=1,
        )
        with patch("time.sleep"):
            with pytest.raises(StepFailed):
                runner.run_step(step, 1)
        atspi.verify.assert_called()
        vlm.verify.assert_not_called()

    def test_verify_atspi_without_runner_raises_runtime_error(self):
        # Same wrap-by-retry-loop semantic as TestAtspiStep above; the
        # error message still names AtspiClient.
        runner, _, _, _ = _make_runner()
        step = FunctionalStep(
            verify_atspi={"role": "frame", "name": "x"},
            verify_timeout=1,
            retries=1,
        )
        with pytest.raises(StepFailed, match="AtspiClient"):
            runner.run_step(step, 1)


# ---------------------------------------------------------------------------
# Stage 2: wait_for: unified poll-on-VM condition wait
# ---------------------------------------------------------------------------

def _make_runner_with_wait_for(fire_result=None, raises=None):
    """Variant of _make_runner that wires a mocked AtspiClient.wait_for."""
    runner, vlm, ss, inj = _make_runner()
    atspi = MagicMock()
    if raises is not None:
        atspi.wait_for.side_effect = raises
    else:
        atspi.wait_for.return_value = fire_result or {
            "ok": True, "matched": "any",
            "cond": {"role": "frame", "name": "Rocket.Chat"},
            "polls": 2,
        }
    runner.atspi = atspi
    return runner, vlm, ss, inj, atspi


class TestUiaDispatch:
    """Stage 4: when the runner is constructed with a UiaClient (Windows VM),
    the same atspi:/verify_atspi:/wait_for: step fields dispatch through
    self.uia instead of self.atspi. self.a11y unifies the lookup.
    """

    def _make_runner_with_uia(self, verify_result=True):
        runner, vlm, ss, inj = _make_runner()
        uia = MagicMock()
        uia.click.return_value = {"ok": True}
        uia.verify.return_value = verify_result
        uia.wait_for.return_value = {
            "ok": True, "matched": "any",
            "cond": {"role": "Button", "name": "OK"},
            "polls": 1,
        }
        runner.uia = uia
        return runner, vlm, ss, inj, uia

    def test_atspi_step_routes_to_uia_on_windows_vm(self):
        # atspi: field with uia client (no atspi client) → UiaClient.click runs.
        runner, vlm, _, inj, uia = self._make_runner_with_uia()
        assert runner.atspi is None  # ctor default for Linux-style wiring absence
        step = FunctionalStep(
            atspi={"role": "Button", "name": "OK"},
            retries=1,
        )
        runner.run_step(step, 1)
        uia.click.assert_called_once_with(
            role="Button", name="OK", input_injector=inj,
        )
        vlm.localize.assert_not_called()
        inj.click.assert_not_called()

    def test_verify_atspi_step_routes_to_uia_on_windows_vm(self):
        runner, vlm, _, _, uia = self._make_runner_with_uia(verify_result=True)
        step = FunctionalStep(
            verify_atspi={"role": "Window", "name": "Rocket.Chat"},
            verify_timeout=1,
            retries=1,
        )
        with patch("time.sleep"):
            runner.run_step(step, 1)
        uia.verify.assert_called_once_with(role="Window", name="Rocket.Chat")
        vlm.verify.assert_not_called()

    def test_wait_for_step_routes_to_uia_on_windows_vm(self):
        runner, vlm, _, inj, uia = self._make_runner_with_uia()
        step = FunctionalStep(
            wait_for={"any": [{"role": "Window", "name": "Rocket.Chat"}],
                      "timeout": 5},
            retries=1,
        )
        runner.run_step(step, 1)
        uia.wait_for.assert_called_once_with(
            any=[{"role": "Window", "name": "Rocket.Chat"}], timeout=5,
        )
        vlm.localize.assert_not_called()
        inj.click.assert_not_called()

    def test_a11y_property_picks_uia_when_both_set(self):
        # Defensive: even if both are wired (shouldn't happen in practice),
        # the property prefers uia. Tests would not exercise this path —
        # validates the precedence rule in the property docstring.
        runner, _, _, _ = _make_runner()
        runner.atspi = MagicMock(name="atspi")
        runner.uia = MagicMock(name="uia")
        assert runner.a11y is runner.uia

    def test_atspi_step_without_any_a11y_raises_naming_both(self):
        # No atspi, no uia → RuntimeError names BOTH clients (helpful for
        # debugging which kwarg to wire). Wrapped in StepFailed by the
        # retry loop (atspi: runs inside it).
        runner, _, _, _ = _make_runner()
        assert runner.atspi is None and runner.uia is None
        step = FunctionalStep(
            atspi={"role": "Button", "name": "OK"},
            retries=1,
        )
        with pytest.raises(StepFailed, match="AtspiClient or UiaClient"):
            runner.run_step(step, 1)

    def test_wait_for_without_any_a11y_raises_naming_both(self):
        # wait_for: fires BEFORE the retry loop — RuntimeError propagates
        # unwrapped (matches existing test_wait_for_without_runner_raises).
        runner, _, _, _ = _make_runner()
        step = FunctionalStep(
            wait_for={"any": [{"role": "Window", "name": "x"}], "timeout": 1},
            retries=1,
        )
        with pytest.raises(RuntimeError, match="AtspiClient or UiaClient"):
            runner.run_step(step, 1)


class TestWaitForStep:
    """Stage 2: wait_for: short-circuits VLM verify poll loops.

    Worker polls AT-SPI conditions ON the VM in a single SSH round-trip,
    returning on first match (or all-match) or AtspiError on timeout.
    """

    def test_wait_for_fires_skips_pipeline(self):
        # wait_for returns ok → step passes, no VLM/click pipeline runs.
        runner, vlm, _, inj, atspi = _make_runner_with_wait_for()
        step = FunctionalStep(
            wait_for={"any": [{"role": "frame", "name": "Rocket.Chat"}],
                      "timeout": 5},
            retries=1,
        )
        runner.run_step(step, 1)
        atspi.wait_for.assert_called_once_with(
            any=[{"role": "frame", "name": "Rocket.Chat"}], timeout=5
        )
        vlm.localize.assert_not_called()
        vlm.verify.assert_not_called()
        inj.click.assert_not_called()

    def test_wait_for_timeout_raises_step_failed(self):
        # AtspiError from the client → StepFailed surfaces, no retry.
        from automation.atspi import AtspiError
        runner, vlm, _, _, atspi = _make_runner_with_wait_for(
            raises=AtspiError("wait_for timed out or failed: wait_for_timeout"),
        )
        step = FunctionalStep(
            wait_for={"any": [{"role": "push button", "name": "Connect"}],
                      "timeout": 1},
            retries=1,
        )
        with pytest.raises(StepFailed, match="wait_for"):
            runner.run_step(step, 1)
        atspi.wait_for.assert_called_once()
        vlm.verify.assert_not_called()

    def test_wait_for_without_runner_raises_runtime_error(self):
        # self.atspi is None → loud RuntimeError naming AtspiClient.
        # Early-return path: unlike atspi:/verify_atspi: which run inside
        # the retry loop and get wrapped in StepFailed, wait_for fires
        # BEFORE the retry loop, so RuntimeError propagates unwrapped.
        runner, _, _, _ = _make_runner()
        step = FunctionalStep(
            wait_for={"any": [{"role": "frame", "name": "x"}], "timeout": 1},
            retries=1,
        )
        with pytest.raises(RuntimeError, match="AtspiClient"):
            runner.run_step(step, 1)


# ---------------------------------------------------------------------------
# click_at: literal-coordinate click — skips VLM localize entirely
# ---------------------------------------------------------------------------

class TestClickAtStep:
    def test_click_at_issues_click_with_literal_coords_no_vlm(self):
        runner, vlm, _, inj = _make_runner()
        step = FunctionalStep(click_at={"x": 50, "y": 75}, retries=1)
        runner.run_step(step, 1)
        inj.click.assert_called_once_with(50, 75, button=1, motion=None, dwell_ms=None)
        vlm.localize.assert_not_called()

    def test_click_at_with_right_button(self):
        runner, vlm, _, inj = _make_runner()
        step = FunctionalStep(click_at={"x": 50, "y": 75}, click="right", retries=1)
        runner.run_step(step, 1)
        inj.click.assert_called_once_with(50, 75, button=3, motion=None, dwell_ms=None)
        vlm.localize.assert_not_called()

    def test_click_at_out_of_bounds_raises_step_failed(self):
        # Screenshotter captures a (1920, 1080) screen (see _make_runner);
        # coordinates beyond that must raise StepFailed, never misclick.
        runner, vlm, _, inj = _make_runner()
        step = FunctionalStep(click_at={"x": 5000, "y": 75}, retries=1)
        with pytest.raises(StepFailed, match="click_at"):
            runner.run_step(step, 1)
        inj.click.assert_not_called()
        vlm.localize.assert_not_called()


# ---------------------------------------------------------------------------
# poll_shell: deterministic REST/shell outcome polling (Card 2)
# ---------------------------------------------------------------------------

class _FakeShellResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestPollShellStep:
    def test_poll_shell_passes_on_first_try(self):
        runner, vlm, _, inj = _make_runner()
        inj.shell_result.return_value = _FakeShellResult(returncode=0, stdout="ok")
        step = FunctionalStep(
            poll_shell="check.sh", poll_exit_code=0,
            poll_timeout=5, poll_interval=0.05,
        )
        runner.run_step(step, 1)
        assert inj.shell_result.call_count == 1
        vlm.localize.assert_not_called()

    def test_poll_shell_passes_on_nth_try(self):
        runner, vlm, _, inj = _make_runner()
        inj.shell_result.side_effect = [
            _FakeShellResult(returncode=1, stdout="not yet"),
            _FakeShellResult(returncode=1, stdout="still not"),
            _FakeShellResult(returncode=0, stdout="done"),
        ]
        step = FunctionalStep(
            poll_shell="check.sh", poll_exit_code=0,
            poll_timeout=5, poll_interval=0.02,
        )
        runner.run_step(step, 1)
        assert inj.shell_result.call_count == 3

    def test_poll_shell_exhaustion_raises_step_failed(self):
        runner, vlm, _, inj = _make_runner()
        inj.shell_result.return_value = _FakeShellResult(returncode=1, stdout="nope")
        step = FunctionalStep(
            poll_shell="check.sh", poll_exit_code=0,
            poll_timeout=0.15, poll_interval=0.05,
        )
        with pytest.raises(StepFailed, match="poll_shell"):
            runner.run_step(step, 1)
        assert inj.shell_result.call_count >= 2

    def test_poll_shell_match_substring(self):
        runner, vlm, _, inj = _make_runner()
        inj.shell_result.return_value = _FakeShellResult(
            returncode=0, stdout="server is ready to accept connections"
        )
        step = FunctionalStep(
            poll_shell="check.sh", poll_exit_code=0, poll_match="ready to accept",
            poll_timeout=5, poll_interval=0.05,
        )
        runner.run_step(step, 1)
        assert inj.shell_result.call_count == 1

    def test_poll_shell_match_only_mode_exit_code_none(self):
        # poll_exit_code=None means only poll_match is checked — a nonzero
        # rc must not block success.
        runner, vlm, _, inj = _make_runner()
        inj.shell_result.return_value = _FakeShellResult(
            returncode=1, stdout="status: ready"
        )
        step = FunctionalStep(
            poll_shell="check.sh", poll_exit_code=None, poll_match="ready",
            poll_timeout=5, poll_interval=0.05,
        )
        runner.run_step(step, 1)
        assert inj.shell_result.call_count == 1


# ---------------------------------------------------------------------------
# verify_recorded: deferred VLM verification — queued, not run inline (Card 3)
# ---------------------------------------------------------------------------

class TestVerifyRecordedStep:
    def test_verify_recorded_is_queued_not_run_inline(self):
        runner, vlm, _, inj = _make_runner()
        step = FunctionalStep(
            verify_recorded={"prompt": "is a toast visible", "between": ["open toast", "close toast"]},
        )
        runner.run_step(step, 3)
        assert runner._pending_recorded_verifies == [{
            "prompt": "is a toast visible",
            "between": ["open toast", "close toast"],
            "step_num": 3,
        }]
        vlm.verify.assert_not_called()
        vlm.localize.assert_not_called()

    def test_verify_recorded_does_not_touch_injector(self):
        runner, vlm, _, inj = _make_runner()
        step = FunctionalStep(
            verify_recorded={"prompt": "is a toast visible", "between": [1, 2]},
        )
        runner.run_step(step, 1)
        inj.click.assert_not_called()
        inj.type_text.assert_not_called()

    def test_multiple_verify_recorded_steps_all_queued(self):
        runner, vlm, _, inj = _make_runner()
        step_a = FunctionalStep(verify_recorded={"prompt": "a", "between": [1, 2]})
        step_b = FunctionalStep(verify_recorded={"prompt": "b", "between": [3, 4]})
        runner.run_step(step_a, 1)
        runner.run_step(step_b, 2)
        assert len(runner._pending_recorded_verifies) == 2
        assert runner._pending_recorded_verifies[0]["prompt"] == "a"
        assert runner._pending_recorded_verifies[1]["prompt"] == "b"
        vlm.verify.assert_not_called()
