"""Tests for render() output in automation/reporting/issue_report.py.

Extracted from test_issue_report.py to keep that file under 500 LOC.
Covers:
- render() produces expected content for report_md (TestRenderReportMd)
- comment.md contains no internal paths (TestRenderCommentMd)
- summary.json is valid JSON with correct fields (TestRenderSummaryJson)

Imports module setup from test_issue_report to avoid duplicate
sys.modules manipulation in the same pytest process.
"""

import json
from pathlib import Path

import pytest

# Import shared setup from test_issue_report — runs their module-level
# loader once; avoids re-exec of issue_report.py in the same process.
from tests.test_issue_report import (
    render,
    ActualEnv,
    RenderedReport,
    FIXED_RUN_ID,
    FIXED_GIT_REV,
    FIXED_VM,
    FIXED_REPRO_CMD,
    _make_result,
    _confirmed,
    _not_visible,
    _inconclusive,
    _make_issue,
    _make_scenario,
    _make_actual_env,
)


# ---------------------------------------------------------------------------
# render() — report_md content
# ---------------------------------------------------------------------------

class TestRenderReportMd:
    def setup_method(self):
        self.issue = _make_issue()
        self.scenario = _make_scenario()
        self.actual_env = _make_actual_env()
        self.iters = [_confirmed(), _confirmed(), _confirmed()]

    def _do_render(self, mode="confirm") -> RenderedReport:
        return render(
            issue=self.issue,
            scenario=self.scenario,
            mode=mode,
            iterations=self.iters,
            actual_env=self.actual_env,
            run_id=FIXED_RUN_ID,
            vm_name=FIXED_VM,
            git_rev=FIXED_GIT_REV,
            repro_command=FIXED_REPRO_CMD,
        )

    def test_contains_verdict_emoji(self):
        result = self._do_render()
        assert "✅" in result.report_md

    def test_contains_issue_title(self):
        result = self._do_render()
        assert "Screen share picker opens on every app launch" in result.report_md

    def test_contains_suspected_pr(self):
        result = self._do_render()
        assert "3266" in result.report_md

    def test_contains_env_table_rows(self):
        result = self._do_render()
        # OS row should show Fedora 43 (reporter) vs Fedora 42 (actual)
        assert "Fedora 43" in result.report_md
        assert "Fedora 42" in result.report_md
        # Ozone mismatch row
        assert "wayland" in result.report_md
        assert "x11" in result.report_md

    def test_env_match_symbol_partial_os(self):
        # Fedora 43 vs Fedora 42 — differ by 1 major → ≈
        result = self._do_render()
        assert "≈" in result.report_md

    def test_env_mismatch_ozone(self):
        # wayland vs x11 → ✗
        result = self._do_render()
        assert "✗" in result.report_md

    def test_install_note_absent_when_same(self):
        # Both expected and actual are flatpak → no note
        result = self._do_render()
        assert "install method differs" not in result.report_md

    def test_install_note_present_when_different(self):
        env = _make_actual_env()
        env.install = "rpm"
        result = render(
            issue=self.issue,
            scenario=self.scenario,
            mode="confirm",
            iterations=self.iters,
            actual_env=env,
            run_id=FIXED_RUN_ID,
            vm_name=FIXED_VM,
            git_rev=FIXED_GIT_REV,
            repro_command=FIXED_REPRO_CMD,
        )
        assert "install method differs" in result.report_md
        assert "flatpak" in result.report_md
        assert "rpm" in result.report_md

    def test_verdict_detail_table_has_three_rows(self):
        result = self._do_render()
        # Count | 1 | ... rows
        data_rows = [l for l in result.report_md.splitlines()
                     if l.startswith("| ") and not l.startswith("| Iter") and not l.startswith("|---")]
        # At least the 3 iter rows in the verdict table
        assert len(data_rows) >= 3

    def test_contains_smoking_gun_image(self):
        result = self._do_render()
        assert "iter-1/bug-signal.png" in result.report_md

    def test_contains_git_rev(self):
        result = self._do_render()
        assert FIXED_GIT_REV in result.report_md

    def test_contains_repro_command(self):
        result = self._do_render()
        assert FIXED_REPRO_CMD in result.report_md

    def test_run_id_in_header(self):
        result = self._do_render()
        assert FIXED_RUN_ID in result.report_md

    def test_mode_confirm_footer(self):
        result = self._do_render(mode="confirm")
        assert "verify-fix" in result.report_md

    def test_mode_verify_fix_verdict_text(self):
        # For verify-fix mode, NOT_REPRODUCED (all not_visible) → "Fix holds"
        iters = [_not_visible(), _not_visible(), _not_visible()]
        result = render(
            issue=self.issue,
            scenario=self.scenario,
            mode="verify-fix",
            iterations=iters,         # all not_visible → NOT_REPRODUCED
            actual_env=self.actual_env,
            run_id=FIXED_RUN_ID,
            vm_name=FIXED_VM,
            git_rev=FIXED_GIT_REV,
            repro_command=FIXED_REPRO_CMD,
        )
        assert "Fix holds" in result.report_md, (
            f"Expected 'Fix holds' in verify-fix NOT_REPRODUCED report. "
            f"Verdict line: {[l for l in result.report_md.splitlines() if 'Fix' in l or 'Regression' in l]}"
        )

    def test_mode_verify_fix_regression_text(self):
        # For verify-fix mode, CONFIRMED → "Regression"
        result = render(
            issue=self.issue,
            scenario=self.scenario,
            mode="verify-fix",
            iterations=self.iters,
            actual_env=self.actual_env,
            run_id=FIXED_RUN_ID,
            vm_name=FIXED_VM,
            git_rev=FIXED_GIT_REV,
            repro_command=FIXED_REPRO_CMD,
        )
        assert "Regression" in result.report_md


