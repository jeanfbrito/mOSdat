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
from automation.authoring import AuthorManager, AuthorSession
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


class FakeImage:
    def convert(self, mode):
        return self

    def tobytes(self):
        return b"\xff\xff\xff" * 20 * 10


class FakeVNC:
    def capture(self):
        return FakeImage(), (20, 10)


class FakeVLM:
    def localize(self, image, prompt, size):
        return 5, 6

    def verify(self, image, question):
        return "ready" in question

    def describe_element(self, image, x, y):
        return f"described target at {x},{y}"


class FakeInjector:
    def __init__(self):
        self.calls = []

    def click(self, x, y, button=1):
        self.calls.append(("click", x, y, button))

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def type_text(self, text):
        self.calls.append(("type", text))

    def key(self, key):
        self.calls.append(("key", key))

    def shell(self, cmd, timeout=60):
        self.calls.append(("shell", cmd))


class FakeAuthorManager(AuthorManager):
    def __init__(self):
        super().__init__(config_path=None)
        self.injector = FakeInjector()

    def create_session(self, vm_name, model=None, verify_model=None):
        session = AuthorSession(
            id="session-1",
            vm_name=vm_name,
            vnc=FakeVNC(),
            vlm=FakeVLM(),
            injector=self.injector,
        )
        self.sessions[session.id] = session
        return self.describe(session)

    def list_vms(self):
        return {
            "configured": True,
            "vms": [
                {
                    "name": "ubuntu2404",
                    "vmid": 101,
                    "desktop": "kde",
                    "os_type": "linux",
                    "status": "running",
                    "running": True,
                },
                {
                    "name": "windows11",
                    "vmid": 102,
                    "desktop": "windows",
                    "os_type": "windows",
                    "status": "stopped",
                    "running": False,
                },
            ],
        }


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

def _start_server(broadcaster: live.SSEBroadcaster, results_root: Path, author_manager=None):
    """Start dashboard server on a free port; return (server, port)."""
    from http.server import HTTPServer

    handler_cls = live._make_handler(broadcaster, results_root, author_manager=author_manager)
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
            assert "run-filter" in body
            assert "freshness" in body
            assert "Latest</option>" in body
            assert "Total runtime" in body
            assert 'href="/author"' in body
            assert "author-screen" not in body
        finally:
            server.shutdown()

    def test_author_page_serves_full_workbench(self, tmp_path):
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/author")
            resp = conn.getresponse()
            assert resp.status == 200
            ct = resp.getheader("Content-Type", "")
            assert "text/html" in ct
            body = resp.read().decode("utf-8")
            assert "mOSdat Author Workbench" in body
            assert 'href="/"' in body
            assert "author-screen" in body
            assert "<select id=\"author-vm\"" in body
            assert "authorLoadVms()" in body
            assert "Refresh screen" in body
            assert "authorAfterAction" in body
            assert "authorEnsureSession" in body
            assert "authorPlaceTarget" in body
            assert "getBoundingClientRect" in body
            assert "Hover" in body
            assert "authorHover" in body
            assert "Run confirmed wait" in body
            assert "authorWait" in body
            assert "Run confirmed shell" in body
            assert "authorShell" in body
            assert "Run confirmed launch" in body
            assert "authorLaunch" in body
            assert "Validate draft" in body
            assert "authorValidate" in body
            assert "Close session" in body
            assert "authorClose" in body
            assert "authorPickTarget" in body
            assert "click screenshot to set target" in body
            assert "Describe clicked target" in body
            assert "authorDescribeTarget" in body
            assert "/api/author/vlm/describe" in body
            assert "Draft steps JSON editor" in body
            assert "author-steps-json" in body
            assert "Load steps" in body
            assert "authorLoadStepsEditor" in body
            assert "Replace steps" in body
            assert "authorReplaceSteps" in body
            assert "Append step" in body
            assert "authorAppendStep" in body
            assert "Left click" in body
            assert "Right click" in body
            assert "button,confirm:true" in body
            assert ".target:before,.target:after" in body
            assert "start a session first" not in body
            assert "/api/author/session" in body
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


