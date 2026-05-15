"""Tests for dashboard state (build_dashboard_state) driven from live_dashboard context."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Module loader (ensures automation.live_dashboard is registered before
# importing automation.dashboard_state, which is the same pattern used in
# the original monolithic file)
# ---------------------------------------------------------------------------
_PROJ = Path(__file__).parent.parent


def _load_live():
    path = _PROJ / "automation" / "live_dashboard.py"
    spec = importlib.util.spec_from_file_location("automation.live_dashboard", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    mod.__package__ = "automation"
    sys.modules.setdefault("automation.live_dashboard", mod)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_load_live()
from automation.dashboard_state import build_dashboard_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for evt in events:
            fh.write(json.dumps(evt) + "\n")


# ---------------------------------------------------------------------------
# DashboardState tests
# ---------------------------------------------------------------------------

class TestDashboardState:
    def test_recording_artifacts_exposed(self, tmp_path):
        run_dir = tmp_path / "functional" / "run1" / "fedora" / "recording"
        run_dir.mkdir(parents=True)
        (run_dir / "session.mp4").write_bytes(b"mp4")
        (run_dir / "session.gif").write_bytes(b"gif")

        state = build_dashboard_state(tmp_path, now=datetime(2026, 5, 3, 10, 0, 10))
        vm = state["runs"][0]["vms"][0]
        assert vm["recording"]["mp4"]["url"] == "/artifact/run1/fedora/recording/session.mp4"
        assert vm["recording"]["gif"]["url"] == "/artifact/run1/fedora/recording/session.gif"

    def test_failure_summary_includes_vlm_and_screenshot(self, tmp_path):
        run_dir = tmp_path / "functional" / "run1" / "fedora"
        run_dir.mkdir(parents=True)
        (run_dir / "120000_step2_verify_poll.png").write_bytes(b"png")
        _write_events(run_dir / "events.jsonl", [
            {"ts": "2026-05-03T10:00:00", "event": "step_start", "step_num": 2, "kind": "verify"},
            {"ts": "2026-05-03T10:00:02", "event": "vlm_verify", "step_num": 2, "question": "logged in?", "answer": "no"},
            {"ts": "2026-05-03T10:00:05", "event": "step_end", "step_num": 2, "status": "failed", "duration_ms": 5000, "attempts": 3},
        ])

        state = build_dashboard_state(tmp_path, now=datetime(2026, 5, 3, 10, 0, 10))

        vm = state["runs"][0]["vms"][0]
        assert vm["status"] == "fail"
        assert vm["latest_screenshot"]["url"] == "/png/run1/fedora/120000_step2_verify_poll.png"
        assert state["failures"][0]["cause"] == "VLM said no"
        assert state["failures"][0]["question"] == "logged in?"
        assert state["failures"][0]["screenshot"]["url"] == "/png/run1/fedora/120000_step2_verify_poll.png"

    def test_runs_sorted_by_latest_event(self, tmp_path):
        old_dir = tmp_path / "functional" / "old-run" / "ubuntu"
        new_dir = tmp_path / "functional" / "new-run" / "ubuntu"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        _write_events(old_dir / "events.jsonl", [
            {"ts": "2026-05-03T09:00:00", "event": "step_start", "step_num": 1},
        ])
        _write_events(new_dir / "events.jsonl", [
            {"ts": "2026-05-03T10:00:00", "event": "step_start", "step_num": 1},
        ])

        state = build_dashboard_state(tmp_path, now=datetime(2026, 5, 3, 10, 1, 0))

        assert state["runs"][0]["name"] == "new-run"
        assert state["runs"][0]["age_seconds"] == 60

    def test_stale_classification(self, tmp_path):
        run_dir = tmp_path / "functional" / "run1" / "opensuse"
        run_dir.mkdir(parents=True)
        _write_events(run_dir / "events.jsonl", [
            {"ts": "2026-05-03T10:00:00", "event": "step_start", "step_num": 1, "kind": "launch"},
        ])

        state = build_dashboard_state(
            tmp_path,
            warn_after=90,
            stale_after=180,
            now=datetime(2026, 5, 3, 10, 4, 0),
        )

        assert state["runs"][0]["vms"][0]["status"] == "stale"
        assert state["runs"][0]["vms"][0]["duration_seconds"] == 240

    def test_skipped_step_does_not_make_run_fail(self, tmp_path):
        run_dir = tmp_path / "functional" / "run1" / "fedora"
        run_dir.mkdir(parents=True)
        _write_events(run_dir / "events.jsonl", [
            {"ts": "2026-05-03T10:00:00", "event": "step_start", "step_num": 1, "kind": "if_visible"},
            {"ts": "2026-05-03T10:00:01", "event": "vlm_verify", "step_num": 1, "question": "banner?", "answer": "no"},
            {"ts": "2026-05-03T10:00:02", "event": "step_end", "step_num": 1, "status": "skipped"},
            {"ts": "2026-05-03T10:00:03", "event": "step_start", "step_num": 2, "kind": "verify"},
            {"ts": "2026-05-03T10:00:04", "event": "step_end", "step_num": 2, "status": "ok"},
        ])

        state = build_dashboard_state(tmp_path, now=datetime(2026, 5, 3, 10, 0, 5))

        vm = state["runs"][0]["vms"][0]
        assert vm["status"] == "pass"
        assert vm["steps"][0]["status"] == "skipped"
        assert state["failures"] == []

    def test_screenshot_only_failure_is_not_running(self, tmp_path):
        run_dir = tmp_path / "functional" / "old-run" / "windows11"
        run_dir.mkdir(parents=True)
        (run_dir / "204747_step1_fail_attempt1.png").write_bytes(b"png")
        (run_dir / "204800_step1_final_fail.png").write_bytes(b"png")

        state = build_dashboard_state(tmp_path, now=datetime(2026, 5, 3, 10, 0, 0))

        vm = state["runs"][0]["vms"][0]
        assert vm["status"] == "fail"
        assert vm["steps"][0]["status"] == "fail"
        assert state["failures"][0]["cause"] == "screenshot-only failure"
        assert state["failures"][0]["screenshot"]["url"] == "/png/old-run/windows11/204800_step1_final_fail.png"