# ---------------------------------------------------------------------------
# render() — comment.md
# ---------------------------------------------------------------------------

class TestRenderCommentMd:
    def setup_method(self):
        self.issue = _make_issue()
        self.scenario = _make_scenario()
        self.actual_env = _make_actual_env()
        self.iters = [_confirmed(), _confirmed(), _confirmed()]

    def _do_render(self, mode="confirm") -> RenderedReport:
        return render(
            issue=self.issue,
            scenario=self.scenario,
            mode=mode,
            iterations=self.iters,
            actual_env=self.actual_env,
            run_id=FIXED_RUN_ID,
            vm_name=FIXED_VM,
            git_rev=FIXED_GIT_REV,
            repro_command=FIXED_REPRO_CMD,
        )

    def test_no_internal_results_path(self):
        result = self._do_render()
        # Internal paths like results/issues/ must not appear in comment.md
        assert "results/issues/" not in result.comment_md

    def test_no_run_id_in_comment(self):
        result = self._do_render()
        # run_id is an internal detail — should not appear in comment
        assert FIXED_RUN_ID not in result.comment_md

    def test_contains_verdict_emoji(self):
        result = self._do_render()
        assert "✅" in result.comment_md

    def test_contains_reproducibility_line(self):
        result = self._do_render()
        assert "3/3" in result.comment_md

    def test_contains_screenshot_hint(self):
        result = self._do_render()
        assert "iter-1/bug-signal.png" in result.comment_md

    def test_contains_mosdat_link(self):
        result = self._do_render()
        assert "mosdat" in result.comment_md

    def test_contains_git_rev(self):
        result = self._do_render()
        assert FIXED_GIT_REV in result.comment_md

    def test_comment_mode_line_confirm(self):
        result = self._do_render(mode="confirm")
        assert "Reproduced via mosdat confirm" in result.comment_md

    def test_comment_mode_line_verify_fix(self):
        iters = [_not_visible()] * 3
        result = render(
            issue=self.issue,
            scenario=self.scenario,
            mode="verify-fix",
            iterations=iters,
            actual_env=self.actual_env,
            run_id=FIXED_RUN_ID,
            vm_name=FIXED_VM,
            git_rev=FIXED_GIT_REV,
            repro_command=FIXED_REPRO_CMD,
        )
        assert "--mode=verify-fix" in result.comment_md


# ---------------------------------------------------------------------------
# render() — summary.json
# ---------------------------------------------------------------------------

class TestRenderSummaryJson:
    def setup_method(self):
        self.issue = _make_issue()
        self.scenario = _make_scenario()
        self.actual_env = _make_actual_env()
        self.iters = [_confirmed(), _confirmed(), _confirmed()]

    def _do_render(self) -> RenderedReport:
        return render(
            issue=self.issue,
            scenario=self.scenario,
            mode="confirm",
            iterations=self.iters,
            actual_env=self.actual_env,
            run_id=FIXED_RUN_ID,
            vm_name=FIXED_VM,
            git_rev=FIXED_GIT_REV,
            repro_command=FIXED_REPRO_CMD,
        )

    def test_is_valid_json(self):
        result = self._do_render()
        parsed = json.loads(result.summary_json)
        assert isinstance(parsed, dict)

    def test_contains_verdict(self):
        result = self._do_render()
        parsed = json.loads(result.summary_json)
        assert parsed["verdict"] == "CONFIRMED"

    def test_contains_counts(self):
        result = self._do_render()
        parsed = json.loads(result.summary_json)
        assert parsed["iterations_confirmed"] == 3
        assert parsed["iterations_not_visible"] == 0
        assert parsed["iterations_inconclusive"] == 0
        assert parsed["iterations_total"] == 3

    def test_contains_issue_id_and_url(self):
        result = self._do_render()
        parsed = json.loads(result.summary_json)
        assert parsed["issue_id"] == "3308"
        assert "3308" in parsed["issue_url"]

    def test_contains_vm_and_run_id(self):
        result = self._do_render()
        parsed = json.loads(result.summary_json)
        assert parsed["vm"] == FIXED_VM
        assert parsed["run_id"] == FIXED_RUN_ID

    def test_contains_git_rev(self):
        result = self._do_render()
        parsed = json.loads(result.summary_json)
        assert parsed["git_rev"] == FIXED_GIT_REV

    def test_env_match_structure(self):
        result = self._do_render()
        parsed = json.loads(result.summary_json)
        env_match = parsed["env_match"]
        assert isinstance(env_match, dict)
        # install=flatpak matches → True
        assert env_match["install"] is True
        # app_version=4.14.0 matches → True
        assert env_match["app_version"] is True
        # ozone=wayland (expected) vs x11 (actual) → False
        assert env_match["ozone"] is False

    def test_elapsed_ms_is_sum(self):
        result = self._do_render()
        parsed = json.loads(result.summary_json)
        assert parsed["elapsed_ms_total"] == 3 * 12000
