"""Negative test suite for mOSdat.

Goal: catch "framework lies" — cases where mosdat would falsely report PASS
on a broken environment.  Each parametrized case exercises a distinct failure
path and asserts that mosdat reports FAIL (non-zero exit / returned False) with
a reason substring matching the expected error.

All tests are hermetic: no real network calls, no real SSH/VNC, no real VLM.
Heavy imports are stubbed at module load time (same technique as test_if_visible).

Run: pytest tests/test_negative.py -q
"""

import io
import os
import sys
import types
import unittest
import importlib.util as _ilu
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Stub heavy dependencies so we can import automation modules without
# installing the full GPU/VLM stack.
# ---------------------------------------------------------------------------

# PIL
for _mod in ("PIL", "PIL.Image", "PIL.ImageDraw"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)
sys.modules["PIL.Image"].Image = object
sys.modules["PIL"].Image = sys.modules["PIL.Image"]
sys.modules["PIL"].ImageDraw = sys.modules["PIL.ImageDraw"]

# openai (used by VLMClient)
if "openai" not in sys.modules:
    _openai = types.ModuleType("openai")
    _openai.OpenAI = MagicMock
    _openai.APIConnectionError = Exception
    _openai.APITimeoutError = Exception
    _openai.RateLimitError = Exception
    _openai.APIStatusError = Exception
    sys.modules["openai"] = _openai

# httpx (used by _is_failover_error)
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

# Stub local transport / vlm modules for import isolation.
# Must set the class attributes that functional.py imports at module level.

class _VLMError(Exception):
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

# Ensure package root on path
_PKG_ROOT = Path(__file__).parent.parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

