"""Tests for EventWatcher in automation/live_dashboard.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Module loader
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


live = _load_live()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for evt in events:
            fh.write(json.dumps(evt) + "\n")


# ---------------------------------------------------------------------------
# EventWatcher tests
# ---------------------------------------------------------------------------

class TestEventWatcher:
    def test_detects_new_lines(self, tmp_path):
        """Watcher should emit events for newly appended lines."""
        run_dir = tmp_path / "functional" / "20260501_run" / "ubuntu"
        run_dir.mkdir(parents=True)
        events_file = run_dir / "events.jsonl"

        collected: list[dict] = []

        class CaptureBroadcaster(live.SSEBroadcaster):
            def broadcast_event(self, data):  # type: ignore[override]
                collected.append(data)

        bc = CaptureBroadcaster()
        watcher = live.EventWatcher(tmp_path, bc, refresh_ms=50)

        # Write initial line
        _write_events(events_file, [{"event": "step_start", "step_num": 1}])

        # Poll once
        watcher._poll_once()
        assert any(e["event"] == "step_start" for e in collected)

    def test_does_not_re_emit_already_seen_lines(self, tmp_path):
        run_dir = tmp_path / "functional" / "run1" / "vm1"
        run_dir.mkdir(parents=True)
        events_file = run_dir / "events.jsonl"
        _write_events(events_file, [{"event": "step_start", "step_num": 1}])

        collected: list[dict] = []

        class CaptureBroadcaster(live.SSEBroadcaster):
            def broadcast_event(self, data):  # type: ignore[override]
                collected.append(data)

        bc = CaptureBroadcaster()
        watcher = live.EventWatcher(tmp_path, bc, refresh_ms=50)
        watcher._poll_once()
        first_count = len(collected)
        watcher._poll_once()  # second poll, same mtime — no new events
        assert len(collected) == first_count

    def test_detects_new_png(self, tmp_path):
        run_dir = tmp_path / "functional" / "run1" / "vm1"
        run_dir.mkdir(parents=True)

        screenshot_events: list[dict] = []

        class CaptureBroadcaster(live.SSEBroadcaster):
            def broadcast_event(self, data):  # type: ignore[override]
                if data.get("event") == "screenshot":
                    screenshot_events.append(data)

        bc = CaptureBroadcaster()
        watcher = live.EventWatcher(tmp_path, bc, refresh_ms=50)
        watcher._poll_once()
        assert len(screenshot_events) == 0

        # Create PNG with VLM-input filename pattern (verify_poll matches the
        # filter; non-matching names like 'step_01.png' or '_click.png' are
        # intentionally skipped — see VLM_INPUT_TOKENS in live_dashboard).
        (run_dir / "120000_step1_verify_poll.png").write_bytes(b"\x89PNG")
        watcher._poll_once()
        assert len(screenshot_events) == 1
        assert screenshot_events[0]["url"] == "/png/run1/vm1/120000_step1_verify_poll.png"

    def test_skips_malformed_json_line(self, tmp_path):
        """Watcher must not crash on mid-line garbage; valid lines after it still emit."""
        run_dir = tmp_path / "functional" / "run1" / "vm1"
        run_dir.mkdir(parents=True)
        events_file = run_dir / "events.jsonl"
        events_file.write_text(
            '{"event":"step_start","step_num":1}\n'
            '{GARBAGE\n'
            '{"event":"step_end","step_num":1,"status":"ok"}\n',
            encoding="utf-8",
        )

        collected: list[dict] = []

        class CaptureBroadcaster(live.SSEBroadcaster):
            def broadcast_event(self, data):  # type: ignore[override]
                collected.append(data)

        bc = CaptureBroadcaster()
        watcher = live.EventWatcher(tmp_path, bc, refresh_ms=50)
        watcher._poll_once()  # must not raise
        events_seen = [e["event"] for e in collected if "event" in e]
        assert "step_start" in events_seen
        assert "step_end" in events_seen
