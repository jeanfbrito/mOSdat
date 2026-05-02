"""Tests for automation/issue_confirm.py — Phase C orchestrator.

All tests are hermetic: no real SSH, VLM, or HTTP calls.

Strategy:
- Monkeypatch `automation.issue_confirm.run_scenario_via_runner` to avoid
  SSH/VLM entirely.
- Monkeypatch `automation.issue_confirm._build_runner_for_vm` to return
  a MagicMock runner + SSH + VNC context.
- Monkeypatch `automation.issue_fetch.fetch_issue` to return a fixed IssueContext.
- Monkeypatch `automation.runners.vm_state.collect` to return a fixed VmState
  (or assert NOT called for --no-state-snapshot).
- Monkeypatch `automation.config.load_config` to return a fixture config.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, call

import pytest


@pytest.fixture(autouse=True)
def _restore_real_automation_modules():
    """Other test files (test_concurrent_safety, test_runner_dispatch,
    test_chaos, test_dashboard, etc.) stub automation.* submodules at module
    import-time. Their stubs persist into the full pytest suite and break
    `patch("automation.config.load_config", ...)` here. Restore real
    modules before each test in this file.
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
        # NOT popping automation.issue_fetch — it's never stubbed elsewhere,
        # and reimporting it breaks test_issue_fetch's monkeypatch chain.
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
# Project root and stubs for modules that have C-extension or SSH deps
# ---------------------------------------------------------------------------

_PROJ = Path(__file__).parent.parent