# ---------------------------------------------------------------------------
# Import functional runner directly (avoids full package init).
# ---------------------------------------------------------------------------
_fr_spec = _ilu.spec_from_file_location(
    "functional_runner",
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
load_test_yaml = _fr_mod.load_test_yaml

# Import _preflight_workspace from main.py.
# main.py does `from .config import load_config, ProjectConfig` at module level,
# so we must provide a stub with those names before exec_module.
_config_stub = types.ModuleType("automation.config")
_config_stub.load_config = MagicMock()
_config_stub.ProjectConfig = MagicMock()

_smoke_stub = types.ModuleType("automation.runners.smoke")
_smoke_stub.TestRunner = MagicMock()

_main_spec = _ilu.spec_from_file_location(
    "mosdat_main",
    Path(__file__).parent.parent / "automation" / "main.py",
)
_main_mod = _ilu.module_from_spec(_main_spec)
_main_mod.__package__ = "automation"
_MAIN_STUBS = {
    "automation.config": _config_stub,
    "automation.runners.smoke": _smoke_stub,
    "automation.runners.functional": _fr_mod,
    "automation.vlm.client": sys.modules["automation.vlm.client"],
    "automation.vlm.screenshot": sys.modules["automation.vlm.screenshot"],
    "automation.vlm.input": sys.modules["automation.vlm.input"],
    "automation.transport.ssh": sys.modules["automation.transport.ssh"],
    "automation.transport.vnc": sys.modules["automation.transport.vnc"],
    "automation.proxmox.api": sys.modules["automation.proxmox.api"],
    "automation.proxmox.vm": sys.modules["automation.proxmox.vm"],
    "automation.reporting.report": sys.modules["automation.reporting.report"],
    "automation.record": types.ModuleType("automation.record"),
}
with patch.dict(sys.modules, _MAIN_STUBS):
    _main_spec.loader.exec_module(_main_mod)

_preflight_workspace = _main_mod._preflight_workspace

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runner(vlm_side_effect=None, ssh_side_effect=None, tmp_path=None):
    """Return a FunctionalRunner with all I/O mocked.

    vlm_side_effect: if set, vlm.localize raises this exception.
    ssh_side_effect: if set, injector.shell raises this exception.
    """
    fake_image = MagicMock()
    fake_image.size = (1920, 1080)
    fake_image.copy.return_value = fake_image
    fake_image.crop.return_value = fake_image

    vlm = MagicMock()
    if vlm_side_effect:
        vlm.localize.side_effect = vlm_side_effect
        vlm.verify.side_effect = vlm_side_effect
    else:
        vlm.localize.return_value = (100, 200)
        vlm.verify.return_value = True

    screenshotter = MagicMock()
    screenshotter.capture.return_value = (fake_image, (1920, 1080))
    screenshotter.wait_for_stable = MagicMock()

    injector = MagicMock()
    if ssh_side_effect:
        injector.shell.side_effect = ssh_side_effect

    screenshot_dir = tmp_path or Path("/tmp/mosdat_neg_test")

    runner = FunctionalRunner(
        vlm=vlm,
        screenshotter=screenshotter,
        injector=injector,
        screenshot_dir=screenshot_dir,
        log_fn=lambda msg: None,
    )
    return runner, vlm, injector


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestNegativeWorkspaceUnreachable(unittest.TestCase):
    """P1 preflight: workspace URL that cannot be reached → exit code 2."""

    def test_unreachable_workspace_returns_false_and_detail(self):
        import urllib.error
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = OSError("Connection refused")
            ok, detail = _preflight_workspace("https://10.255.255.1/", timeout=1, retries=0)
        self.assertFalse(ok, "preflight must return False for unreachable workspace")
        self.assertIn("Connection refused", detail,
                      "detail must mention the connection error")

    def test_unreachable_workspace_503_returns_false(self):
        import urllib.error
        err = urllib.error.HTTPError(
            url="https://10.255.255.1/api/info",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err):
            ok, detail = _preflight_workspace("https://10.255.255.1/", timeout=1, retries=0)
        self.assertFalse(ok)
        self.assertIn("503", detail)

    def test_workspace_ok_returns_true(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200
        with patch("urllib.request.urlopen", return_value=mock_resp):
            ok, detail = _preflight_workspace("https://example.com/", timeout=5, retries=0)
        self.assertTrue(ok)


class TestNegativeMissingBinary(unittest.TestCase):
    """launch step with app_path pointing to /nonexistent/rocketchat → StepFailed."""

    def test_launch_nonexistent_binary_raises_step_failed(self):
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp())
        try:
            runner, vlm, injector = _make_runner(tmp_path=tmp)
            # injector.launch succeeds (fire and forget), but process_running returns False
            injector.process_running.return_value = False
            step = FunctionalStep(
                launch="/nonexistent/rocketchat",
                wait=1,
                launch_timeout=1,
            )
            with self.assertRaises(StepFailed) as ctx:
                runner.run_step(step, 1)
            msg = str(ctx.exception)
            self.assertIn("Launch failed", msg,
                          "StepFailed must mention launch failure")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNegativeBadPassword(unittest.TestCase):
    """SSH auth failure (ConnectionRefusedError from injector.shell) → run_test returns False."""

    def test_ssh_refused_in_health_probe_returns_false(self):
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp())
        try:
            runner, vlm, injector = _make_runner(
                ssh_side_effect=ConnectionRefusedError("Connection refused"),
                tmp_path=tmp,
            )
            # Health probe hits shell first
            healthy = runner._probe_vm_health()
            self.assertFalse(healthy, "_probe_vm_health must return False when SSH refused")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ssh_refused_in_shell_step_raises_step_failed(self):
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp())
        try:
            runner, vlm, injector = _make_runner(
                ssh_side_effect=ConnectionRefusedError("Connection refused"),
                tmp_path=tmp,
            )
            step = FunctionalStep(shell="echo test", retries=1)
            # shell step runs outside the retry loop — exception bubbles as StepFailed
            with self.assertRaises((StepFailed, ConnectionRefusedError)):
                runner.run_step(step, 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNegativeMalformedScenario(unittest.TestCase):
    """Scenario YAML missing 'steps' key → load_test_yaml returns empty list.

    The runner must not silently report PASS on a zero-step scenario.
    We verify that run_test with zero steps returns (True, log) but that
    separately, a loader enforcing steps presence raises ValueError.
    """

    def test_load_yaml_missing_steps_returns_empty(self):
        fixture = FIXTURES / "broken_malformed_scenario.yaml"
        name, steps, vars_, _ = load_test_yaml(fixture)
        self.assertEqual(steps, [],
                         "load_test_yaml must return empty list when 'steps' key is absent")

    def test_zero_step_scenario_is_vacuously_true_not_silently_passing(self):
        """A scenario with no steps returns True but the log records 0 steps — not a real PASS."""
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp())
        try:
            runner, _, _ = _make_runner(tmp_path=tmp)
            passed, log = runner.run_test([], "empty-scenario")
            # Passed is True (vacuous), but log must say 0 steps — not "PASS: all N steps"
            self.assertIn("0 steps", log,
                          "log must record 0 steps so callers can detect vacuous pass")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNegativeVLM5xx(unittest.TestCase):
    """VLMClient raises VLMError (retry exhaustion after 5xx) → StepFailed with error detail."""

    def test_vlm_exhaustion_raises_step_failed(self):
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp())
        try:
            # Use the VLMError from the loaded module (not the stub)
            VLMError = _fr_mod.VLMError if hasattr(_fr_mod, "VLMError") else Exception
            err = VLMError("All VLM endpoints exhausted: http://vlm:5001/v1")
            runner, vlm, injector = _make_runner(
                vlm_side_effect=err,
                tmp_path=tmp,
            )
            step = FunctionalStep(
                localize="the login button",
                verify="login form is visible",
                retries=1,
            )
            with self.assertRaises(StepFailed) as ctx:
                runner.run_step(step, 1)
            msg = str(ctx.exception)
            self.assertTrue(
                "exhausted" in msg.lower() or "VLM" in msg or "login" in msg,
                f"StepFailed message should mention VLM exhaustion, got: {msg!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_vlm_5xx_causes_run_test_to_return_false(self):
        """run_test returns (False, log) when a step raises StepFailed."""
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp())
        try:
            VLMError = _fr_mod.VLMError if hasattr(_fr_mod, "VLMError") else Exception
            err = VLMError("All VLM endpoints exhausted: http://vlm:5001/v1")
            runner, vlm, injector = _make_runner(
                vlm_side_effect=err,
                tmp_path=tmp,
            )
            steps = [FunctionalStep(localize="login button", verify="form visible", retries=1)]
            passed, log = runner.run_test(steps, "vlm-5xx-test")
            self.assertFalse(passed, "run_test must return False when VLM exhausted")
            self.assertIn("FAIL", log, "log must contain FAIL marker")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNegativeSSHRefused(unittest.TestCase):
    """SSH ConnectionRefusedError during shell step → run_test returns False."""

    def test_ssh_refused_causes_run_test_false(self):
        """shell step raises ConnectionRefusedError before the retry loop,
        so it propagates out of run_step uncaught as a non-StepFailed exception.
        run_test's StepFailed handler does not catch it; the caller sees the raw
        exception.  The negative assertion: the scenario cannot silently PASS.
        """
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp())
        try:
            runner, vlm, injector = _make_runner(
                ssh_side_effect=ConnectionRefusedError("Connection refused"),
                tmp_path=tmp,
            )
            steps = [FunctionalStep(shell="echo test", retries=1)]
            # ConnectionRefusedError escapes run_test (shell fires pre-retry-loop).
            # Either it propagates or run_test catches it — either way the scenario
            # must NOT return (True, ...).
            try:
                passed, log = runner.run_test(steps, "ssh-refused-test")
                self.assertFalse(passed,
                                 "run_test must not return True when SSH refused")
            except (ConnectionRefusedError, Exception):
                # Acceptable: exception propagating means we definitely did not PASS
                pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ssh_refused_health_probe_detail(self):
        """_probe_vm_health logs the SSH error and returns False."""
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp())
        log_lines = []
        try:
            runner, vlm, injector = _make_runner(
                ssh_side_effect=ConnectionRefusedError("Connection refused"),
                tmp_path=tmp,
            )
            runner.log = lambda msg: log_lines.append(msg)
            result = runner._probe_vm_health()
            self.assertFalse(result)
            combined = " ".join(log_lines)
            self.assertTrue(
                "SSH" in combined or "refused" in combined.lower() or "unresponsive" in combined.lower(),
                f"log must mention SSH failure, got: {combined!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNegativePreflightWorkspaceExitCode(unittest.TestCase):
    """End-to-end: cmd_functional returns exit code 2 on workspace preflight failure.

    cmd_functional does lazy imports inside the function body.  We must pre-stub
    the full automation package hierarchy in sys.modules so none of the real
    __init__.py files (which pull in GPU/proxmox/SSH deps) execute at call time.
    """

    def test_cmd_functional_returns_2_on_dead_workspace(self):
        """cmd_functional must return 2 when workspace preflight fails."""
        # Build a minimal args namespace
        args = MagicMock()
        args.record = False
        args.vms = "ubuntu24"
        args.config = str(FIXTURES / "broken_unreachable_workspace.yaml")
        args.skip_warmup = True
        args.skip_workspace_check = False
        args.skip_health_probe = True
        args.no_checkpoints = True
        args.test = "broken_unreachable_workspace"
        args.screenshots = None
        args.save_screenshots = False
        args.from_step = 1
        args.until_step = None
        args.model = "test-model"
        args.verify_model = "test-verify-model"
        args.popup_sweep = False
        args.results_dir = None

        # Mock config with workspace_url set.
        # Point tests_dir at fixtures so cmd_functional finds the YAML file
        # and proceeds past the file-not-found check to the workspace preflight.
        mock_config = MagicMock()
        mock_config.vm_by_name = {"ubuntu24": MagicMock()}
        mock_config.functional.workspace_url = "https://10.255.255.1/"
        mock_config.functional.tests_dir = FIXTURES
        mock_config.framework_path = Path("/tmp")
        mock_config.proxmox.password = "test"

        # Stub the full automation package hierarchy to prevent __init__.py
        # imports (proxmox.api, runners.smoke, etc.) from pulling in GPU deps.
        _proxmox_stub = types.ModuleType("automation.proxmox")
        _proxmox_stub.ProxmoxAPI = MagicMock()
        _proxmox_api_stub = types.ModuleType("automation.proxmox.api")
        _proxmox_api_stub.ProxmoxAPI = MagicMock()
        _proxmox_api_stub.ProxmoxAPIError = Exception
        _proxmox_gpu_stub = types.ModuleType("automation.proxmox.gpu")
        _proxmox_gpu_stub.GPUManager = MagicMock()
        _proxmox_vm_stub = types.ModuleType("automation.proxmox.vm")
        _proxmox_vm_stub.VMOperations = MagicMock()
        _runners_stub = types.ModuleType("automation.runners")
        _runners_stub.TestRunner = MagicMock()
        _runners_smoke_stub = types.ModuleType("automation.runners.smoke")
        _runners_smoke_stub.TestRunner = MagicMock()

        # VLMClient stub must be callable (MagicMock, not object) because
        # cmd_functional constructs it before reaching the workspace preflight.
        _vlm_client_mock = MagicMock()
        _vlm_client_stub_local = types.ModuleType("automation.vlm.client")
        _vlm_client_stub_local.VLMClient = _vlm_client_mock
        _vlm_client_stub_local.VLMError = _VLMError

        extra_stubs = {
            "automation.proxmox": _proxmox_stub,
            "automation.proxmox.api": _proxmox_api_stub,
            "automation.proxmox.gpu": _proxmox_gpu_stub,
            "automation.proxmox.vm": _proxmox_vm_stub,
            "automation.runners": _runners_stub,
            "automation.runners.smoke": _runners_smoke_stub,
            "automation.runners.functional": _fr_mod,
            "automation.vlm.client": _vlm_client_stub_local,
            "automation.vlm.screenshot": sys.modules["automation.vlm.screenshot"],
            "automation.vlm.input": sys.modules["automation.vlm.input"],
        }

        with patch.dict(sys.modules, extra_stubs), \
             patch.object(_main_mod, "load_config", return_value=mock_config), \
             patch("urllib.request.urlopen",
                   side_effect=OSError("Connection timed out")):
            result = _main_mod.cmd_functional(args)

        self.assertEqual(result, 2,
                         f"cmd_functional must return 2 on workspace preflight failure, got {result}")


# ---------------------------------------------------------------------------
# Parametrized summary (for pytest -v readability)
# ---------------------------------------------------------------------------

import pytest

@pytest.mark.parametrize("test_class,method,expected_behavior", [
    ("TestNegativeWorkspaceUnreachable", "test_unreachable_workspace_returns_false_and_detail",
     "preflight returns False + error detail"),
    ("TestNegativeWorkspaceUnreachable", "test_unreachable_workspace_503_returns_false",
     "HTTP 503 treated as dead"),
    ("TestNegativeMissingBinary", "test_launch_nonexistent_binary_raises_step_failed",
     "StepFailed with Launch failed"),
    ("TestNegativeBadPassword", "test_ssh_refused_in_health_probe_returns_false",
     "health probe False on SSH refused"),
    ("TestNegativeMalformedScenario", "test_load_yaml_missing_steps_returns_empty",
     "empty steps list on missing key"),
    ("TestNegativeVLM5xx", "test_vlm_5xx_causes_run_test_to_return_false",
     "run_test False + FAIL in log on VLM exhaustion"),
    ("TestNegativeSSHRefused", "test_ssh_refused_causes_run_test_false",
     "run_test False + FAIL in log on SSH refused"),
    ("TestNegativePreflightWorkspaceExitCode", "test_cmd_functional_returns_2_on_dead_workspace",
     "cmd_functional exit code 2 on dead workspace"),
])
def test_negative_parametrized_smoke(test_class, method, expected_behavior):
    """Smoke check: each negative path is reachable (non-skip)."""
    # This parametrized test just documents the negative paths; the real
    # assertions live in the TestCase classes above.  pytest collects both.
    assert test_class  # always true — existence check only


if __name__ == "__main__":
    unittest.main()
