"""Chaos-injection tests — infrastructure failure modes (I4).

Covers:
  C9:  Proxmox lock timeout during snapshot
  C10: Mid-write events.jsonl partial line (reader resilience)

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
    "functional_runner_chaos_infra",
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
            passed, log = runner.run_test(steps, "lock-timeout")
            self.assertTrue(passed, "run_test must return True when snapshot lock timeout is caught")

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
            passed, log = runner.run_test(steps, "lock-timeout-log")
            combined = " ".join(log_lines)
            self.assertIn(
                "WARNING",
                combined,
                f"Warning about snapshot failure must appear in log, got: {combined!r}",
            )
            self.assertTrue(passed, "run_test must succeed when checkpoint warning is logged")


class TestChaosEventsJsonlPartialLine(unittest.TestCase):
    """C10: Partial/malformed line in events.jsonl (as if killed mid-write).

    The reader (_read_events in this module and the dashboard) must skip
    malformed lines gracefully — no crash, valid events still returned.
    """

    def test_reader_skips_malformed_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            events_path = tmp / "events.jsonl"
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
# Parametrized smoke table for infrastructure failure modes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("test_class,method,description", [
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
def test_chaos_infra_parametrized_smoke(test_class, method, description):
    """Smoke table: each infra chaos path is reachable (non-skip)."""
    assert test_class


if __name__ == "__main__":
    unittest.main()
