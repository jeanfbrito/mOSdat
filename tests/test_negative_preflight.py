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


if __name__ == "__main__":
    unittest.main()
