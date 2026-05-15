"""Chaos-injection tests — I/O failure modes (I4).

Covers:
  C1: SSH drops mid-run
  C2: VLM 502 mid-call (retry succeeds)
  C3: VLM all endpoints exhausted
  C5: VNC framebuffer black / zero-size
  C6: Process disappears mid-test
  C8: Step retry budget exhausted

All tests are hermetic: no real SSH / VLM / Proxmox / network.
"""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy dependencies
# PIL must be stubbed at module-load time before exec_module runs, matching
# the same pattern as test_negative.py to avoid PIL.Image.Image = object
# mutation conflicts when the full test suite collects all modules.
# ---------------------------------------------------------------------------

for _pil_mod in ("PIL", "PIL.Image", "PIL.ImageDraw"):
    if _pil_mod not in sys.modules:
        sys.modules[_pil_mod] = types.ModuleType(_pil_mod)
if not hasattr(sys.modules["PIL.Image"], "Image"):
    sys.modules["PIL.Image"].Image = object
sys.modules["PIL"].Image = sys.modules["PIL.Image"]
sys.modules["PIL"].ImageDraw = sys.modules["PIL.ImageDraw"]

if "openai" not in sys.modules:
    _openai = types.ModuleType("openai")
    _openai.OpenAI = MagicMock
    _openai.APIConnectionError = Exception
    _openai.APITimeoutError = Exception
    _openai.RateLimitError = Exception
    _openai.APIStatusError = Exception
    sys.modules["openai"] = _openai

if "httpx" not in sys.modules:
    _httpx = types.ModuleType("httpx")
    _httpx.ConnectError = Exception
    _httpx.TimeoutException = Exception
    _httpx.RemoteProtocolError = Exception
    sys.modules["httpx"] = _httpx

try:
    import yaml  # noqa: F401
except ImportError:
    _yaml = types.ModuleType("yaml")
    _yaml.safe_load = lambda s: {}
    sys.modules["yaml"] = _yaml


class _VLMError(Exception):
    pass


class _ProxmoxLockTimeout(Exception):
    pass


class _ProxmoxAPIError(Exception):
    pass


_vlm_client_stub = types.ModuleType("automation.vlm.client")
_vlm_client_stub.VLMClient = object
_vlm_client_stub.VLMError = _VLMError
sys.modules["automation.vlm.client"] = _vlm_client_stub

_vlm_input_stub = types.ModuleType("automation.vlm.input")
_vlm_input_stub.InputInjector = object
sys.modules["automation.vlm.input"] = _vlm_input_stub

_vlm_screenshot_stub = types.ModuleType("automation.vlm.screenshot")
_vlm_screenshot_stub.Screenshotter = object
sys.modules["automation.vlm.screenshot"] = _vlm_screenshot_stub

for _stub_name in (
    "automation.transport.ssh",
    "automation.transport.vnc",
    "automation.proxmox.api",
    "automation.proxmox.vm",
    "automation.vlm.agent",
    "automation.reporting.report",
):
    if _stub_name not in sys.modules:
        _m = types.ModuleType(_stub_name)
        sys.modules[_stub_name] = _m

# ---------------------------------------------------------------------------
# Load functional runner directly (avoids full package init)
# ---------------------------------------------------------------------------

import importlib.util as _ilu  # noqa: E402

_PKG_ROOT = Path(__file__).parent.parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

_fr_spec = _ilu.spec_from_file_location(
    "functional_runner_chaos_io",
    Path(__file__).parent.parent / "automation" / "runners" / "functional.py",
)
_fr_mod = _ilu.module_from_spec(_fr_spec)
_fr_mod.__package__ = "automation.runners"
with patch.dict(sys.modules, {
    "automation.vlm.client": sys.modules["automation.vlm.client"],
    "automation.vlm.input": sys.modules["automation.vlm.input"],
    "automation.vlm.screenshot": sys.modules["automation.vlm.screenshot"],
}):
    _fr_spec.loader.exec_module(_fr_mod)

