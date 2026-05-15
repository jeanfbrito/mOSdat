"""Tests for confirm/verify-fix/intermittent/harness-error verdict aggregation
in automation/issue_confirm.py.

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
    confirm_patches,
    make_bug_result,
    make_issue_context,
    make_scenario_model,
    run_confirm,
)


class TestConfirmMode:
    def test_confirmed_3_of_3(self, tmp_path):
        scenario = make_scenario_model()
        issue = make_issue_context()

        with confirm_patches(scenario, issue, ["BUG_CONFIRMED"] * 3):
            inv = ConfirmInvocation(
                issue_id_or_url="3308",
                vm_name="fedora42",
                iterations=3,
                mode="confirm",
                output_dir=tmp_path,
                skip_state_snapshot=False,
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
        assert artifacts.exit_code == 0
        assert len(artifacts.iter_dirs) == 3


class TestVerifyFixMode:
    def test_fix_holds_not_reproduced(self, tmp_path):
        """3/3 BUG_NOT_VISIBLE in verify-fix → exit 0."""
        scenario = make_scenario_model()
        issue = make_issue_context()

        with confirm_patches(scenario, issue, ["BUG_NOT_VISIBLE"] * 3):
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
        scenario = make_scenario_model()
        issue = make_issue_context()

        with confirm_patches(scenario, issue, ["BUG_CONFIRMED"] * 3):
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


class TestIntermittent:
    def test_mixed_iters_exit2(self, tmp_path):
        """Mixed BUG_CONFIRMED + BUG_NOT_VISIBLE → INTERMITTENT → exit 2."""
        scenario = make_scenario_model()
        issue = make_issue_context()

        with confirm_patches(
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


class TestHarnessError:
    def test_all_inconclusive_exit3(self, tmp_path):
        scenario = make_scenario_model()
        issue = make_issue_context()

        with confirm_patches(scenario, issue, ["INCONCLUSIVE"] * 3):
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
