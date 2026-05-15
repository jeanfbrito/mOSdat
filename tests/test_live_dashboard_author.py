"""Tests for Author API endpoints in automation/live_dashboard.py."""

from __future__ import annotations

import importlib.util
import json
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
from automation.authoring import AuthorManager, AuthorSession


# ---------------------------------------------------------------------------
# Fake collaborators
# ---------------------------------------------------------------------------

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
            "vlm": {"model": "localizer", "verify_model": "qwen3-vl", "verify_model_configured": True},
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_server(broadcaster: live.SSEBroadcaster, results_root: Path, author_manager=None):
    """Start dashboard server on a free port; return (server, port)."""
    from http.server import HTTPServer

    handler_cls = live._make_handler(broadcaster, results_root, author_manager=author_manager)
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


# ---------------------------------------------------------------------------
# Author API tests
# ---------------------------------------------------------------------------

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
            assert vms["vlm"] == {"model": "localizer", "verify_model": "qwen3-vl", "verify_model_configured": True}

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
