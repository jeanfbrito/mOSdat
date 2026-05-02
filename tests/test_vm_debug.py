"""Tests for I2 — VM-side debug artifact capture on smoke FAIL.

Covers:
  - _capture_vm_debug writes expected files into vm-debug/ on Linux FAIL
  - _capture_vm_debug writes expected files into vm-debug/ on Windows FAIL
  - SSH errors produce warnings, never crash the runner
  - run_test calls _capture_vm_debug when results_dir + vm_name provided on FAIL
  - run_test does NOT call _capture_vm_debug on PASS
"""

import sys
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch


import importlib as _importlib
import PIL.Image as _pil_image_mod
if getattr(_pil_image_mod, "Image", None) is object:
    _importlib.reload(_pil_image_mod)
from PIL import Image

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Stub dependencies and load functional module
# ---------------------------------------------------------------------------

_agent_mod = types.ModuleType("automation.vlm.agent")
_agent_mod.AgentLoop = MagicMock
sys.modules.setdefault("automation.vlm.agent", _agent_mod)

for _sn in ("automation.vlm.client", "automation.vlm.input", "automation.vlm.screenshot"):
    if _sn not in sys.modules:
        _m = types.ModuleType(_sn)
        _m.VLMClient = object
        _m.InputInjector = object
        _m.Screenshotter = object
        _m.VLMError = Exception
        sys.modules[_sn] = _m

# Re-use the module if already loaded by another test file in this session
if "automation.runners.functional" not in sys.modules:
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
# Helpers
# ---------------------------------------------------------------------------

def _fake_image():
    return Image.new("RGB", (1920, 1080), (64, 64, 64))


def _make_runner(is_windows=False, screenshot_dir=None):
    vlm = MagicMock()
    vlm.verify.return_value = True
    vlm.localize.return_value = (480, 270)

    ss = MagicMock()
    ss.capture.return_value = (_fake_image(), (1920, 1080))
    ss.wait_for_stable.return_value = True

    inj = MagicMock()
    inj.process_running.return_value = True
    inj.is_windows = is_windows
    # ssh.run returns an SSHResult-like object with stdout attribute
    inj.ssh.run.return_value = MagicMock(stdout="fake output line")

    runner = FunctionalRunner(
        vlm=vlm, screenshotter=ss, injector=inj,
        screenshot_dir=screenshot_dir,
        log_fn=lambda m: None,
    )
    return runner, vlm, ss, inj


# ---------------------------------------------------------------------------
# Direct _capture_vm_debug tests — Linux
# ---------------------------------------------------------------------------

class TestCaptureVmDebugLinux:
    def test_creates_debug_dir(self, tmp_path):
        runner, _, _, inj = _make_runner(screenshot_dir=tmp_path / "ss")
        inj.ssh.run.return_value = MagicMock(stdout="some output")
        results_dir = tmp_path / "results"

        runner._capture_vm_debug(results_dir, "myvm")

        assert (results_dir / "myvm" / "vm-debug").is_dir()

    def test_dmesg_file_written(self, tmp_path):
        runner, _, _, inj = _make_runner(screenshot_dir=tmp_path / "ss")
        inj.ssh.run.return_value = MagicMock(stdout="dmesg line 1\ndmesg line 2")
        results_dir = tmp_path / "results"

        runner._capture_vm_debug(results_dir, "myvm")

        dmesg = results_dir / "myvm" / "vm-debug" / "dmesg_tail.txt"
        assert dmesg.exists()
        assert "dmesg line" in dmesg.read_text()

    def test_journalctl_file_written(self, tmp_path):
        runner, _, _, inj = _make_runner(screenshot_dir=tmp_path / "ss")
        # All calls return same output — we just need journalctl file to exist
        inj.ssh.run.return_value = MagicMock(stdout="journal output")
        results_dir = tmp_path / "results"

        runner._capture_vm_debug(results_dir, "myvm")

        journal = results_dir / "myvm" / "vm-debug" / "journalctl_recent.txt"
        assert journal.exists()

    def test_ssh_error_does_not_crash(self, tmp_path):
        runner, _, _, inj = _make_runner(screenshot_dir=tmp_path / "ss")
        inj.ssh.run.side_effect = RuntimeError("SSH dead")
        results_dir = tmp_path / "results"

        # Must not raise
        runner._capture_vm_debug(results_dir, "myvm")

        # Debug dir still created; no artifact files (all SSH calls failed)
        debug_dir = results_dir / "myvm" / "vm-debug"
        assert debug_dir.is_dir()

    def test_empty_ssh_output_no_dmesg_file(self, tmp_path):
        runner, _, _, inj = _make_runner(screenshot_dir=tmp_path / "ss")
        inj.ssh.run.return_value = MagicMock(stdout="")
        results_dir = tmp_path / "results"

        runner._capture_vm_debug(results_dir, "myvm")

        # dmesg_tail.txt should not be written when output is empty
        dmesg = results_dir / "myvm" / "vm-debug" / "dmesg_tail.txt"
        assert not dmesg.exists()