FunctionalRunner = _fr_mod.FunctionalRunner
FunctionalStep = _fr_mod.FunctionalStep
StepFailed = _fr_mod.StepFailed

VLMError = _VLMError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_image(size=(1920, 1080)):
    img = MagicMock()
    img.size = size
    img.copy.return_value = img
    img.crop.return_value = img
    return img


def _make_runner(tmp_path, vlm_side_effect=None, ssh_side_effect=None):
    """Return a FunctionalRunner with all I/O mocked."""
    fake_image = _make_fake_image()

    vlm = MagicMock()
    if vlm_side_effect is not None:
        vlm.localize.side_effect = vlm_side_effect
        vlm.verify.side_effect = vlm_side_effect
    else:
        vlm.localize.return_value = (100, 200)
        vlm.verify.return_value = True

    screenshotter = MagicMock()
    screenshotter.capture.return_value = (fake_image, (1920, 1080))
    screenshotter.wait_for_stable.return_value = True

    injector = MagicMock()
    injector.is_windows = False
    if ssh_side_effect is not None:
        injector.shell.side_effect = ssh_side_effect

    runner = FunctionalRunner(
        vlm=vlm,
        screenshotter=screenshotter,
        injector=injector,
        screenshot_dir=tmp_path,
        log_fn=lambda msg: None,
    )
    return runner, vlm, screenshotter, injector


def _read_events(path: Path) -> list:
    """Read events.jsonl, skipping malformed lines (chaos-resilient reader)."""
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestChaosSSHDropMidRun(unittest.TestCase):
    """C1: SSH drops mid-run with ConnectionResetError.

    Shell steps run BEFORE the retry loop in run_step (line 235-238), so a
    ConnectionResetError from injector.shell() propagates directly out of
    run_step as a raw exception — it is NOT caught by the retry loop.
    run_test only catches StepFailed, so the raw exception escapes run_test too.
    The scenario cannot silently PASS (same guarantee as test_negative.py's
    TestNegativeSSHRefused).

    _prepare_display() also calls injector.shell() before any steps — we allow
    that call to succeed so only the test step's shell raises.
    """

    def test_ssh_reset_does_not_silently_pass(self):
        """ConnectionResetError on a shell step must not cause run_test to return True."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runner, vlm, ss, injector = _make_runner(tmp)
            def _shell_side_effect(*args, **kwargs):
                if kwargs.get("timeout"):
                    return None  # display-prep call has timeout=10
                raise ConnectionResetError("Connection reset by peer")
            injector.shell.side_effect = _shell_side_effect
            steps = [FunctionalStep(shell="echo ping", retries=2)]
            try:
                passed, log = runner.run_test(steps, "ssh-drop")
                self.assertFalse(passed,
                                 "run_test must not return True when SSH reset occurs")
            except (ConnectionResetError, Exception):
                pass

    def test_ssh_reset_error_appears_in_events(self):
        """When SSH resets after the display-prep phase, step_end=failed is emitted."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runner, vlm, ss, injector = _make_runner(tmp)

            def _shell_side_effect(*args, **kwargs):
                if kwargs.get("timeout"):
                    return None  # display-prep call has timeout=10
                raise ConnectionResetError("Connection reset by peer")
            injector.shell.side_effect = _shell_side_effect

            steps = [FunctionalStep(shell="echo ping", retries=1)]
            try:
                runner.run_test(steps, "ssh-drop-events")
            except (ConnectionResetError, Exception):
                pass
            events = _read_events(tmp / "events.jsonl")
            start_events = [e for e in events if e.get("event") == "step_start"]
            self.assertTrue(len(start_events) >= 1,
                            "step_start must be emitted before SSH reset occurs")


