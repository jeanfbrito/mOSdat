"""Tests for automation/reporting/dashboard.py (H2.2).

Covers:
  - aggregate_runs: pass-rate, retry-count, mean-duration aggregation
  - detect_regressions: threshold behaviour
  - render_dashboard: HTML output non-empty and contains expected sections
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

_PROJ = Path(__file__).parent.parent


def _load_dashboard():
    """Load dashboard module directly to avoid the broken __init__ chain."""
    path = _PROJ / "automation" / "reporting" / "dashboard.py"
    spec = importlib.util.spec_from_file_location("automation.reporting.dashboard", path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "automation.reporting"
    sys.modules["automation.reporting.dashboard"] = mod
    spec.loader.exec_module(mod)
    return mod


dashboard = _load_dashboard()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _make_run(
    functional_dir: Path,
    run_id: str,
    vm_name: str,
    steps: list[dict],
) -> None:
    """Write a synthetic events.jsonl for one run/VM."""
    events_path = functional_dir / run_id / vm_name / "events.jsonl"
    _write_events(events_path, steps)


def _step_events(
    step_num: int,
    label: str,
    status: str = "passed",
    attempts: int = 1,
    duration_s: float = 1.0,
    ts_base: float = 1_700_000_000.0,
) -> list[dict]:
    return [
        {"event": "step_start", "step_num": step_num, "label": label, "ts": ts_base},
        {"event": "step_end", "step_num": step_num, "label": label,
         "status": status, "attempts": attempts, "ts": ts_base + duration_s},
    ]


# ---------------------------------------------------------------------------
# aggregate_runs
# ---------------------------------------------------------------------------

class TestAggregateRuns:
    def test_empty_functional_dir_returns_empty(self, tmp_path: Path):
        agg = dashboard.aggregate_runs(tmp_path)
        assert agg["runs"] == []
        assert agg["per_vm"] == {}
        assert agg["per_step"] == {}

    def test_no_functional_subdir(self, tmp_path: Path):
        agg = dashboard.aggregate_runs(tmp_path)
        assert agg["runs"] == []

    def test_single_run_single_vm_pass_rate(self, tmp_path: Path):
        functional = tmp_path / "functional"
        # 3 steps: 2 pass, 1 fail -> pass rate = 2/3
        events = (
            _step_events(1, "login", "passed", 1, 1.0)
            + _step_events(2, "send message", "passed", 1, 1.5)
            + _step_events(3, "upload", "failed", 2, 2.0)
        )
        _make_run(functional, "2026-04-01_functional", "ubuntu-vm", events)

        agg = dashboard.aggregate_runs(tmp_path)

        assert len(agg["runs"]) == 1
        assert "ubuntu-vm" in agg["per_vm"]
        run_info = agg["per_vm"]["ubuntu-vm"]["2026-04-01_functional"]
        assert run_info["passed"] == 2
        assert run_info["total"] == 3
        assert abs(run_info["pass_rate"] - 2 / 3) < 1e-9

    def test_retry_count_aggregated_per_step(self, tmp_path: Path):
        functional = tmp_path / "functional"
        # step "login" appears in two runs: attempts=[1, 3]
        events_r1 = _step_events(1, "login", "passed", attempts=1)
        events_r2 = _step_events(1, "login", "passed", attempts=3)
        _make_run(functional, "2026-04-01_functional", "vm1", events_r1)
        _make_run(functional, "2026-04-02_functional", "vm1", events_r2)

        agg = dashboard.aggregate_runs(tmp_path)

        login_stats = agg["per_step"]["login"]
        assert login_stats["attempts"] == [1, 3]
        # 0 retries for run1 (attempts=1), 2 retries for run2 (attempts=3)
        total_retries = sum(max(0, a - 1) for a in login_stats["attempts"])
        assert total_retries == 2

    def test_mean_duration_computed(self, tmp_path: Path):
        functional = tmp_path / "functional"
        events = (
            _step_events(1, "click button", "passed", duration_s=4.0)
            + _step_events(2, "click button", "passed", duration_s=6.0,
                           ts_base=1_700_000_100.0)
        )
        # Two occurrences of same step label in same run
        _make_run(functional, "2026-04-01_functional", "vm1", events)

        agg = dashboard.aggregate_runs(tmp_path)

        stats = agg["per_step"]["click button"]
        assert len(stats["durations_s"]) == 2
        from statistics import mean
        assert abs(mean(stats["durations_s"]) - 5.0) < 1e-6

    def test_multiple_vms_tracked_separately(self, tmp_path: Path):
        functional = tmp_path / "functional"
        # vm1: 1 pass; vm2: 0 pass (1 fail)
        _make_run(functional, "2026-04-01_functional", "vm1",
                  _step_events(1, "step", "passed"))
        _make_run(functional, "2026-04-01_functional", "vm2",
                  _step_events(1, "step", "failed"))

        agg = dashboard.aggregate_runs(tmp_path)

        assert agg["per_vm"]["vm1"]["2026-04-01_functional"]["pass_rate"] == 1.0
        assert agg["per_vm"]["vm2"]["2026-04-01_functional"]["pass_rate"] == 0.0

    def test_malformed_json_line_skipped_gracefully(self, tmp_path: Path, recwarn):
        functional = tmp_path / "functional"
        events_path = functional / "2026-04-01_functional" / "vm1" / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps({"event": "step_start", "step_num": 1, "label": "x", "ts": 1.0}),
            "NOT JSON {{{",
            json.dumps({"event": "step_end", "step_num": 1, "label": "x",
                        "status": "passed", "attempts": 1, "ts": 2.0}),
        ]
        events_path.write_text("\n".join(lines) + "\n")

        agg = dashboard.aggregate_runs(tmp_path)
        # Should still parse the valid lines
        assert agg["per_step"]["x"]["passed"] == 1

    def test_run_ids_sorted_chronologically(self, tmp_path: Path):
        functional = tmp_path / "functional"
        for date in ("2026-04-03_functional", "2026-04-01_functional", "2026-04-02_functional"):
            _make_run(functional, date, "vm1", _step_events(1, "s", "passed"))

        agg = dashboard.aggregate_runs(tmp_path)
        run_ids = [r["run_id"] for r in agg["runs"]]
        assert run_ids == sorted(run_ids)


# ---------------------------------------------------------------------------
# detect_regressions
# ---------------------------------------------------------------------------

class TestDetectRegressions:
    def _make_aggregates_with_rates(
        self,
        recent_rate: float,
        baseline_rate: float,
        now_ts: float | None = None,
    ) -> dict:
        """Build a minimal aggregates dict with two time windows."""
        if now_ts is None:
            now_ts = datetime.now(tz=timezone.utc).timestamp()

        # recent run: 3 days ago
        recent_ts = now_ts - 3 * 86400
        recent_run_id = "2026-04-28_functional"

        # baseline run: 15 days ago
        base_ts = now_ts - 15 * 86400
        base_run_id = "2026-04-16_functional"

        per_vm = {
            "vm1": {
                recent_run_id: {
                    "pass_rate": recent_rate,
                    "run_ts": datetime.fromtimestamp(recent_ts, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H%M%S"
                    ),
                    "passed": int(recent_rate * 10),
                    "total": 10,
                },
                base_run_id: {
                    "pass_rate": baseline_rate,
                    "run_ts": datetime.fromtimestamp(base_ts, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H%M%S"
                    ),
                    "passed": int(baseline_rate * 10),
                    "total": 10,
                },
            }
        }
        per_step = {
            "some step": {
                "attempts": [1, 1],
                "passed": int(recent_rate * 10) + int(baseline_rate * 10),
                "failed": 0,
                "durations_s": [1.0, 1.0],
                "run_ids": [recent_run_id, base_run_id],
            }
        }
        return {
            "runs": [{"run_id": base_run_id}, {"run_id": recent_run_id}],
            "per_vm": per_vm,
            "per_step": per_step,
            "results_root": "/tmp/results",
        }

    def test_regression_detected_above_threshold(self):
        # baseline=0.90, recent=0.70 -> drop=0.20 > 0.10
        agg = self._make_aggregates_with_rates(recent_rate=0.70, baseline_rate=0.90)
        regs = dashboard.detect_regressions(agg)
        assert len(regs) >= 1
        # The regression should report a positive drop
        assert regs[0]["drop_pp"] > 0.10

    def test_no_regression_below_threshold(self):
        # baseline=0.90, recent=0.85 -> drop=0.05 < 0.10
        agg = self._make_aggregates_with_rates(recent_rate=0.85, baseline_rate=0.90)
        regs = dashboard.detect_regressions(agg)
        assert len(regs) == 0

    def test_no_regression_when_rate_improved(self):
        # recent is better than baseline — not a regression
        agg = self._make_aggregates_with_rates(recent_rate=0.95, baseline_rate=0.70)
        regs = dashboard.detect_regressions(agg)
        assert len(regs) == 0

    def test_empty_aggregates_returns_empty(self):
        agg = {"runs": [], "per_vm": {}, "per_step": {}, "results_root": ""}
        regs = dashboard.detect_regressions(agg)
        assert regs == []

    def test_regression_fields_present(self):
        agg = self._make_aggregates_with_rates(recent_rate=0.50, baseline_rate=0.90)
        regs = dashboard.detect_regressions(agg)
        assert regs, "Expected at least one regression"
        r = regs[0]
        for key in ("step", "recent_rate", "baseline_rate", "drop_pp",
                    "recent_runs", "baseline_runs"):
            assert key in r, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# render_dashboard
# ---------------------------------------------------------------------------

class TestRenderDashboard:
    def _make_minimal_aggregates(self, tmp_path: Path) -> dict:
        functional = tmp_path / "functional"
        events = (
            _step_events(1, "login", "passed", 1, 1.0)
            + _step_events(2, "send message", "failed", 2, 3.0)
        )
        _make_run(functional, "2026-04-01_functional", "ubuntu-vm", events)
        return dashboard.aggregate_runs(tmp_path)

    def test_output_file_created(self, tmp_path: Path):
        agg = self._make_minimal_aggregates(tmp_path)
        out = tmp_path / "dashboard.html"
        dashboard.render_dashboard(agg, out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_html_contains_section_ids(self, tmp_path: Path):
        agg = self._make_minimal_aggregates(tmp_path)
        out = tmp_path / "dashboard.html"
        dashboard.render_dashboard(agg, out)
        content = out.read_text(encoding="utf-8")

        for section_id in ("pass-rate-trend", "retry-rate", "duration", "regressions"):
            assert section_id in content, f"Missing section: {section_id}"

    def test_html_contains_chartjs_script_tag(self, tmp_path: Path):
        agg = self._make_minimal_aggregates(tmp_path)
        out = tmp_path / "dashboard.html"
        dashboard.render_dashboard(agg, out)
        content = out.read_text(encoding="utf-8")
        assert "chart.js" in content.lower() or "Chart" in content

    def test_html_contains_vm_name(self, tmp_path: Path):
        agg = self._make_minimal_aggregates(tmp_path)
        out = tmp_path / "dashboard.html"
        dashboard.render_dashboard(agg, out)
        content = out.read_text(encoding="utf-8")
        assert "ubuntu-vm" in content

    def test_html_valid_structure(self, tmp_path: Path):
        agg = self._make_minimal_aggregates(tmp_path)
        out = tmp_path / "dashboard.html"
        dashboard.render_dashboard(agg, out)
        content = out.read_text(encoding="utf-8")
        assert content.startswith("<!DOCTYPE html>")
        assert "</html>" in content

    def test_empty_aggregates_renders_without_error(self, tmp_path: Path):
        agg = {"runs": [], "per_vm": {}, "per_step": {}, "results_root": str(tmp_path)}
        out = tmp_path / "empty_dashboard.html"
        dashboard.render_dashboard(agg, out)
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content

    def test_output_parent_created_if_missing(self, tmp_path: Path):
        agg = {"runs": [], "per_vm": {}, "per_step": {}, "results_root": str(tmp_path)}
        out = tmp_path / "deep" / "nested" / "dashboard.html"
        assert not out.parent.exists()
        dashboard.render_dashboard(agg, out)
        assert out.exists()
