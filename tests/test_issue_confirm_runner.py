"""Tests for run_confirm() runner-level behaviour in automation/issue_confirm.py:
- exception on iter-1 continues the loop
- missing scenario YAML → exit 4
- --no-state-snapshot skips vm_state.collect
- --refresh-issue-context passes refresh=True to fetch_issue

All tests are hermetic: no real SSH, VLM, or HTTP calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._ic_bootstrap import (
    ConfirmInvocation,
    _ic_mod,
    _restore_real_automation_modules,  # noqa: F401 — autouse fixture
    make_bug_result,
    make_issue_context,
    make_project_config,
    make_scenario_model,
    make_vm_state,
    run_confirm,
)


class TestRunnerExceptionContinues:
    def test_exception_iter1_loop_continues(self, tmp_path):
        """If iter-1 raises, it's recorded as INCONCLUSIVE; iters 2+3 still run."""
        scenario = make_scenario_model()
        issue = make_issue_context()

        call_counter = {"n": 0}
        good_result = make_bug_result("BUG_CONFIRMED")

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
            patch("automation.config.load_config", return_value=make_project_config()),
            patch("automation.runners.vm_state.collect", return_value=make_vm_state()),
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


class TestMissingScenario:
    def test_missing_yaml_exit4(self, tmp_path):
        """If the scenario YAML is missing, exit code must be 4."""
        issue = make_issue_context()

        with (
            patch("automation.issue_fetch.fetch_issue", return_value=issue),
            patch("automation.config.load_config", return_value=make_project_config()),
        ):
            inv = ConfirmInvocation(
                issue_id_or_url="3308",
                vm_name="fedora42",
                iterations=3,
                mode="confirm",
                output_dir=tmp_path,
                skip_state_snapshot=True,
                scenario_path=tmp_path / "nonexistent.yaml",
            )
            artifacts = run_confirm(inv)

        assert artifacts.exit_code == 4


class TestNoStateSnapshot:
    def test_skip_state_snapshot_not_called(self, tmp_path):
        """When skip_state_snapshot=True, vm_state.collect must NOT be called."""
        scenario = make_scenario_model()
        issue = make_issue_context()

        mock_collect = MagicMock()

        mock_vnc = MagicMock()
        mock_vnc.__enter__ = MagicMock(return_value=mock_vnc)
        mock_vnc.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(_ic_mod, "run_scenario_via_runner",
                         return_value=make_bug_result("BUG_CONFIRMED")),
            patch.object(_ic_mod, "_build_runner_for_vm",
                         return_value=(MagicMock(), MagicMock(), mock_vnc)),
            patch("automation.issue_fetch.fetch_issue", return_value=issue),
            patch("automation.config.load_config", return_value=make_project_config()),
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


class TestRefreshIssueContext:
    def test_refresh_flag_passed(self, tmp_path):
        """refresh_issue_context=True must reach fetch_issue(refresh=True)."""
        scenario = make_scenario_model()
        issue = make_issue_context()

        mock_fetch = MagicMock(return_value=issue)

        mock_vnc = MagicMock()
        mock_vnc.__enter__ = MagicMock(return_value=mock_vnc)
        mock_vnc.__exit__ = MagicMock(return_value=False)

        with (
            patch("automation.issue_fetch.fetch_issue", mock_fetch),
            patch.object(_ic_mod, "run_scenario_via_runner",
                         return_value=make_bug_result("BUG_CONFIRMED")),
            patch.object(_ic_mod, "_build_runner_for_vm",
                         return_value=(MagicMock(), MagicMock(), mock_vnc)),
            patch("automation.config.load_config", return_value=make_project_config()),
            patch("automation.runners.vm_state.collect", return_value=make_vm_state()),
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
