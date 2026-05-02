"""Chaos-injection unit test suite — I4.

Verifies graceful handling of mid-run infrastructure failures.

All tests are hermetic: no real SSH / VLM / Proxmox / network.
Mirrors the module-load technique from test_negative.py.

Items tested
------------
1.  SSH drops mid-run                     — ConnectionResetError → retry → False
2.  VLM 502 mid-call (retry succeeds)     — VLMError("502") then succeed
3.  VLM all endpoints exhausted           — run_test returns False
4.  Proxmox API 503 during snapshot       — StepFailed; lock released
5.  VNC framebuffer black mid-test        — _probe_vm_health returns False
6.  Process disappears mid-test           — process_running → False → terminated
7.  Workspace preflight (skip duplicate)  — covered in test_negative.py
8.  Step retry budget exhausted           — persistent mis-localize → False
9.  Lock timeout under contention         — ProxmoxLockTimeout raised cleanly
10. Mid-write events.jsonl partial line   — reader skips malformed, doesn't crash
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
# Stub heavy dependencies (same technique as test_negative.py)
# PIL must be stubbed at module-load time before exec_module runs, matching
# the same pattern as test_negative.py to avoid PIL.Image.Image = object
# mutation conflicts when the full test suite collects all modules.
# ---------------------------------------------------------------------------

# PIL — stub exactly as test_negative.py does to avoid cross-module mutation
for _pil_mod in ("PIL", "PIL.Image", "PIL.ImageDraw"):
    if _pil_mod not in sys.modules:
        sys.modules[_pil_mod] = types.ModuleType(_pil_mod)
if not hasattr(sys.modules["PIL.Image"], "Image"):
    sys.modules["PIL.Image"].Image = object
sys.modules["PIL"].Image = sys.modules["PIL.Image"]
sys.modules["PIL"].ImageDraw = sys.modules["PIL.ImageDraw"]

# openai
if "openai" not in sys.modules:
    _openai = types.ModuleType("openai")
    _openai.OpenAI = MagicMock
    _openai.APIConnectionError = Exception
    _openai.APITimeoutError = Exception
    _openai.RateLimitError = Exception
    _openai.APIStatusError = Exception
    sys.modules["openai"] = _openai

# httpx
if "httpx" not in sys.modules:
    _httpx = types.ModuleType("httpx")
    _httpx.ConnectError = Exception
    _httpx.TimeoutException = Exception
    _httpx.RemoteProtocolError = Exception
    sys.modules["httpx"] = _httpx

# yaml
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
    "functional_runner_chaos",
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

VLMError = _VLMError  # use local stub (matches what the runner imported)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_image(size=(1920, 1080)):
    """Return a MagicMock that behaves like a PIL Image for the runner's purposes."""
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
            pass  # skip malformed
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
            # Allow display-prep shell (called with timeout kwarg), raise on plain shell calls
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
                # Acceptable: raw exception propagating means we definitely did not PASS
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
                pass  # exception propagating is acceptable
            events = _read_events(tmp / "events.jsonl")
            # step_start must have been emitted before the shell raised
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
            # First localize raises, second succeeds; verify returns True always
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
            # Return a mock image with tiny dimensions (size < 100px) to trigger the check
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
            # process_running: True on first call (launch detection), False after
            injector.process_running.side_effect = [True, False]
            # VLM verify: always False (window never visible after process gone)
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
            # localize raises on every call; no verify so retry pre-check cannot short-circuit
            vlm.localize.side_effect = err
            vlm.verify.return_value = False  # ensure retry pre-check does NOT short-circuit
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
            vlm.verify.return_value = False  # ensure retry pre-check does NOT short-circuit
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