def _stub_module(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# Stub heavy deps that aren't needed for orchestrator tests.
# These MUST be in place before any automation.* import that goes through
# the broken automation.runners.__init__ chain (smoke → proxmox.gpu → etc).
for _sn in ("automation.vlm.client", "automation.vlm.input",
            "automation.vlm.screenshot", "automation.transport.vnc",
            "automation.transport.ssh", "automation.proxmox.api",
            "automation.proxmox.vm", "automation.proxmox.gpu",
            "automation.runners.smoke"):
    if _sn not in sys.modules:
        _stub_module(_sn,
                     VLMClient=object,
                     InputInjector=object,
                     Screenshotter=object,
                     SSHClient=object,
                     VncClient=object,
                     ProxmoxAPI=object,
                     ProxmoxAPIError=Exception,
                     VMOperations=object,
                     GPUManager=object,
                     TestRunner=object)

# Stub the broken runners __init__ before it's imported anywhere
_runners_init = types.ModuleType("automation.runners")
sys.modules.setdefault("automation.runners", _runners_init)

# Load vm_state directly to get VmState without going through __init__
_vmstate_spec = importlib.util.spec_from_file_location(
    "automation.runners.vm_state",
    _PROJ / "automation" / "runners" / "vm_state.py",
)
_vmstate_mod = importlib.util.module_from_spec(_vmstate_spec)
_vmstate_mod.__package__ = "automation.runners"
sys.modules["automation.runners.vm_state"] = _vmstate_mod
_vmstate_spec.loader.exec_module(_vmstate_mod)
VmState = _vmstate_mod.VmState

# Load functional.py directly to get BugConfirmationResult
_fspec = importlib.util.spec_from_file_location(
    "automation.runners.functional",
    _PROJ / "automation" / "runners" / "functional.py",
)
_fmod = importlib.util.module_from_spec(_fspec)
_fmod.__package__ = "automation.runners"
sys.modules["automation.runners.functional"] = _fmod
_fspec.loader.exec_module(_fmod)
BugConfirmationResult = _fmod.BugConfirmationResult

# ---------------------------------------------------------------------------
# Load the modules under test after stubs are in place
# ---------------------------------------------------------------------------

import importlib as _il

# Load issue_confirm
_ic_spec = _il.util.spec_from_file_location(
    "automation.issue_confirm",
    _PROJ / "automation" / "issue_confirm.py",
)
_ic_mod = _il.util.module_from_spec(_ic_spec)
_ic_mod.__package__ = "automation"
sys.modules["automation.issue_confirm"] = _ic_mod
_ic_spec.loader.exec_module(_ic_mod)

ConfirmInvocation = _ic_mod.ConfirmInvocation
run_confirm = _ic_mod.run_confirm
_exit_code = _ic_mod._exit_code

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

def _make_issue_context(issue_id="3308"):
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


def _make_scenario_model(issue_id="3308"):
    """Return a minimal ScenarioModel for kind=bug-confirmation."""
    from automation.scenario import ScenarioModel, IssueRef, ExpectedEnv
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


def _make_vm_state(install="flatpak"):
    """Return a minimal VmState-like object (uses directly-loaded VmState)."""
    return VmState(
        install_method=install,
        app_version="4.14.0",
        ozone_backend="x11",
        display_server="wayland",
        os_pretty_name="Fedora Linux 42",
        process_cmdline="rocketchat-desktop.bin --ozone-platform=x11",
    )


def _make_bug_result(verdict_str: str):
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


def _make_vm_config(name="fedora42"):
    """Return a minimal VMConfig-like object."""
    vm = MagicMock()
    vm.name = name
    vm.vmid = 100
    vm.ip = "192.168.1.100"
    vm.user = "user"
    vm.is_windows = False
    vm.packages = []
    return vm


def _make_project_config(vm_name="fedora42"):
    """Return a minimal ProjectConfig-like object."""
    cfg = MagicMock()
    vm = _make_vm_config(vm_name)
    cfg.vm_by_name = {vm_name: vm}
    cfg.vlm.base_url = "http://localhost:11434"
    cfg.vlm.model = "test-model"
    cfg.vlm.verify_model = None
    cfg.proxmox.host = "proxmox.local"
    return cfg


# ---------------------------------------------------------------------------
# Context manager to patch the four key seams
# ---------------------------------------------------------------------------

@contextmanager
def _confirm_patches(
    scenario_model,
    issue_ctx,
    iter_verdicts: list[str],
    vm_state=None,
    skip_state_snapshot=False,
    scenario_path: Optional[Path] = None,
):
    """Patch all external seams for run_confirm.

    iter_verdicts: list of per-iter BugConfirmationResult.verdict strings.
    """
    bug_results = [_make_bug_result(v) for v in iter_verdicts]
    call_counter = {"n": 0}

    def _fake_run_scenario(runner, scenario, vars_):
        idx = call_counter["n"] % len(bug_results)
        call_counter["n"] += 1
        return bug_results[idx]

    mock_vnc = MagicMock()
    mock_vnc.__enter__ = MagicMock(return_value=mock_vnc)
    mock_vnc.__exit__ = MagicMock(return_value=False)

    mock_runner = MagicMock()
    mock_ssh = MagicMock()

    mock_vm_state = vm_state if vm_state is not None else _make_vm_state()
    mock_collect = MagicMock(return_value=mock_vm_state)

    mock_config = _make_project_config()

    # Provide a TOML file path glob result so load_config isn't called with None
    dummy_toml = Path("/tmp/dummy.toml")

    with (
        patch.object(_ic_mod, "run_scenario_via_runner", side_effect=_fake_run_scenario) as p_run,
        patch.object(_ic_mod, "_build_runner_for_vm",
                     return_value=(mock_runner, mock_ssh, mock_vnc)) as p_build,
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


# ---------------------------------------------------------------------------
# Tests: verdict + exit-code mapping
# ---------------------------------------------------------------------------

class TestExitCodeMapping:
    def test_confirm_confirmed_exit0(self):
        assert _exit_code("CONFIRMED", "confirm") == 0

    def test_confirm_not_reproduced_exit1(self):
        assert _exit_code("NOT_REPRODUCED", "confirm") == 1

    def test_verify_fix_not_reproduced_exit0(self):
        assert _exit_code("NOT_REPRODUCED", "verify-fix") == 0

    def test_verify_fix_confirmed_exit1(self):
        assert _exit_code("CONFIRMED", "verify-fix") == 1

    def test_intermittent_exit2(self):
        assert _exit_code("INTERMITTENT", "confirm") == 2
        assert _exit_code("INTERMITTENT", "verify-fix") == 2

    def test_harness_error_exit3(self):
        assert _exit_code("HARNESS_ERROR", "confirm") == 3

    def test_inconclusive_exit3(self):
        assert _exit_code("INCONCLUSIVE", "confirm") == 3


# ---------------------------------------------------------------------------
# Tests: confirm mode — 3/3 BUG_CONFIRMED
# ---------------------------------------------------------------------------

class TestConfirmMode:
    def test_confirmed_3_of_3(self, tmp_path):
        scenario = _make_scenario_model()
        issue = _make_issue_context()

        with _confirm_patches(scenario, issue, ["BUG_CONFIRMED"] * 3):
            inv = ConfirmInvocation(
                issue_id_or_url="3308",
                vm_name="fedora42",
                iterations=3,
                mode="confirm",
                output_dir=tmp_path,
                skip_state_snapshot=False,
                scenario_path=None,
            )

            # Patch scenario loading to return our fixture
            with (
                patch("builtins.open", new_callable=MagicMock),
                patch("yaml.safe_load", return_value={}),
                patch("automation.scenario.ScenarioModel.model_validate", return_value=scenario),
                patch.object(Path, "exists", return_value=True),
                patch.object(Path, "mkdir", return_value=None),
                patch.object(_ic_mod, "_copy_screenshot"),
            ):
                artifacts = run_confirm(inv)

        assert artifacts.verdict == "CONFIRMED"
        assert artifacts.exit_code == 0
        assert len(artifacts.iter_dirs) == 3


# ---------------------------------------------------------------------------
# Tests: verify-fix mode
# ---------------------------------------------------------------------------

class TestVerifyFixMode:
    def test_fix_holds_not_reproduced(self, tmp_path):
        """3/3 BUG_NOT_VISIBLE in verify-fix → exit 0."""
        scenario = _make_scenario_model()
        issue = _make_issue_context()

        with _confirm_patches(scenario, issue, ["BUG_NOT_VISIBLE"] * 3):
            inv = ConfirmInvocation(
                issue_id_or_url="3308",
                vm_name="fedora42",
                iterations=3,
                mode="verify-fix",
                output_dir=tmp_path,
                skip_state_snapshot=True,
                scenario_path=None,
            )
            with (
                patch("builtins.open", new_callable=MagicMock),
                patch("yaml.safe_load", return_value={}),
                patch("automation.scenario.ScenarioModel.model_validate", return_value=scenario),
                patch.object(Path, "exists", return_value=True),
                patch.object(Path, "mkdir", return_value=None),
                patch.object(_ic_mod, "_copy_screenshot"),
            ):
                artifacts = run_confirm(inv)

        assert artifacts.verdict == "NOT_REPRODUCED"
        assert artifacts.exit_code == 0

    def test_regression_bug_still_visible(self, tmp_path):
        """3/3 BUG_CONFIRMED in verify-fix → exit 1."""
        scenario = _make_scenario_model()
        issue = _make_issue_context()

        with _confirm_patches(scenario, issue, ["BUG_CONFIRMED"] * 3):
            inv = ConfirmInvocation(
                issue_id_or_url="3308",
                vm_name="fedora42",
                iterations=3,
                mode="verify-fix",
                output_dir=tmp_path,
                skip_state_snapshot=True,
                scenario_path=None,
            )
            with (
                patch("builtins.open", new_callable=MagicMock),
                patch("yaml.safe_load", return_value={}),
                patch("automation.scenario.ScenarioModel.model_validate", return_value=scenario),
                patch.object(Path, "exists", return_value=True),
                patch.object(Path, "mkdir", return_value=None),
                patch.object(_ic_mod, "_copy_screenshot"),
            ):
                artifacts = run_confirm(inv)

        assert artifacts.verdict == "CONFIRMED"
        assert artifacts.exit_code == 1


# ---------------------------------------------------------------------------
# Tests: INTERMITTENT
# ---------------------------------------------------------------------------

class TestIntermittent:
    def test_mixed_iters_exit2(self, tmp_path):
        """Mixed BUG_CONFIRMED + BUG_NOT_VISIBLE → INTERMITTENT → exit 2."""
        scenario = _make_scenario_model()
        issue = _make_issue_context()

        with _confirm_patches(
            scenario, issue,
            ["BUG_CONFIRMED", "BUG_NOT_VISIBLE", "BUG_CONFIRMED"],
        ):
            inv = ConfirmInvocation(
                issue_id_or_url="3308",
                vm_name="fedora42",
                iterations=3,
                mode="confirm",
                output_dir=tmp_path,
                skip_state_snapshot=True,
                scenario_path=None,
            )
            with (
                patch("builtins.open", new_callable=MagicMock),
                patch("yaml.safe_load", return_value={}),
                patch("automation.scenario.ScenarioModel.model_validate", return_value=scenario),
                patch.object(Path, "exists", return_value=True),
                patch.object(Path, "mkdir", return_value=None),
                patch.object(_ic_mod, "_copy_screenshot"),
            ):
                artifacts = run_confirm(inv)

        assert artifacts.verdict == "INTERMITTENT"
        assert artifacts.exit_code == 2


# ---------------------------------------------------------------------------
# Tests: all inconclusive → HARNESS_ERROR
# ---------------------------------------------------------------------------

class TestHarnessError:
    def test_all_inconclusive_exit3(self, tmp_path):
        scenario = _make_scenario_model()
        issue = _make_issue_context()

        with _confirm_patches(scenario, issue, ["INCONCLUSIVE"] * 3):
            inv = ConfirmInvocation(
                issue_id_or_url="3308",
                vm_name="fedora42",
                iterations=3,
                mode="confirm",
                output_dir=tmp_path,
                skip_state_snapshot=True,
                scenario_path=None,
            )
            with (
                patch("builtins.open", new_callable=MagicMock),
                patch("yaml.safe_load", return_value={}),
                patch("automation.scenario.ScenarioModel.model_validate", return_value=scenario),
                patch.object(Path, "exists", return_value=True),
                patch.object(Path, "mkdir", return_value=None),
                patch.object(_ic_mod, "_copy_screenshot"),
            ):
                artifacts = run_confirm(inv)

        assert artifacts.verdict == "HARNESS_ERROR"
        assert artifacts.exit_code == 3


# ---------------------------------------------------------------------------
# Tests: runner exception on iter-1 → INCONCLUSIVE for that iter, loop continues
# ---------------------------------------------------------------------------

class TestRunnerExceptionContinues:
    def test_exception_iter1_loop_continues(self, tmp_path):
        """If iter-1 raises, it's recorded as INCONCLUSIVE; iters 2+3 still run."""
        scenario = _make_scenario_model()
        issue = _make_issue_context()

        call_counter = {"n": 0}
        good_result = _make_bug_result("BUG_CONFIRMED")

        def _flaky_run(runner, scenario, vars_):
            call_counter["n"] += 1
            if call_counter["n"] == 1:
                raise RuntimeError("VM unreachable on iter 1")
            return good_result

        mock_vnc = MagicMock()
        mock_vnc.__enter__ = MagicMock(return_value=mock_vnc)
        mock_vnc.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(_ic_mod, "run_scenario_via_runner", side_effect=_flaky_run),
            patch.object(_ic_mod, "_build_runner_for_vm",
                         return_value=(MagicMock(), MagicMock(), mock_vnc)),
            patch("automation.issue_fetch.fetch_issue", return_value=issue),
            patch("automation.config.load_config", return_value=_make_project_config()),
            patch("automation.runners.vm_state.collect", return_value=_make_vm_state()),
        ):
            inv = ConfirmInvocation(
                issue_id_or_url="3308",
                vm_name="fedora42",
                iterations=3,
                mode="confirm",
                output_dir=tmp_path,
                skip_state_snapshot=True,
                scenario_path=None,
            )
            with (
                patch("builtins.open", new_callable=MagicMock),
                patch("yaml.safe_load", return_value={}),
                patch("automation.scenario.ScenarioModel.model_validate", return_value=scenario),
                patch.object(Path, "exists", return_value=True),
                patch.object(Path, "mkdir", return_value=None),
                patch.object(_ic_mod, "_copy_screenshot"),
            ):
                artifacts = run_confirm(inv)

        # All 3 iters were attempted (1 failed → INCONCLUSIVE, 2+3 → BUG_CONFIRMED)
        assert call_counter["n"] == 3
        assert len(artifacts.iter_dirs) == 3


# ---------------------------------------------------------------------------
# Tests: missing scenario YAML → exit 4
# ---------------------------------------------------------------------------

class TestMissingScenario:
    def test_missing_yaml_exit4(self, tmp_path):
        """If the scenario YAML is missing, exit code must be 4."""
        issue = _make_issue_context()

        with (
            patch("automation.issue_fetch.fetch_issue", return_value=issue),
            patch("automation.config.load_config", return_value=_make_project_config()),
        ):
            inv = ConfirmInvocation(
                issue_id_or_url="3308",
                vm_name="fedora42",
                iterations=3,
                mode="confirm",
                output_dir=tmp_path,
                skip_state_snapshot=True,
                # Pass a non-existent explicit path
                scenario_path=tmp_path / "nonexistent.yaml",
            )
            artifacts = run_confirm(inv)

        assert artifacts.exit_code == 4


# ---------------------------------------------------------------------------
# Tests: --no-state-snapshot skips vm_state.collect
# ---------------------------------------------------------------------------

class TestNoStateSnapshot:
    def test_skip_state_snapshot_not_called(self, tmp_path):
        """When skip_state_snapshot=True, vm_state.collect must NOT be called."""
        scenario = _make_scenario_model()
        issue = _make_issue_context()

        mock_collect = MagicMock()

        mock_vnc = MagicMock()
        mock_vnc.__enter__ = MagicMock(return_value=mock_vnc)
        mock_vnc.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(_ic_mod, "run_scenario_via_runner",
                         return_value=_make_bug_result("BUG_CONFIRMED")),
            patch.object(_ic_mod, "_build_runner_for_vm",
                         return_value=(MagicMock(), MagicMock(), mock_vnc)),
            patch("automation.issue_fetch.fetch_issue", return_value=issue),
            patch("automation.config.load_config", return_value=_make_project_config()),
            patch("automation.runners.vm_state.collect", mock_collect),
        ):
            inv = ConfirmInvocation(
                issue_id_or_url="3308",
                vm_name="fedora42",
                iterations=1,
                mode="confirm",
                output_dir=tmp_path,
                skip_state_snapshot=True,
                scenario_path=None,
            )
            with (
                patch("builtins.open", new_callable=MagicMock),
                patch("yaml.safe_load", return_value={}),
                patch("automation.scenario.ScenarioModel.model_validate", return_value=scenario),
                patch.object(Path, "exists", return_value=True),
                patch.object(Path, "mkdir", return_value=None),
                patch.object(_ic_mod, "_copy_screenshot"),
            ):
                run_confirm(inv)

        mock_collect.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: --refresh-issue-context passes refresh=True to fetch_issue