class TestAuthorAPI:
    def _post(self, port, path, payload):
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            path,
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        return resp.status, data

    def test_author_session_screenshot_vlm_and_export(self, tmp_path):
        manager = FakeAuthorManager()
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path, author_manager=manager)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/author/vms")
            resp = conn.getresponse()
            vms = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200
            assert vms["vms"][0]["name"] == "ubuntu2404"
            assert vms["vms"][0]["status"] == "running"

            status, data = self._post(port, "/api/author/session", {"vm": "ubuntu2404"})
            assert status == 200
            assert data["session_id"] == "session-1"

            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/author/session?session=session-1")
            resp = conn.getresponse()
            state = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200
            assert state["vm"] == "ubuntu2404"
            assert state["draft_steps"] == []

            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/author/screenshot?session=session-1")
            resp = conn.getresponse()
            shot = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200
            assert shot["width"] == 20
            assert shot["height"] == 10
            assert shot["image"]

            status, capture = self._post(port, "/api/author/capture", {"session_id": "session-1"})
            assert status == 200
            assert capture["ok"] is True
            assert capture["result"]["width"] == 20

            status, loc = self._post(
                port,
                "/api/author/vlm/localize",
                {"session_id": "session-1", "prompt": "login button"},
            )
            assert status == 200
            assert (loc["x"], loc["y"]) == (5, 6)

            status, described = self._post(
                port,
                "/api/author/vlm/describe",
                {"session_id": "session-1", "x": 7, "y": 8},
            )
            assert status == 200
            assert described == {
                "prompt": "described target at 7,8",
                "x": 7,
                "y": 8,
                "width": 20,
                "height": 10,
                "source": "vlm_describe",
            }

            status, verify = self._post(
                port,
                "/api/author/vlm/verify",
                {"session_id": "session-1", "question": "is ready?"},
            )
            assert status == 200
            assert verify["answer"] == "yes"

            status, action = self._post(
                port,
                "/api/author/action",
                {"session_id": "session-1", "action": "click", "confirm": True, "x": 5, "y": 6, "prompt": "login button"},
            )
            assert status == 200
            assert manager.injector.calls == [("click", 5, 6, 1)]
            assert action["draft_steps"][0]["localize"] == "login button"

            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/author/export?session=session-1&name=demo")
            resp = conn.getresponse()
            text = resp.read().decode("utf-8")
            assert resp.status == 200
            assert "name: demo" in text
            assert "login button" in text
        finally:
            server.shutdown()

    def test_author_right_click_records_button(self, tmp_path):
        manager = FakeAuthorManager()
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path, author_manager=manager)
        try:
            self._post(port, "/api/author/session", {"vm": "ubuntu2404"})

            status, action = self._post(
                port,
                "/api/author/action",
                {
                    "session_id": "session-1",
                    "action": "click",
                    "button": "right",
                    "confirm": True,
                    "x": 5,
                    "y": 6,
                    "prompt": "context menu",
                },
            )

            assert status == 200
            assert manager.injector.calls == [("click", 5, 6, 3)]
            assert action["draft_steps"][0]["click"] == "right"
        finally:
            server.shutdown()

    def test_author_hover_records_move(self, tmp_path):
        manager = FakeAuthorManager()
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path, author_manager=manager)
        try:
            self._post(port, "/api/author/session", {"vm": "ubuntu2404"})

            status, action = self._post(
                port,
                "/api/author/action",
                {
                    "session_id": "session-1",
                    "action": "hover",
                    "confirm": True,
                    "x": 5,
                    "y": 6,
                    "prompt": "help tooltip",
                },
            )

            assert status == 200
            assert manager.injector.calls == [("move", 5, 6)]
            assert action["draft_steps"][0]["hover"] is True
            assert action["draft_steps"][0]["localize"] == "help tooltip"
        finally:
            server.shutdown()

    def test_author_validate_draft_steps(self, tmp_path):
        manager = FakeAuthorManager()
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path, author_manager=manager)
        try:
            self._post(port, "/api/author/session", {"vm": "ubuntu2404"})
            self._post(
                port,
                "/api/author/action",
                {
                    "session_id": "session-1",
                    "action": "hover",
                    "confirm": True,
                    "x": 5,
                    "y": 6,
                    "prompt": "help tooltip",
                },
            )

            status, data = self._post(port, "/api/author/validate", {"session_id": "session-1"})
            assert status == 200
            assert data["ok"] is True

            self._post(port, "/api/author/step", {"session_id": "session-1", "steps": [{"loaclize": "typo"}]})
            status, data = self._post(port, "/api/author/validate", {"session_id": "session-1"})
            assert status == 200
            assert data["ok"] is False
            assert "validation" in data["error"].lower()
        finally:
            server.shutdown()

    def test_author_rejects_unknown_click_button(self, tmp_path):
        manager = FakeAuthorManager()
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path, author_manager=manager)
        try:
            self._post(port, "/api/author/session", {"vm": "ubuntu2404"})
            status, data = self._post(
                port,
                "/api/author/action",
                {
                    "session_id": "session-1",
                    "action": "click",
                    "button": "middle",
                    "confirm": True,
                    "x": 5,
                    "y": 6,
                },
            )
            assert status == 400
            assert "unsupported click button" in data["error"]
            assert manager.injector.calls == []
        finally:
            server.shutdown()

    def test_author_action_requires_confirm(self, tmp_path):
        manager = FakeAuthorManager()
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path, author_manager=manager)
        try:
            self._post(port, "/api/author/session", {"vm": "ubuntu2404"})
            status, data = self._post(
                port,
                "/api/author/action",
                {"session_id": "session-1", "action": "click", "x": 5, "y": 6},
            )
            assert status == 400
            assert "confirm" in data["error"]
            assert manager.injector.calls == []
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
