"""Tests for dashboard data aggregation and regression detection.

Covers:
  - aggregate_runs: pass-rate, retry-count, mean-duration aggregation
  - detect_regressions: threshold behaviour and extended params
  - build_regression_summary: summary message formatting
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
# detect_regressions (extended — I6 params)
# ---------------------------------------------------------------------------

class TestDetectRegressionsExtended:
    """Tests for threshold_multiplier and min_pass_rate params added in I6."""

    def _make_agg_with_durations(
        self,
        recent_mean_s: float,
        baseline_mean_s: float,
        now_ts: float | None = None,
    ) -> dict:
        """Build aggregates with duration data in two time windows."""
        if now_ts is None:
            now_ts = datetime.now(tz=timezone.utc).timestamp()

        recent_run_id = "2026-04-28_functional"
        base_run_id = "2026-04-16_functional"
        recent_ts = now_ts - 3 * 86400
        base_ts = now_ts - 15 * 86400

        per_vm = {
            "vm1": {
                recent_run_id: {
                    "pass_rate": 1.0,
                    "run_ts": datetime.fromtimestamp(recent_ts, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H%M%S"
                    ),
                    "passed": 10,
                    "total": 10,
                },
                base_run_id: {
                    "pass_rate": 1.0,
                    "run_ts": datetime.fromtimestamp(base_ts, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H%M%S"
                    ),
                    "passed": 10,
                    "total": 10,
                },
            }
        }
        per_step = {
            "slow step": {
                "attempts": [1, 1],
                "passed": 2,
                "failed": 0,
                "durations_s": [recent_mean_s, baseline_mean_s],
                "run_ids": [recent_run_id, base_run_id],
            }
        }
        return {
            "runs": [{"run_id": base_run_id}, {"run_id": recent_run_id}],
            "per_vm": per_vm,
            "per_step": per_step,
            "results_root": "/tmp/results",
        }

    def _make_agg_with_vm_rate(
        self,
        vm_recent_rate: float,
        now_ts: float | None = None,
    ) -> dict:
        """Build aggregates with a single VM whose recent pass rate is vm_recent_rate."""
        if now_ts is None:
            now_ts = datetime.now(tz=timezone.utc).timestamp()

        recent_run_id = "2026-04-28_functional"
        recent_ts = now_ts - 3 * 86400

        per_vm = {
            "vm-low": {
                recent_run_id: {
                    "pass_rate": vm_recent_rate,
                    "run_ts": datetime.fromtimestamp(recent_ts, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H%M%S"
                    ),
                    "passed": int(vm_recent_rate * 10),
                    "total": 10,
                },
            }
        }
        per_step = {
            "some step": {
                "attempts": [1],
                "passed": int(vm_recent_rate * 10),
                "failed": 10 - int(vm_recent_rate * 10),
                "durations_s": [1.0],
                "run_ids": [recent_run_id],
            }
        }
        return {
            "runs": [{"run_id": recent_run_id}],
            "per_vm": per_vm,
            "per_step": per_step,
            "results_root": "/tmp/results",
        }

    def test_duration_regression_detected_above_multiplier(self):
        # baseline=1.0s, recent=2.0s -> ratio=2.0 >= default 1.5
        agg = self._make_agg_with_durations(recent_mean_s=2.0, baseline_mean_s=1.0)
        regs = dashboard.detect_regressions(agg, threshold_multiplier=1.5)
        dur_regs = [r for r in regs if r.get("kind") == "duration_regression"]
        assert len(dur_regs) >= 1
        assert dur_regs[0]["duration_ratio"] >= 1.5

    def test_duration_regression_not_triggered_below_multiplier(self):
        # baseline=1.0s, recent=1.2s -> ratio=1.2 < 1.5
        agg = self._make_agg_with_durations(recent_mean_s=1.2, baseline_mean_s=1.0)
        regs = dashboard.detect_regressions(agg, threshold_multiplier=1.5)
        dur_regs = [r for r in regs if r.get("kind") == "duration_regression"]
        assert dur_regs == []

    def test_threshold_multiplier_flag_honoured(self):
        # ratio=1.3x — only triggered when multiplier=1.2, not when multiplier=1.5
        agg = self._make_agg_with_durations(recent_mean_s=1.3, baseline_mean_s=1.0)
        regs_strict = dashboard.detect_regressions(agg, threshold_multiplier=1.5)
        regs_loose = dashboard.detect_regressions(agg, threshold_multiplier=1.2)
        assert not [r for r in regs_strict if r.get("kind") == "duration_regression"]
        assert [r for r in regs_loose if r.get("kind") == "duration_regression"]

    def test_vm_pass_rate_below_floor_triggers(self):
        # vm recent rate=0.70 < default floor 0.85
        agg = self._make_agg_with_vm_rate(vm_recent_rate=0.70)
        regs = dashboard.detect_regressions(agg, min_pass_rate=0.85)
        vm_regs = [r for r in regs if r.get("kind") == "vm_pass_rate"]
        assert len(vm_regs) >= 1
        assert vm_regs[0]["vm"] == "vm-low"

    def test_vm_pass_rate_above_floor_no_alert(self):
        # vm recent rate=0.90 >= floor 0.85
        agg = self._make_agg_with_vm_rate(vm_recent_rate=0.90)
        regs = dashboard.detect_regressions(agg, min_pass_rate=0.85)
        vm_regs = [r for r in regs if r.get("kind") == "vm_pass_rate"]
        assert vm_regs == []

    def test_regression_kind_field_present_for_pass_rate_drop(self):
        now_ts = datetime.now(tz=timezone.utc).timestamp()
        recent_ts = now_ts - 3 * 86400
        base_ts = now_ts - 15 * 86400
        per_vm = {
            "vm1": {
                "2026-04-28_functional": {
                    "pass_rate": 0.60,
                    "run_ts": datetime.fromtimestamp(recent_ts, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H%M%S"
                    ),
                    "passed": 6, "total": 10,
                },
                "2026-04-16_functional": {
                    "pass_rate": 0.90,
                    "run_ts": datetime.fromtimestamp(base_ts, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H%M%S"
                    ),
                    "passed": 9, "total": 10,
                },
            }
        }
        per_step = {
            "a step": {
                "attempts": [1, 1], "passed": 15, "failed": 0,
                "durations_s": [1.0, 1.0],
                "run_ids": ["2026-04-28_functional", "2026-04-16_functional"],
            }
        }
        agg = {"runs": [], "per_vm": per_vm, "per_step": per_step, "results_root": ""}
        regs = dashboard.detect_regressions(agg)
        pass_drops = [r for r in regs if r.get("kind") == "pass_rate_drop"]
        assert pass_drops, "Expected a pass_rate_drop regression"
        assert pass_drops[0]["kind"] == "pass_rate_drop"


# ---------------------------------------------------------------------------
# build_regression_summary
# ---------------------------------------------------------------------------

class TestBuildRegressionSummary:
    def test_empty_returns_no_regressions(self):
        msg = dashboard.build_regression_summary([])
        assert "No regressions" in msg

    def test_pass_rate_drop_in_summary(self):
        reg = {
            "kind": "pass_rate_drop", "step": "login", "vm": "",
            "recent_rate": 0.70, "baseline_rate": 0.90, "drop_pp": 0.20,
            "recent_mean_s": 0.0, "baseline_mean_s": 0.0, "duration_ratio": 0.0,
            "recent_runs": 3, "baseline_runs": 5,
        }
        msg = dashboard.build_regression_summary([reg])
        assert "pass-rate drop" in msg
        assert "login" in msg
        assert "20.0pp" in msg

    def test_duration_regression_in_summary(self):
        reg = {
            "kind": "duration_regression", "step": "upload", "vm": "",
            "recent_rate": 0.0, "baseline_rate": 0.0, "drop_pp": 0.0,
            "recent_mean_s": 3.0, "baseline_mean_s": 1.0, "duration_ratio": 3.0,
            "recent_runs": 2, "baseline_runs": 2,
        }
        msg = dashboard.build_regression_summary([reg])
        assert "duration spike" in msg
        assert "upload" in msg
        assert "3.00x" in msg

    def test_vm_pass_rate_in_summary(self):
        reg = {
            "kind": "vm_pass_rate", "step": "", "vm": "fedora-vm",
            "recent_rate": 0.70, "baseline_rate": 0.0, "drop_pp": 0.0,
            "recent_mean_s": 0.0, "baseline_mean_s": 0.0, "duration_ratio": 0.0,
            "recent_runs": 3, "baseline_runs": 0,
        }
        msg = dashboard.build_regression_summary([reg])
        assert "vm pass-rate" in msg
        assert "fedora-vm" in msg