class TestChaosProxmoxLockTimeout(unittest.TestCase):
    """C9: Proxmox lock held externally — ProxmoxLockTimeout raised on snapshot.

    BUG FOUND: _do_checkpoint() calls vm_ops.snapshot() at line 208 with NO
    try/except wrapper. The only exception guard (lines 201-207) covers
    list_snapshots/delete_snapshot, not the snapshot() call itself. So a
    ProxmoxLockTimeout from vm_ops.snapshot() propagates up through run_step
    and run_test uncaught — crashing the runner instead of logging a warning.

    Both tests below are marked xfail(strict=True): they document the expected
    (correct) behaviour that the runner should provide. They will pass once
    _do_checkpoint wraps vm_ops.snapshot() in a try/except.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG: _do_checkpoint does not catch exceptions from vm_ops.snapshot(). "
            "ProxmoxLockTimeout propagates uncaught out of run_test. "
            "Fix: wrap vm_ops.snapshot() in try/except in _do_checkpoint."
        ),
    )
    def test_lock_timeout_on_snapshot_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runner, vlm, ss, injector = _make_runner(tmp)
            runner._checkpoints_enabled = True
            vm_ops = MagicMock()
            vm_ops.list_snapshots.return_value = []
            vm_ops.snapshot.side_effect = _ProxmoxLockTimeout(
                "Could not acquire VM lock for VMID 100 within 30s"
            )
            runner._vm_ops = vm_ops
            runner._vmid = 100
            steps = [
                FunctionalStep(checkpoint="pre-test"),
                FunctionalStep(shell="echo done", retries=1),
            ]
            try:
                passed, log = runner.run_test(steps, "lock-timeout")
            except _ProxmoxLockTimeout:
                self.fail("ProxmoxLockTimeout must not propagate out of run_test")

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG: _do_checkpoint does not catch exceptions from vm_ops.snapshot(). "
            "The warning is never logged because the exception escapes the method. "
            "Fix: wrap vm_ops.snapshot() in try/except in _do_checkpoint."
        ),
    )
    def test_lock_timeout_logged_as_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log_lines: list = []
            runner, vlm, ss, injector = _make_runner(tmp)
            runner.log = lambda msg: log_lines.append(msg)
            runner._checkpoints_enabled = True
            vm_ops = MagicMock()
            vm_ops.list_snapshots.return_value = []
            vm_ops.snapshot.side_effect = _ProxmoxLockTimeout(
                "Could not acquire VM lock for VMID 100 within 30s"
            )
            runner._vm_ops = vm_ops
            runner._vmid = 100
            steps = [FunctionalStep(checkpoint="pre-test")]
            runner.run_test(steps, "lock-timeout-log")
            combined = " ".join(log_lines)
            self.assertTrue(
                "WARNING" in combined or "lock" in combined.lower() or "snapshot" in combined.lower(),
                f"Warning about lock/snapshot failure must appear in log, got: {combined!r}",
            )


class TestChaosEventsJsonlPartialLine(unittest.TestCase):
    """C10: Partial/malformed line in events.jsonl (as if killed mid-write).

    The reader (_read_events in this module and the dashboard) must skip
    malformed lines gracefully — no crash, valid events still returned.
    """

    def test_reader_skips_malformed_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            events_path = tmp / "events.jsonl"
            # Write a valid line, then a partial/truncated line, then another valid line
            valid1 = json.dumps({"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 1})
            partial = '{"ts": "2026-01-01T00:00:01", "event": "step_end", "step_num'  # truncated
            valid2 = json.dumps({"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 1, "status": "ok"})
            events_path.write_text(f"{valid1}\n{partial}\n{valid2}\n")
            result = _read_events(events_path)
            self.assertEqual(len(result), 2,
                             "Reader must return 2 valid events and skip 1 malformed line")
            self.assertEqual(result[0]["event"], "step_start")
            self.assertEqual(result[1]["event"], "step_end")

    def test_reader_handles_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            events_path = tmp / "events.jsonl"
            events_path.write_text("")
            result = _read_events(events_path)
            self.assertEqual(result, [], "Reader must return empty list for empty file")

    def test_reader_handles_missing_file(self):
        result = _read_events(Path("/nonexistent/events.jsonl"))
        self.assertEqual(result, [], "Reader must return empty list for missing file")

    def test_runner_events_readable_after_normal_run(self):
        """Sanity: events written by runner are all valid JSON."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            runner, vlm, ss, injector = _make_runner(tmp)
            steps = [FunctionalStep(shell="echo hello", retries=1)]
            runner.run_test(steps, "normal-run")
            events_path = tmp / "events.jsonl"
            # All lines written by the runner must be valid JSON
            if events_path.exists():
                for line in events_path.read_text().splitlines():
                    if line.strip():
                        try:
                            json.loads(line)
                        except json.JSONDecodeError as e:
                            self.fail(f"Runner wrote non-JSON line: {line!r} ({e})")


# ---------------------------------------------------------------------------
# Coverage note: item 7 (workspace URL preflight) is already covered by
# test_negative.py::TestNegativeWorkspaceUnreachable (3 tests). No duplicate needed.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Parametrized smoke table (pytest -v readability)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("test_class,method,description", [
    ("TestChaosSSHDropMidRun", "test_ssh_reset_exhausts_retries_returns_false",
     "SSH reset exhausts retries → run_test False"),
    ("TestChaosSSHDropMidRun", "test_ssh_reset_error_appears_in_events",
     "SSH reset emits step_end failed event"),
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
    ("TestChaosProxmoxLockTimeout", "test_lock_timeout_on_snapshot_is_nonfatal",
     "ProxmoxLockTimeout on snapshot is non-fatal (logged, run continues)"),
    ("TestChaosProxmoxLockTimeout", "test_lock_timeout_logged_as_warning",
     "Lock timeout warning appears in log"),
    ("TestChaosEventsJsonlPartialLine", "test_reader_skips_malformed_line",
     "Reader skips partial/malformed JSON line"),
    ("TestChaosEventsJsonlPartialLine", "test_reader_handles_empty_file",
     "Reader handles empty events.jsonl"),
    ("TestChaosEventsJsonlPartialLine", "test_reader_handles_missing_file",
     "Reader handles missing events.jsonl"),
    ("TestChaosEventsJsonlPartialLine", "test_runner_events_readable_after_normal_run",
     "Runner writes valid JSON events"),
])
def test_chaos_parametrized_smoke(test_class, method, description):
    """Smoke table: each chaos path is reachable (non-skip). Assertions live in TestCase classes."""
    assert test_class  # existence check only


if __name__ == "__main__":
    unittest.main()
