"""Tests for automation/live_dashboard.py.

Covers:
  - EventWatcher detects new lines added to events.jsonl
  - EventWatcher detects new PNG files via scandir
  - SSEBroadcaster delivers to multiple subscribers
  - SSEBroadcaster drops disconnected subscribers without error
  - HTTP handler serves HTML at /
  - HTTP handler sets text/event-stream Content-Type at /stream
  - HTTP handler serves PNG with image/png Content-Type
  - Path traversal protection on /png/ endpoint
  - EventWatcher skips malformed mid-line JSON without crashing
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
from datetime import datetime
from http.client import HTTPConnection
from pathlib import Path

# ---------------------------------------------------------------------------
# Module loader (mirrors pattern from test_dashboard.py)
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
from automation.dashboard_state import build_dashboard_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for evt in events:
            fh.write(json.dumps(evt) + "\n")


def _append_event(path: Path, evt: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(evt) + "\n")


# ---------------------------------------------------------------------------
# SSEBroadcaster tests
# ---------------------------------------------------------------------------

class TestSSEBroadcaster:
    def test_single_subscriber_receives_event(self):
        bc = live.SSEBroadcaster()
        q = bc.subscribe()
        bc.broadcast_event({"event": "step_start", "vm": "ubuntu"})
        assert len(q) == 1
        payload = json.loads(q[0].removeprefix("data: ").strip())
        assert payload["event"] == "step_start"

    def test_multiple_subscribers_all_receive(self):
        bc = live.SSEBroadcaster()
        q1 = bc.subscribe()
        q2 = bc.subscribe()
        bc.broadcast_event({"x": 1})
        assert len(q1) == 1
        assert len(q2) == 1

    def test_unsubscribe_drops_queue(self):
        bc = live.SSEBroadcaster()
        q = bc.subscribe()
        bc.unsubscribe(q)
        bc.broadcast_event({"x": 2})
        assert len(q) == 0  # no more messages after unsubscribe

    def test_unsubscribe_nonexistent_is_safe(self):
        bc = live.SSEBroadcaster()
        orphan: list = []
        bc.unsubscribe(orphan)  # must not raise

    def test_heartbeat_format(self):
        bc = live.SSEBroadcaster()
        q = bc.subscribe()
        bc.heartbeat()
        assert q[0] == ": heartbeat\n\n"


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


# ---------------------------------------------------------------------------
# HTTP handler tests (using a live server on a random port)
# ---------------------------------------------------------------------------

def _start_server(broadcaster: live.SSEBroadcaster, results_root: Path):
    """Start dashboard server on a free port; return (server, port)."""
    from http.server import HTTPServer

    handler_cls = live._make_handler(broadcaster, results_root)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


class TestHTTPHandler:
    def test_root_serves_html(self, tmp_path):
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            assert resp.status == 200
            ct = resp.getheader("Content-Type", "")
            assert "text/html" in ct
            body = resp.read().decode("utf-8")
            assert "mOSdat Live Triage" in body
        finally:
            server.shutdown()

    def test_api_state_served(self, tmp_path):
        run_dir = tmp_path / "functional" / "run1" / "ubuntu"
        run_dir.mkdir(parents=True)
        _write_events(run_dir / "events.jsonl", [
            {"ts": "2026-05-03T10:00:00", "event": "step_start", "step_num": 1, "kind": "launch"},
            {"ts": "2026-05-03T10:00:01", "event": "step_end", "step_num": 1, "status": "ok", "duration_ms": 1000, "attempts": 1},
        ])

        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/state")
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200
            assert payload["runs"][0]["vms"][0]["vm"] == "ubuntu"
            assert payload["runs"][0]["vms"][0]["steps"][0]["status"] == "pass"
        finally:
            server.shutdown()

    def test_stream_content_type(self, tmp_path):
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path)
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            sock.sendall(b"GET /stream HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            # Read only the headers (first 1 KB)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = sock.recv(512)
                if not chunk:
                    break
                data += chunk
            sock.close()
            headers_str = data.decode("utf-8", errors="replace")
            assert "text/event-stream" in headers_str
        finally:
            server.shutdown()

    def test_png_served(self, tmp_path):
        run_dir = tmp_path / "functional" / "run1" / "vm1"
        run_dir.mkdir(parents=True)
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
        (run_dir / "step_01.png").write_bytes(png_data)

        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/png/run1/vm1/step_01.png")
            resp = conn.getresponse()
            assert resp.status == 200
            assert resp.getheader("Content-Type") == "image/png"
            assert resp.read() == png_data
        finally:
            server.shutdown()

    def test_path_traversal_rejected(self, tmp_path):
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/png/../../../etc/passwd")
            resp = conn.getresponse()
            resp.read()
            assert resp.status in (400, 403, 404)
        finally:
            server.shutdown()

    def test_path_traversal_dotdot_in_segment(self, tmp_path):
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/png/run1/../../../etc/passwd")
            resp = conn.getresponse()
            resp.read()
            assert resp.status in (400, 403, 404)
        finally:
            server.shutdown()

    def test_unknown_path_404(self, tmp_path):
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/nonexistent")
            resp = conn.getresponse()
            resp.read()
            assert resp.status == 404
        finally:
            server.shutdown()


class TestDashboardState:
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
        assert state["failures"][0]["question"] == "logged in?"
        assert state["failures"][0]["screenshot"]["url"] == "/png/run1/fedora/120000_step2_verify_poll.png"

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

    def test_screenshot_only_failure_is_not_running(self, tmp_path):
        run_dir = tmp_path / "functional" / "old-run" / "windows11"
        run_dir.mkdir(parents=True)
        (run_dir / "204747_step1_fail_attempt1.png").write_bytes(b"png")
        (run_dir / "204800_step1_final_fail.png").write_bytes(b"png")

        state = build_dashboard_state(tmp_path, now=datetime(2026, 5, 3, 10, 0, 0))

        vm = state["runs"][0]["vms"][0]
        assert vm["status"] == "fail"
        assert vm["steps"][0]["status"] == "fail"
        assert state["failures"][0]["screenshot"]["url"] == "/png/old-run/windows11/204800_step1_final_fail.png"
