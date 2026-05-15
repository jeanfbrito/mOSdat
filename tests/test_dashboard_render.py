"""Tests for dashboard HTML rendering.

Covers:
  - render_dashboard: output file creation, HTML structure, section IDs,
    chart.js reference, VM name inclusion, empty aggregates, deep parent creation
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

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

        for section_id in ("pass-rate-trend", "retry-rate", "duration", "recordings", "regressions"):
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
