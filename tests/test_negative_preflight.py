"""Negative test: end-to-end cmd_functional workspace preflight exit code.

Extracted from test_negative.py to keep that file under 500 LOC.
Covers TestNegativePreflightWorkspaceExitCode — verifies cmd_functional
returns exit code 2 when the workspace URL is unreachable.

Imports module setup from test_negative to avoid duplicate sys.modules
stub setup that would contaminate the shared test process.
"""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import shared setup from test_negative — runs their module-level stub
# setup once; avoids re-exec of main.py / functional.py in the same process.
from tests.test_negative import (
    _fr_mod,
    _main_mod,
    _VLMError,
    FIXTURES,
)


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
        # I6: cmd_functional imports set_cache_enabled when args.no_cache is truthy
        # (MagicMock attrs are always truthy by default).
        _vlm_client_stub_local.set_cache_enabled = lambda enabled: None

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


class TestVerifyRecordedRequiresRecordSession(unittest.TestCase):
    """Card 3: cmd_functional must hard-fail (nonzero exit) BEFORE any VM/
    Proxmox connection when a loaded step uses verify_recorded but
    --record-session was not passed — never silently auto-enable recording.
    """

    def test_verify_recorded_without_record_session_exits_nonzero_before_vm_connect(self):
        args = MagicMock()
        args.record = False
        args.vms = "ubuntu24"
        args.config = str(FIXTURES / "verify_recorded_no_session.yaml")
        args.skip_warmup = True
        args.skip_model_check = True
        args.skip_workspace_check = False
        args.skip_health_probe = True
        args.no_checkpoints = True
        args.test = "verify_recorded_no_session"
        args.screenshots = None
        args.save_screenshots = False
        args.from_step = 1
        args.until_step = None
        args.model = "test-model"
        args.verify_model = "test-verify-model"
        args.popup_sweep = False
        args.results_dir = None
        args.record_session = False  # the precondition under test

        mock_config = MagicMock()
        mock_config.vm_by_name = {"ubuntu24": MagicMock()}
        mock_config.functional.workspace_url = "https://example.com"
        mock_config.functional.tests_dir = FIXTURES
        mock_config.framework_path = Path("/tmp")
        mock_config.proxmox.password = "test"

        # No automation.proxmox/automation.runners stubbing needed: the
        # verify_recorded precondition check (this test's target) fires
        # BEFORE `ProxmoxAPI(...)` is ever constructed in cmd_functional —
        # proven below by asserting the mock was never called. Overriding
        # "automation.runners" here would also shadow conftest.py's
        # preloaded real package and break the scenario_loader submodule
        # import that happens earlier in cmd_functional.
        _proxmox_api_mock = MagicMock()
        _proxmox_api_stub = types.ModuleType("automation.proxmox.api")
        _proxmox_api_stub.ProxmoxAPI = _proxmox_api_mock
        _proxmox_api_stub.ProxmoxAPIError = Exception

        _vlm_client_mock = MagicMock()
        _vlm_client_stub_local = types.ModuleType("automation.vlm.client")
        _vlm_client_stub_local.VLMClient = _vlm_client_mock
        _vlm_client_stub_local.VLMError = _VLMError
        _vlm_client_stub_local.set_cache_enabled = lambda enabled: None

        extra_stubs = {
            "automation.proxmox.api": _proxmox_api_stub,
            "automation.runners.functional": _fr_mod,
            "automation.vlm.client": _vlm_client_stub_local,
            "automation.vlm.screenshot": sys.modules["automation.vlm.screenshot"],
            "automation.vlm.input": sys.modules["automation.vlm.input"],
        }

        with patch.dict(sys.modules, extra_stubs), \
             patch.object(_main_mod, "load_config", return_value=mock_config):
            result = _main_mod.cmd_functional(args)

        self.assertNotEqual(
            result, 0,
            "cmd_functional must exit nonzero when verify_recorded is present "
            "without --record-session",
        )
        _proxmox_api_mock.assert_not_called()

    def test_verify_recorded_with_record_session_passes_the_precondition(self):
        """Sanity check: the SAME scenario does NOT hard-fail the precondition
        when --record-session IS set (proves the check is specific to the
        missing-flag case, not to verify_recorded's mere presence). We only
        assert it gets past the precondition — not a full happy-path run,
        which needs a much heavier VM/VNC stub rig (see
        TestPartialRunPreconditionWarning for that rig)."""
        args = MagicMock()
        args.record = False
        args.vms = "ubuntu24"
        args.config = str(FIXTURES / "verify_recorded_no_session.yaml")
        args.skip_warmup = True
        args.skip_model_check = True
        args.skip_workspace_check = False
        args.skip_health_probe = True
        args.no_checkpoints = True
        args.test = "verify_recorded_no_session"
        args.screenshots = None
        args.save_screenshots = False
        args.from_step = 1
        args.until_step = None
        args.model = "test-model"
        args.verify_model = "test-verify-model"
        args.popup_sweep = False
        args.results_dir = None
        args.record_session = True  # precondition satisfied this time

        mock_config = MagicMock()
        mock_config.vm_by_name = {"ubuntu24": MagicMock()}
        # Force a workspace preflight failure instead — a DIFFERENT, later
        # gate — so we can prove the verify_recorded precondition itself did
        # not stop the run at exit code 1 for this reason.
        mock_config.functional.workspace_url = "https://10.255.255.1/"
        mock_config.functional.tests_dir = FIXTURES
        mock_config.framework_path = Path("/tmp")
        mock_config.proxmox.password = "test"

        _vlm_client_mock = MagicMock()
        _vlm_client_stub_local = types.ModuleType("automation.vlm.client")
        _vlm_client_stub_local.VLMClient = _vlm_client_mock
        _vlm_client_stub_local.VLMError = _VLMError
        _vlm_client_stub_local.set_cache_enabled = lambda enabled: None

        extra_stubs = {
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

        # Reaches the (later, unrelated) workspace preflight gate = 2, proving
        # the verify_recorded/--record-session precondition did not block it.
        self.assertEqual(
            result, 2,
            "with --record-session set, the run should proceed past the "
            f"verify_recorded precondition to the workspace preflight gate; got {result}",
        )


class TestPartialRunPreconditionWarning(unittest.TestCase):
    """B4: cmd_functional must print a precondition-awareness warning when a
    partial run (--from-step > 1) skips earlier steps, and must NOT print it
    for a full run (from_step == 1)."""

    def _run_with_from_step(self, from_step, capture):
        """Drive cmd_functional through a full happy-path VM loop with all
        I/O mocked, and return the captured stdout text."""
        import contextlib
        import io
        import tempfile
        from types import SimpleNamespace

        args = SimpleNamespace(
            record=False,
            vms="ubuntu24",
            config=str(FIXTURES / "broken_ssh_refused.yaml"),
            skip_warmup=True,
            skip_workspace_check=True,
            skip_health_probe=True,
            skip_model_check=True,
            no_checkpoints=True,
            test="broken_ssh_refused",
            screenshots=None,
            save_screenshots=False,
            from_step=from_step,
            until_step=None,
            model="test-model",
            verify_model="test-verify-model",
            popup_sweep=False,
            results_dir=None,
            record_session=False,
            no_cache=False,
            cursor_instant=False,
            timeout=0,
        )

        vm = SimpleNamespace(
            name="ubuntu24",
            vmid=1,
            ip="10.0.0.1",
            user="root",
            is_windows=False,
            os_type="linux",
            packages=[],
            x11="off",
            scenario_subdir=None,
            scenario_fallback_subdirs=[],
        )

        mock_config = SimpleNamespace(
            vm_by_name={"ubuntu24": vm},
            functional=SimpleNamespace(
                tests_dir=FIXTURES, workspace_url=None,
                test_user="u", test_password="p",
            ),
            vlm=SimpleNamespace(
                base_url="http://x", model="m", verify_model=None,
                api_key=None, max_tokens_floor=None, expected_model=None,
            ),
            proxmox=SimpleNamespace(password="test"),
            app=SimpleNamespace(process_name="rocketchat-desktop"),
            framework_path=Path(tempfile.mkdtemp()),
        )

        run_test_calls: list[dict] = []

        class FakeRunner:
            def __init__(self, **kw):
                pass

            def run_test(self, steps, name, vars=None, start_index=0, **kw):
                run_test_calls.append({"steps": steps, "start_index": start_index})
                return True, "PASS: fake"

        class FakeVncCtx:
            def __enter__(self):
                return MagicMock()

            def __exit__(self, *a):
                return False

        _ssh_stub = types.ModuleType("automation.transport.ssh")
        _ssh_stub.SSHClient = MagicMock(return_value=MagicMock())
        _vnc_stub = types.ModuleType("automation.transport.vnc")
        _vnc_stub.VncClient = MagicMock(return_value=FakeVncCtx())
        _atspi_stub = types.ModuleType("automation.atspi")
        _atspi_stub.AtspiClient = MagicMock(return_value=MagicMock())
        _uia_stub = types.ModuleType("automation.uia")
        _uia_stub.UiaClient = MagicMock(return_value=MagicMock())
        _recording_stub = types.ModuleType("automation.recording")
        _recording_stub.SessionRecorder = MagicMock()
        _reporting_stub = types.ModuleType("automation.reporting.report")
        _reporting_stub.generate_html_report = MagicMock(return_value=Path("/tmp/report.html"))
        _proxmox_api_stub = types.ModuleType("automation.proxmox.api")
        _proxmox_api_stub.ProxmoxAPI = MagicMock(return_value=MagicMock())
        _proxmox_vm_stub = types.ModuleType("automation.proxmox.vm")
        _proxmox_vm_stub.VMOperations = MagicMock()
        _vlm_client_stub = types.ModuleType("automation.vlm.client")
        _vlm_client_stub.VLMClient = MagicMock(return_value=MagicMock())
        _vlm_client_stub.VLMError = _VLMError
        _vlm_client_stub.set_cache_enabled = lambda enabled: None
        _vlm_screenshot_stub = types.ModuleType("automation.vlm.screenshot")
        _vlm_screenshot_stub.Screenshotter = MagicMock(return_value=MagicMock())
        _vlm_input_stub = types.ModuleType("automation.vlm.input")
        _vlm_input_stub.InputInjector = MagicMock(return_value=MagicMock())

        extra_stubs = {
            "automation.transport.ssh": _ssh_stub,
            "automation.transport.vnc": _vnc_stub,
            "automation.atspi": _atspi_stub,
            "automation.uia": _uia_stub,
            "automation.recording": _recording_stub,
            "automation.reporting.report": _reporting_stub,
            "automation.proxmox.api": _proxmox_api_stub,
            "automation.proxmox.vm": _proxmox_vm_stub,
            "automation.vlm.client": _vlm_client_stub,
            "automation.vlm.screenshot": _vlm_screenshot_stub,
            "automation.vlm.input": _vlm_input_stub,
            "automation.runners.functional": _fr_mod,
        }

        buf = io.StringIO()
        with patch.dict(sys.modules, extra_stubs), \
             patch.object(_main_mod, "load_config", return_value=mock_config), \
             patch.object(_fr_mod, "FunctionalRunner", FakeRunner), \
             contextlib.redirect_stdout(buf):
            result = _main_mod.cmd_functional(args)

        self.assertEqual(result, 0, f"cmd_functional must PASS end-to-end, got {result}; output={buf.getvalue()}")
        capture["run_test_calls"] = run_test_calls
        return buf.getvalue()

    def test_partial_run_prints_precondition_warning(self):
        capture = {}
        output = self._run_with_from_step(2, capture)
        self.assertIn(
            "WARNING: starting at step 2", output,
            f"partial run (--from-step 2) must print the precondition warning; got: {output}",
        )
        self.assertEqual(capture["run_test_calls"][0]["start_index"], 1,
                          "start_index passed to run_test must be from_step - 1")

    def test_full_run_does_not_print_precondition_warning(self):
        capture = {}
        output = self._run_with_from_step(1, capture)
        self.assertNotIn(
            "starting at step", output,
            f"full run (from_step=1) must NOT print the partial-run precondition warning; got: {output}",
        )
        self.assertEqual(capture["run_test_calls"][0]["start_index"], 0)


if __name__ == "__main__":
    unittest.main()