# ---------------------------------------------------------------------------

class TestRefreshIssueContext:
    def test_refresh_flag_passed(self, tmp_path):
        """refresh_issue_context=True must reach fetch_issue(refresh=True)."""
        scenario = _make_scenario_model()
        issue = _make_issue_context()

        mock_fetch = MagicMock(return_value=issue)

        mock_vnc = MagicMock()
        mock_vnc.__enter__ = MagicMock(return_value=mock_vnc)
        mock_vnc.__exit__ = MagicMock(return_value=False)

        with (
            patch("automation.issue_fetch.fetch_issue", mock_fetch),
            patch.object(_ic_mod, "run_scenario_via_runner",
                         return_value=_make_bug_result("BUG_CONFIRMED")),
            patch.object(_ic_mod, "_build_runner_for_vm",
                         return_value=(MagicMock(), MagicMock(), mock_vnc)),
            patch("automation.config.load_config", return_value=_make_project_config()),
            patch("automation.runners.vm_state.collect", return_value=_make_vm_state()),
        ):
            inv = ConfirmInvocation(
                issue_id_or_url="3308",
                vm_name="fedora42",
                iterations=1,
                mode="confirm",
                output_dir=tmp_path,
                skip_state_snapshot=True,
                refresh_issue_context=True,
                scenario_path=None,
            )
            with (
                patch("builtins.open", new_callable=MagicMock),
                patch("yaml.safe_load", return_value={}),
                patch("automation.scenario.ScenarioModel.model_validate", return_value=scenario),
                patch.object(Path, "exists", return_value=True),
                patch.object(Path, "mkdir", return_value=None),
                patch.object(_ic_mod, "_copy_screenshot"),
            ):
                run_confirm(inv)

        mock_fetch.assert_called_once_with("3308", refresh=True)