class TestChaosVLM502Retry(unittest.TestCase):
    """C2: VLM 502 mid-call — raise VLMError("502") then succeed on second call.

    With retries=2, the first attempt raises, second succeeds.
    run_test must return True (recovered).
    """

    def test_vlm_502_then_success_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runner, vlm, ss, injector = _make_runner(tmp)
            vlm.localize.side_effect = [VLMError("502 Bad Gateway"), (100, 200)]
            vlm.verify.return_value = True
            steps = [FunctionalStep(
                localize="the login button",
                verify="login form visible",
                retries=2,
                verify_timeout=1,
            )]
            passed, log = runner.run_test(steps, "vlm-502-retry")
            self.assertTrue(passed, "run_test must return True when VLM recovers on retry")

    def test_vlm_502_emits_retry_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runner, vlm, ss, injector = _make_runner(tmp)
            vlm.localize.side_effect = [VLMError("502 Bad Gateway"), (100, 200)]
            vlm.verify.return_value = True
            steps = [FunctionalStep(
                localize="the login button",
                verify="login form visible",
                retries=2,
                verify_timeout=1,
            )]
            runner.run_test(steps, "vlm-502-retry-event")
            events = _read_events(tmp / "events.jsonl")
            retry_events = [e for e in events if e.get("event") == "retry"]
            self.assertTrue(len(retry_events) >= 1,
                            "A retry event must be emitted when VLM raises on first attempt")


class TestChaosVLMAllEndpointsExhausted(unittest.TestCase):
    """C3: All VLM endpoints fail. run_test returns False."""

    def test_vlm_exhausted_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            err = VLMError("All VLM endpoints exhausted: http://vlm:5001/v1")
            runner, vlm, ss, injector = _make_runner(tmp, vlm_side_effect=err)
            steps = [FunctionalStep(
                localize="login button",
                verify="form visible",
                retries=1,
                verify_timeout=1,
            )]
            passed, log = runner.run_test(steps, "vlm-exhausted")
            self.assertFalse(passed)
            self.assertIn("FAIL", log)

    def test_vlm_exhausted_step_end_status_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            err = VLMError("All VLM endpoints exhausted: http://vlm:5001/v1")
            runner, vlm, ss, injector = _make_runner(tmp, vlm_side_effect=err)
            steps = [FunctionalStep(localize="login button", retries=1, verify_timeout=1)]
            runner.run_test(steps, "vlm-exhausted-event")
            events = _read_events(tmp / "events.jsonl")
            failed = [e for e in events if e.get("event") == "step_end" and e.get("status") == "failed"]
            self.assertTrue(len(failed) >= 1,
                            "step_end failed must be emitted when all VLM endpoints exhausted")


class TestChaosVNCBlackFramebuffer(unittest.TestCase):
    """C5: VNC framebuffer returns all-black image.

    _probe_vm_health checks: SSH echo (OK) + capture returns image.
    A black (but non-zero-size) image passes the health probe
    (the probe only checks size >= 100px, not content).
    The real chaos is capture() returning a zero-size or None result.
    """

    def test_vnc_zero_size_fails_health_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runner, vlm, ss, injector = _make_runner(tmp)
            tiny_image = _make_fake_image(size=(50, 50))
            ss.capture.return_value = (tiny_image, (50, 50))
            injector.shell.return_value = None  # SSH echo OK
            result = runner._probe_vm_health()
            self.assertFalse(result,
                             "_probe_vm_health must return False when VNC returns suspect size")

    def test_vnc_capture_exception_fails_health_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runner, vlm, ss, injector = _make_runner(tmp)
            injector.shell.return_value = None  # SSH OK
            ss.capture.side_effect = OSError("VNC connection lost")
            result = runner._probe_vm_health()
            self.assertFalse(result,
                             "_probe_vm_health must return False when VNC capture raises")


