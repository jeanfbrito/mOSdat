"""Tests for HTTP handler in automation/live_dashboard.py."""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
from http.client import HTTPConnection
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


def _start_server(broadcaster: live.SSEBroadcaster, results_root: Path, author_manager=None):
    """Start dashboard server on a free port; return (server, port).

    Uses ``ThreadingHTTPServer`` (matching production in ``live_dashboard.py``).
    The single-threaded ``HTTPServer`` deadlocks ``shutdown()`` when an SSE
    ``/stream`` request is in flight: the serving thread is stuck inside the
    handler's blocking write loop and can never return to the poll cycle that
    notices the stop flag, so ``shutdown()`` waits for ``_is_shut_down``
    forever (the original ~19-minute test hang).
    """
    from http.server import ThreadingHTTPServer

    handler_cls = live._make_handler(broadcaster, results_root, author_manager=author_manager)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


# ---------------------------------------------------------------------------
# HTTP handler tests (using a live server on a random port)
# ---------------------------------------------------------------------------

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
            assert "author-op" in body
            assert "op-card" in body
            assert "authorRun" in body
            assert "authorSetBusy" in body
            assert "Request sent. Waiting for response" in body
            assert "waiting for dashboard response" in body
            assert "button:disabled" in body
            assert "data-op" in body
            assert "Describing clicked target" in body
            assert "Refreshing screen" in body
            assert "Starting session" in body
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

    def test_artifact_served(self, tmp_path):
        run_dir = tmp_path / "functional" / "run1" / "vm1" / "recording"
        run_dir.mkdir(parents=True)
        mp4_data = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16
        (run_dir / "session.mp4").write_bytes(mp4_data)

        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/artifact/run1/vm1/recording/session.mp4")
            resp = conn.getresponse()
            assert resp.status == 200
            assert resp.getheader("Content-Type") == "video/mp4"
            assert resp.read() == mp4_data
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

    def test_artifact_path_traversal_rejected(self, tmp_path):
        bc = live.SSEBroadcaster()
        server, port = _start_server(bc, tmp_path)
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/artifact/run1/vm1/../../../etc/passwd")
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