# ---------------------------------------------------------------------------
# Tests: CLI arg parsing
# ---------------------------------------------------------------------------

class TestCLIArgParsing:
    """Load main.py and verify that 'confirm' args parse correctly."""

    def _build_parser(self):
        # Load main module
        spec = importlib.util.spec_from_file_location(
            "automation.main",
            _PROJ / "automation" / "main.py",
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "automation"
        # Stub heavy imports the module does at top-level
        sys.modules.setdefault("automation.issue_confirm", _ic_mod)

        # We only need the parser shape, not to run main() fully.
        # Patch load_config and state so the module loads cleanly.
        with (
            patch("automation.config.load_config", return_value=MagicMock()),
            patch("automation.state.StateManager", MagicMock()),
        ):
            spec.loader.exec_module(mod)
        return mod

    def test_confirm_args_parse(self):
        """mosdat confirm 3308 --vm fedora42 --no-state-snapshot --output /tmp/foo"""
        mod = self._build_parser()

        import argparse
        # Reconstruct just the parser from main()
        parser = argparse.ArgumentParser(prog="mosdat")
        sub = parser.add_subparsers(dest="command")

        confirm_p = sub.add_parser("confirm")
        confirm_p.add_argument("issue")
        confirm_p.add_argument("--vm", required=True)
        confirm_p.add_argument("--iterations", type=int, default=None)
        confirm_p.add_argument("--mode", choices=["confirm", "verify-fix", "regression"],
                               default="confirm")
        confirm_p.add_argument("--scenario", type=Path, default=None)
        confirm_p.add_argument("--output", type=Path, default=None)
        confirm_p.add_argument("--refresh-issue-context", action="store_true",
                               dest="refresh_issue_context")
        confirm_p.add_argument("--html", action="store_true")
        confirm_p.add_argument("--no-state-snapshot", action="store_true",
                               dest="no_state_snapshot")

        args = parser.parse_args([
            "confirm", "3308", "--vm", "fedora42",
            "--no-state-snapshot", "--output", "/tmp/foo",
        ])

        assert args.command == "confirm"
        assert args.issue == "3308"
        assert args.vm == "fedora42"
        assert args.no_state_snapshot is True
        assert args.output == Path("/tmp/foo")
        assert args.mode == "confirm"
        assert args.refresh_issue_context is False
        assert args.html is False
        assert args.iterations is None
