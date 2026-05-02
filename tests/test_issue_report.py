"""Tests for automation/reporting/issue_report.py (Phase D).

Covers:
- aggregate_verdict correctness for all specified combinations
- render() produces expected content for report_md / comment_md / summary_json
- mode=verify-fix flips verdict-line text
- comment.md contains no internal paths
- summary.json is valid JSON with correct fields
- Snapshot test against tests/fixtures/issue_report_3308_confirm_expected.md
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest


# Load automation.reporting.issue_report by file path to bypass
# automation/reporting/__init__.py's eager `from .report import generate_report`,
# which other tests (test_dashboard, test_negative) sys-modules-stub in ways
# that break the eager-init when this test runs after them in the full suite.
_PROJ = Path(__file__).resolve().parent.parent
_ir_path = _PROJ / "automation" / "reporting" / "issue_report.py"
_ir_spec = importlib.util.spec_from_file_location(
    "automation.reporting.issue_report", _ir_path
)
_ir_mod = importlib.util.module_from_spec(_ir_spec)
sys.modules["automation.reporting.issue_report"] = _ir_mod
_ir_spec.loader.exec_module(_ir_mod)

ActualEnv = _ir_mod.ActualEnv
AggregateVerdict = _ir_mod.AggregateVerdict
RenderedReport = _ir_mod.RenderedReport
aggregate_verdict = _ir_mod.aggregate_verdict
render = _ir_mod.render

from automation.runners.functional import BugConfirmationResult
from automation.issue_fetch import IssueContext
from automation.scenario import ScenarioModel


# ---------------------------------------------------------------------------
# Fixtures — frozen inputs for deterministic rendering
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"

FIXED_RUN_ID = "2026-05-02_191002"
FIXED_GIT_REV = "1597085"
FIXED_VM = "fedora42"
FIXED_REPRO_CMD = "mosdat confirm 3308 --vm fedora42 --iterations 3 --mode confirm"


def _make_result(precondition_met: bool, bug_visible: bool, elapsed_ms: int = 12000) -> BugConfirmationResult:
    """Construct a BugConfirmationResult with minimal required fields."""
    return BugConfirmationResult(
        precondition_met=precondition_met,
        bug_visible=bug_visible,
        bug_signal_screenshot=None,
        precondition_screenshot=None,
        final_screenshot=Path("iter-1/final.png"),
        step_failures=[],
        elapsed_ms=elapsed_ms,
    )


def _confirmed() -> BugConfirmationResult:
    return _make_result(True, True)


def _not_visible() -> BugConfirmationResult:
    return _make_result(True, False)


def _inconclusive() -> BugConfirmationResult:
    return _make_result(False, False)


def _make_issue() -> IssueContext:
    return IssueContext(
        id="3308",
        url="https://github.com/RocketChat/Rocket.Chat.Electron/issues/3308",
        title="Screen share picker opens on every app launch",
        labels=["type: bug"],
        suspected_prs=["3266"],
        linked_prs=["3313"],
        body_markdown="Reporter: Fedora 43 Flatpak RC 4.14.0 Wayland.",
        fetched_at="2026-05-02T19:10:00Z",
    )


def _make_scenario_yaml() -> str:
    return """
name: rocketchat-3308-screenshare-picker
description: |
  1. Clean RC state (wipe config)
  2. Launch RC via GNOME Activities
  3. Verify RC window opens
  4. Check if screen-share picker appears unprompted
kind: bug-confirmation
bug_signal: "A screen-share picker dialog with text mentioning 'share your screen' and Cancel/Share buttons is visible"
precondition_check: "The Rocket.Chat main window is visible (server-URL form, login form, or chat UI)"
issue:
  id: "3308"
  url: "https://github.com/RocketChat/Rocket.Chat.Electron/issues/3308"
  title: "Screen share picker opens on every app launch"
  suspected_pr: "3266"
  reporter_version: "4.14.0"
expected_env:
  ozone: wayland
  display_server: wayland
  install: flatpak
  app_version: "4.14.0"
  os: "Fedora 43"
steps:
  - shell: "rm -rf ~/.config/Rocket.Chat*"
  - key: super
  - then_type: "Rocket.Chat"
  - key: return
