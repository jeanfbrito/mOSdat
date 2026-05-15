"""Shared bootstrap for test_issue_confirm_*.py files.

Loads automation.issue_confirm and its heavy deps via importlib.util so the
broken automation.runners.__init__ chain is never traversed.  Safe to import
from multiple test files in the same pytest session — uses sys.modules guards
throughout.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

_PROJ = Path(__file__).parent.parent


def _stub_module(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def _load_via_spec(dotted_name: str, rel_path: str) -> types.ModuleType:
    """Load a module by file path and register it in sys.modules."""
    spec = importlib.util.spec_from_file_location(
        dotted_name,
        _PROJ / rel_path,
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = dotted_name.rsplit(".", 1)[0]
    sys.modules[dotted_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Stub heavy deps with C-extensions or SSH requirements
# ---------------------------------------------------------------------------

for _sn in (
    "automation.vlm.client",
    "automation.vlm.input",
    "automation.vlm.screenshot",
    "automation.transport.vnc",
    "automation.transport.ssh",
    "automation.proxmox.api",
    "automation.proxmox.vm",
    "automation.proxmox.gpu",
    "automation.runners.smoke",
):
    if _sn not in sys.modules:
        _stub_module(
            _sn,
            VLMClient=object,
            InputInjector=object,
            Screenshotter=object,
            SSHClient=object,
            VncClient=object,
            ProxmoxAPI=object,
            ProxmoxAPIError=Exception,
            VMOperations=object,
            GPUManager=object,
            TestRunner=object,
        )

# Stub the broken runners __init__ before anything imports it
sys.modules.setdefault("automation.runners", types.ModuleType("automation.runners"))

# Load runner submodules in dependency order so functional.py imports succeed
if "automation.runners.scenario_loader" not in sys.modules:
    _load_via_spec(
        "automation.runners.scenario_loader",
        "automation/runners/scenario_loader.py",
    )
if "automation.runners.functional_steps" not in sys.modules:
    _load_via_spec(
        "automation.runners.functional_steps",
        "automation/runners/functional_steps.py",
    )
if "automation.runners.functional_lifecycle" not in sys.modules:
    _load_via_spec(
        "automation.runners.functional_lifecycle",
        "automation/runners/functional_lifecycle.py",
    )

# Load vm_state to get VmState
if "automation.runners.vm_state" not in sys.modules:
    _load_via_spec(
        "automation.runners.vm_state",
        "automation/runners/vm_state.py",
    )
VmState = sys.modules["automation.runners.vm_state"].VmState

# Load functional.py to get BugConfirmationResult
if "automation.runners.functional" not in sys.modules:
    _load_via_spec(
        "automation.runners.functional",
        "automation/runners/functional.py",
    )
BugConfirmationResult = sys.modules["automation.runners.functional"].BugConfirmationResult

# Load issue_confirm (the module under test)
if "automation.issue_confirm" not in sys.modules:
    _load_via_spec(
        "automation.issue_confirm",
        "automation/issue_confirm.py",
    )
_ic_mod = sys.modules["automation.issue_confirm"]

ConfirmInvocation = _ic_mod.ConfirmInvocation
run_confirm = _ic_mod.run_confirm
_exit_code = _ic_mod._exit_code


# ---------------------------------------------------------------------------
# autouse fixture — must be imported and used by each test file
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_real_automation_modules():
    """Other test files stub automation.* submodules at module import-time.
    Their stubs persist into the full pytest suite and break patches here.
    Restore real modules before each test in this file.
    """
    for _mod_name in (
        "automation.config",
        "automation.transport.ssh",
        "automation.transport.vnc",
        "automation.transport",
        "automation.runners.functional",
        "automation.runners.vm_state",
        "automation.runners",
        "automation.scenario",
        "automation.reporting.issue_report",
        "automation.reporting.report",
        "automation.reporting.aggregate",
        "automation.reporting",
        "automation.proxmox.gpu",
        "automation.proxmox.vm",
        "automation.proxmox",
        "automation.vlm.client",
        "automation.vlm.input",
        "automation.vlm.screenshot",
        "automation.vlm",
        "automation.issue_confirm",
    ):
        sys.modules.pop(_mod_name, None)
    importlib.import_module("automation.config")
    importlib.import_module("automation.transport.ssh")
    importlib.import_module("automation.transport.vnc")
    importlib.import_module("automation.scenario")
    importlib.import_module("automation.runners.functional")
    importlib.import_module("automation.reporting.report")
    importlib.import_module("automation.reporting.issue_report")
    importlib.import_module("automation.issue_confirm")
    yield


# ---------------------------------------------------------------------------
# Shared test helper factories
# ---------------------------------------------------------------------------

def make_issue_context(issue_id="3308"):
    """Return a minimal IssueContext-like object."""
    from automation.issue_fetch import IssueContext
    return IssueContext(
        id=issue_id,
        url=f"https://github.com/RocketChat/Rocket.Chat.Electron/issues/{issue_id}",
        title="Screen share picker opens on every launch",
        labels=["type: bug"],
        suspected_prs=["3266"],
        linked_prs=["3313"],
        body_markdown="Reporter says picker appears on every launch.",
        fetched_at="2026-05-02T19:00:00Z",
    )


def make_scenario_model(issue_id="3308"):
    """Return a minimal ScenarioModel for kind=bug-confirmation."""
    from automation.scenario import ScenarioModel
    data = {
        "name": "test-screenshare-picker",
        "kind": "bug-confirmation",
        "issue": {
            "id": issue_id,
            "url": f"https://github.com/RocketChat/Rocket.Chat.Electron/issues/{issue_id}",
        },
        "bug_signal": "is a screen-share picker dialog visible",
        "precondition_check": "is the Rocket.Chat window visible",
        "expected_env": {
            "install": "flatpak",
            "app_version": "4.14.0",
            "os": "Fedora 43",
        },
        "steps": [
            {"shell": "echo hello"},
        ],
    }
    return ScenarioModel.model_validate(data)


def make_vm_state(install="flatpak"):
    """Return a minimal VmState (uses directly-loaded VmState)."""
    return VmState(
        install_method=install,
        app_version="4.14.0",
        ozone_backend="x11",
        display_server="wayland",
        os_pretty_name="Fedora Linux 42",
        process_cmdline="rocketchat-desktop.bin --ozone-platform=x11",
    )


def make_bug_result(verdict_str: str):
    """Return a BugConfirmationResult for the given per-iter verdict."""
    precondition_met = verdict_str != "INCONCLUSIVE"
    bug_visible = verdict_str == "BUG_CONFIRMED"
    r = BugConfirmationResult(
        precondition_met=precondition_met,
        bug_visible=bug_visible,
        bug_signal_screenshot=None,
        precondition_screenshot=None,
        final_screenshot=Path("/tmp/final.png"),
        step_failures=[],
        elapsed_ms=500,
    )
    assert r.verdict == verdict_str
    return r


def make_vm_config(name="fedora42"):
    """Return a minimal VMConfig-like object."""
    vm = MagicMock()
    vm.name = name
    vm.vmid = 100
    vm.ip = "192.168.1.100"
    vm.user = "user"
    vm.is_windows = False
    vm.packages = []
    return vm


def make_project_config(vm_name="fedora42"):
    """Return a minimal ProjectConfig-like object."""
    cfg = MagicMock()
    vm = make_vm_config(vm_name)
    cfg.vm_by_name = {vm_name: vm}
    cfg.vlm.base_url = "http://localhost:11434"
    cfg.vlm.model = "test-model"
    cfg.vlm.verify_model = None
    cfg.proxmox.host = "proxmox.local"
    return cfg


@contextmanager
def confirm_patches(
    scenario_model,
    issue_ctx,
    iter_verdicts: list[str],
    vm_state=None,
    scenario_path: Optional[Path] = None,
):
    """Patch all external seams for run_confirm.

    iter_verdicts: list of per-iter BugConfirmationResult.verdict strings.
    """
    bug_results = [make_bug_result(v) for v in iter_verdicts]
    call_counter = {"n": 0}

    def _fake_run_scenario(runner, scenario, vars_):
        idx = call_counter["n"] % len(bug_results)
        call_counter["n"] += 1
        return bug_results[idx]

    mock_vnc = MagicMock()
    mock_vnc.__enter__ = MagicMock(return_value=mock_vnc)
    mock_vnc.__exit__ = MagicMock(return_value=False)

    mock_vm_state = vm_state if vm_state is not None else make_vm_state()
    mock_collect = MagicMock(return_value=mock_vm_state)
    mock_config = make_project_config()

    with (
        patch.object(_ic_mod, "run_scenario_via_runner", side_effect=_fake_run_scenario) as p_run,
        patch.object(_ic_mod, "_build_runner_for_vm",
                     return_value=(MagicMock(), MagicMock(), mock_vnc)) as p_build,
        patch.object(_ic_mod, "_copy_screenshot"),
        patch("automation.issue_fetch.fetch_issue", return_value=issue_ctx),
        patch("automation.config.load_config", return_value=mock_config),
        patch("automation.runners.vm_state.collect", mock_collect),
    ):
        yield {
            "run_scenario": p_run,
            "build_runner": p_build,
            "vm_state_collect": mock_collect,
            "config": mock_config,
        }


def scenario_load_patches(scenario):
    """Return patchers that mock YAML scenario loading."""
    return (
        patch("builtins.open", new_callable=MagicMock),
        patch("yaml.safe_load", return_value={}),
        patch("automation.scenario.ScenarioModel.model_validate", return_value=scenario),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "mkdir", return_value=None),
        patch.object(_ic_mod, "_copy_screenshot"),
    )