# ---------------------------------------------------------------------------
# Direct _capture_vm_debug tests — Windows
# ---------------------------------------------------------------------------

class TestCaptureVmDebugWindows:
    def test_event_log_file_written(self, tmp_path):
        runner, _, _, inj = _make_runner(is_windows=True, screenshot_dir=tmp_path / "ss")
        inj.ssh.run.return_value = MagicMock(stdout="Event log entry")
        results_dir = tmp_path / "results"

        runner._capture_vm_debug(results_dir, "winvm")

        evt = results_dir / "winvm" / "vm-debug" / "event_log_app.txt"
        assert evt.exists()
        assert "Event log entry" in evt.read_text()

    def test_top_processes_file_written(self, tmp_path):
        runner, _, _, inj = _make_runner(is_windows=True, screenshot_dir=tmp_path / "ss")
        inj.ssh.run.return_value = MagicMock(stdout="process list output")
        results_dir = tmp_path / "results"

        runner._capture_vm_debug(results_dir, "winvm")

        procs = results_dir / "winvm" / "vm-debug" / "top_processes.txt"
        assert procs.exists()

    def test_windows_ssh_error_does_not_crash(self, tmp_path):
        runner, _, _, inj = _make_runner(is_windows=True, screenshot_dir=tmp_path / "ss")
        inj.ssh.run.side_effect = RuntimeError("WinRM dead")
        results_dir = tmp_path / "results"

        # Must not raise
        runner._capture_vm_debug(results_dir, "winvm")


# ---------------------------------------------------------------------------
# run_test integration — capture called on FAIL, skipped on PASS
# ---------------------------------------------------------------------------

class TestRunTestCapture:
    def _steps_that_fail(self):
        """One step that always fails (verify never returns True)."""
        return [FunctionalStep(
            shell="echo test",
            verify="something impossible",
            verify_timeout=1,
            retries=1,
        )]

    def _steps_that_pass(self):
        """One step that always passes (no verify)."""
        return [FunctionalStep(shell="echo ok")]

    def test_capture_called_on_fail(self, tmp_path):
        ss_dir = tmp_path / "screenshots"
        ss_dir.mkdir()
        runner, vlm, ss, inj = _make_runner(screenshot_dir=ss_dir)
        # Make verify always return False so the step fails
        vlm.verify.return_value = False
        inj.ssh.run.return_value = MagicMock(stdout="debug output")
        results_dir = tmp_path / "results"

        with patch("time.sleep"):
            passed, _ = runner.run_test(
                self._steps_that_fail(),
                name="test",
                results_dir=results_dir,
                vm_name="myvm",
            )

        assert not passed
        # vm-debug dir should be created
        assert (results_dir / "myvm" / "vm-debug").is_dir()

    def test_capture_not_called_on_pass(self, tmp_path):
        ss_dir = tmp_path / "screenshots"
        ss_dir.mkdir()
        runner, vlm, ss, inj = _make_runner(screenshot_dir=ss_dir)
        results_dir = tmp_path / "results"

        with patch("time.sleep"):
            passed, _ = runner.run_test(
                self._steps_that_pass(),
                name="test",
                results_dir=results_dir,
                vm_name="myvm",
            )

        assert passed
        # vm-debug dir should NOT be created on PASS
        assert not (results_dir / "myvm" / "vm-debug").exists()

    def test_no_results_dir_no_crash_on_fail(self, tmp_path):
        """run_test without results_dir/vm_name still returns False on FAIL."""
        ss_dir = tmp_path / "screenshots"
        ss_dir.mkdir()
        runner, vlm, ss, inj = _make_runner(screenshot_dir=ss_dir)
        vlm.verify.return_value = False

        with patch("time.sleep"):
            passed, _ = runner.run_test(
                self._steps_that_fail(),
                name="test",
            )

        assert not passed

    def test_capture_error_does_not_mask_fail(self, tmp_path):
        """If _capture_vm_debug raises, run_test still returns False (not propagated)."""
        ss_dir = tmp_path / "screenshots"
        ss_dir.mkdir()
        runner, vlm, ss, inj = _make_runner(screenshot_dir=ss_dir)
        vlm.verify.return_value = False
        results_dir = tmp_path / "results"

        # Make _capture_vm_debug blow up
        runner._capture_vm_debug = MagicMock(side_effect=RuntimeError("boom"))

        with patch("time.sleep"):
            passed, _ = runner.run_test(
                self._steps_that_fail(),
                name="test",
                results_dir=results_dir,
                vm_name="myvm",
            )

        assert not passed