"""


def _make_scenario() -> ScenarioModel:
    import yaml
    data = yaml.safe_load(_make_scenario_yaml())
    return ScenarioModel.model_validate(data)


def _make_actual_env() -> ActualEnv:
    return ActualEnv(
        ozone="x11",
        display_server="wayland",
        install="flatpak",
        app_version="4.14.0",
        os="Fedora 42",
        vm_name=FIXED_VM,
        process_cmdline="rocketchat-desktop.bin --disable-gpu --ozone-platform=x11",
        config_view="add-new-server",
    )


# ---------------------------------------------------------------------------
# aggregate_verdict tests
# ---------------------------------------------------------------------------

class TestAggregateVerdict:
    def test_all_confirmed(self):
        iters = [_confirmed(), _confirmed(), _confirmed()]
        assert aggregate_verdict(iters) == "CONFIRMED"

    def test_all_not_visible(self):
        iters = [_not_visible(), _not_visible(), _not_visible()]
        assert aggregate_verdict(iters) == "NOT_REPRODUCED"

    def test_mixed_confirmed_and_not_visible_is_intermittent(self):
        iters = [_confirmed(), _confirmed(), _not_visible()]
        assert aggregate_verdict(iters) == "INTERMITTENT"

    def test_one_confirmed_two_inconclusive_is_inconclusive(self):
        # 1 conclusive < min_conclusive=3 → INCONCLUSIVE
        iters = [_confirmed(), _inconclusive(), _inconclusive()]
        assert aggregate_verdict(iters) == "INCONCLUSIVE"

    def test_all_inconclusive_is_harness_error(self):
        iters = [_inconclusive(), _inconclusive(), _inconclusive()]
        assert aggregate_verdict(iters) == "HARNESS_ERROR"

    def test_five_iterations_mixed(self):
        # 3 confirmed + 2 not_visible = INTERMITTENT (all conclusive)
        iters = [_confirmed(), _confirmed(), _confirmed(), _not_visible(), _not_visible()]
        assert aggregate_verdict(iters) == "INTERMITTENT"

    def test_five_all_confirmed(self):
        iters = [_confirmed()] * 5
        assert aggregate_verdict(iters) == "CONFIRMED"

    def test_four_conclusive_from_five_meets_min(self):
        # 4 conclusive >= min_conclusive=3 → proceeds to check mix
        iters = [_confirmed(), _confirmed(), _confirmed(), _confirmed(), _inconclusive()]
        assert aggregate_verdict(iters) == "CONFIRMED"

    def test_two_conclusive_below_min_is_inconclusive(self):
        iters = [_confirmed(), _not_visible(), _inconclusive()]
        # conclusive=2 < min_conclusive=3 → INCONCLUSIVE
        assert aggregate_verdict(iters) == "INCONCLUSIVE"

    def test_custom_min_conclusive(self):
        iters = [_confirmed(), _confirmed()]
        assert aggregate_verdict(iters, min_conclusive=2) == "CONFIRMED"


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


# ---------------------------------------------------------------------------
# RenderedReport dataclass fields
# ---------------------------------------------------------------------------

class TestRenderedReportFields:
    def test_verdict_field_matches_aggregate(self):
        iters = [_confirmed()] * 3
        result = render(
            issue=_make_issue(),
            scenario=_make_scenario(),
            mode="confirm",
            iterations=iters,
            actual_env=_make_actual_env(),
            run_id=FIXED_RUN_ID,
            vm_name=FIXED_VM,
            git_rev=FIXED_GIT_REV,
            repro_command=FIXED_REPRO_CMD,
        )
        assert result.verdict == "CONFIRMED"
        assert result.confirmed_count == 3
        assert result.not_visible_count == 0
        assert result.inconclusive_count == 0


# ---------------------------------------------------------------------------
# Snapshot test
# ---------------------------------------------------------------------------

SNAPSHOT_PATH = FIXTURE_DIR / "issue_report_3308_confirm_expected.md"


def _render_snapshot() -> str:
    """Render the canonical snapshot fixture."""
    return render(
        issue=_make_issue(),
        scenario=_make_scenario(),
        mode="confirm",
        iterations=[
            _make_result(True, True, elapsed_ms=12000),
            _make_result(True, True, elapsed_ms=13500),
            _make_result(True, True, elapsed_ms=11800),
        ],
        actual_env=_make_actual_env(),
        run_id=FIXED_RUN_ID,
        vm_name=FIXED_VM,
        git_rev=FIXED_GIT_REV,
        repro_command=FIXED_REPRO_CMD,
    ).report_md


def test_snapshot_report_md():
    """Snapshot test: rendered report_md must match the committed fixture.

    To update the fixture (intentional format change):
        python -c "
        import sys; sys.path.insert(0, '.')
        from tests.test_issue_report import _render_snapshot, SNAPSHOT_PATH
        SNAPSHOT_PATH.write_text(_render_snapshot())
        print('Fixture updated.')
        "
    """
    if not SNAPSHOT_PATH.exists():
        # First run: write the fixture and pass
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(_render_snapshot())
        pytest.skip("Fixture created on first run — re-run to validate")

    expected = SNAPSHOT_PATH.read_text()
    actual = _render_snapshot()
    assert actual == expected, (
        "report_md differs from snapshot. "
        "If this is an intentional format change, update the fixture:\n"
        "  python -c \"from tests.test_issue_report import _render_snapshot, SNAPSHOT_PATH; "
        "SNAPSHOT_PATH.write_text(_render_snapshot())\""
    )