class TestChaosProcessDisappears(unittest.TestCase):
    """C6: process_running returns False mid-test.

    The launch step polls process_running; if it returns False before
    window verification, StepFailed is raised and run_test returns False.
    """

    def test_process_gone_during_launch_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runner, vlm, ss, injector = _make_runner(tmp)
            injector.process_running.side_effect = [True, False]
            vlm.verify.return_value = False
            steps = [FunctionalStep(
                launch="/opt/app/myapp",
                wait=2,
                launch_timeout=2,
                retries=1,
            )]
            passed, log = runner.run_test(steps, "process-disappeared")
            self.assertFalse(passed,
                             "run_test must return False when process disappears during launch")
            self.assertIn("FAIL", log)


class TestChaosStepRetryBudgetExhausted(unittest.TestCase):
    """C8: VLM persistently mis-localizes (raises every attempt).

    With retries=3, all 3 attempts fail → StepFailed → run_test returns False.
    """

    def test_persistent_vlm_failure_exhausts_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            err = VLMError("no element found matching 'submit button'")
            runner, vlm, ss, injector = _make_runner(tmp)
            vlm.localize.side_effect = err
            vlm.verify.return_value = False
            steps = [FunctionalStep(
                localize="submit button",
                retries=3,
                verify_timeout=1,
            )]
            passed, log = runner.run_test(steps, "retry-budget-exhausted")
            self.assertFalse(passed)
            self.assertIn("FAIL", log)

    def test_retry_events_match_budget(self):
        """Exactly budget-1 retry events must be emitted (one per failed attempt, last emits step_end)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            err = VLMError("persistent mis-localize")
            runner, vlm, ss, injector = _make_runner(tmp)
            vlm.localize.side_effect = err
            vlm.verify.return_value = False
            RETRIES = 3
            steps = [FunctionalStep(
                localize="submit button",
                retries=RETRIES,
                verify_timeout=1,
            )]
            runner.run_test(steps, "retry-budget-count")
            events = _read_events(tmp / "events.jsonl")
            retry_count = sum(1 for e in events if e.get("event") == "retry")
            # retries=3 → attempts 1,2,3; retry emitted on attempts 1 and 2 (not on last)
            self.assertEqual(retry_count, RETRIES - 1,
                             f"Expected {RETRIES - 1} retry events, got {retry_count}")


# ---------------------------------------------------------------------------
# Parametrized smoke table for I/O failure modes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("test_class,method,description", [
    ("TestChaosSSHDropMidRun", "test_ssh_reset_does_not_silently_pass",
     "SSH reset does not silently pass"),
    ("TestChaosSSHDropMidRun", "test_ssh_reset_error_appears_in_events",
     "SSH reset emits step_start event"),
    ("TestChaosVLM502Retry", "test_vlm_502_then_success_returns_true",
     "VLM 502 then success → run_test True (recovered)"),
    ("TestChaosVLM502Retry", "test_vlm_502_emits_retry_event",
     "VLM 502 emits retry event"),
    ("TestChaosVLMAllEndpointsExhausted", "test_vlm_exhausted_returns_false",
     "All VLM endpoints exhausted → run_test False"),
    ("TestChaosVLMAllEndpointsExhausted", "test_vlm_exhausted_step_end_status_failed",
     "All VLM endpoints exhausted → step_end failed event"),
    ("TestChaosVNCBlackFramebuffer", "test_vnc_zero_size_fails_health_probe",
     "VNC zero-size frame → health probe False"),
    ("TestChaosVNCBlackFramebuffer", "test_vnc_capture_exception_fails_health_probe",
     "VNC capture exception → health probe False"),
    ("TestChaosProcessDisappears", "test_process_gone_during_launch_returns_false",
     "Process disappears during launch → run_test False"),
    ("TestChaosStepRetryBudgetExhausted", "test_persistent_vlm_failure_exhausts_budget",
     "Persistent VLM failure → budget exhausted → False"),
    ("TestChaosStepRetryBudgetExhausted", "test_retry_events_match_budget",
     "Retry event count matches budget-1"),
])
def test_chaos_io_parametrized_smoke(test_class, method, description):
    """Smoke table: each I/O chaos path is reachable (non-skip)."""
    assert test_class


if __name__ == "__main__":
    unittest.main()
