"""DashboardHandler and factory for the live dashboard HTTP server."""

from __future__ import annotations

import json
import mimetypes
import posixpath
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from automation.authoring import AuthorManager
from automation.dashboard_state import build_dashboard_state
from automation.live_events import SSEBroadcaster
from automation.live_dashboard_html import _TRIAGE_HTML, _AUTHOR_HTML


class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the live dashboard HTML, SSE stream, PNG screenshots, and artifacts."""

    # Injected by factory
    broadcaster: SSEBroadcaster
    results_root: Path
    warn_after: int
    stale_after: int
    author_manager: AuthorManager

    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "":
            self._serve_html()
        elif path == "/author":
            self._serve_author_html()
        elif path == "/api/state":
            self._serve_state()
        elif path == "/api/author/vms":
            self._send_json(self.author_manager.list_vms())
        elif path == "/api/author/session":
            self._serve_author_session(parsed.query)
        elif path == "/stream":
            self._serve_sse()
        elif path == "/api/author/screenshot":
            self._serve_author_screenshot(parsed.query)
        elif path == "/api/author/export":
            self._serve_author_export(parsed.query)
        elif path.startswith("/png/"):
            self._serve_png(path)
        elif path.startswith("/artifact/"):
            self._serve_artifact(path)
        else:
            self.send_error(404, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        body = self._read_json()
        try:
            if parsed.path == "/api/author/session":
                result = self.author_manager.create_session(
                    body["vm"],
                    model=body.get("model"),
                    verify_model=body.get("verify_model"),
                )
            elif parsed.path == "/api/author/capture":
                session = self.author_manager.get(body["session_id"])
                result = self._agent_result(session.capture())
            elif parsed.path == "/api/author/vlm/localize":
                session = self.author_manager.get(body["session_id"])
                result = session.localize(body["prompt"])
            elif parsed.path == "/api/author/vlm/describe":
                session = self.author_manager.get(body["session_id"])
                result = session.describe_target(int(body["x"]), int(body["y"]))
            elif parsed.path == "/api/author/vlm/verify":
                session = self.author_manager.get(body["session_id"])
                result = session.verify(body["question"])
            elif parsed.path == "/api/author/action":
                session = self.author_manager.get(body["session_id"])
                result = session.run_action(body["action"], body)
            elif parsed.path == "/api/author/validate":
                result = self.author_manager.validate(
                    body["session_id"],
                    name=body.get("name", "authored-scenario"),
                )
            elif parsed.path == "/api/author/step":
                session = self.author_manager.get(body["session_id"])
                if "steps" in body:
                    result = session.update_steps(body["steps"])
                else:
                    result = session.append_step(body["step"])
            elif parsed.path == "/api/author/close":
                result = self.author_manager.close(body["session_id"])
            else:
                self.send_error(404, "Not Found")
                return
            self._send_json(result)
        except (KeyError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, status=500)

    def _serve_html(self) -> None:
        body = _TRIAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_author_html(self) -> None:
        body = _AUTHOR_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _agent_result(self, result: dict) -> dict:
        return {"ok": True, "result": result}

    def _serve_state(self) -> None:
        state = build_dashboard_state(
            self.results_root,
            warn_after=self.warn_after,
            stale_after=self.stale_after,
        )
        body = json.dumps(state).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_author_screenshot(self, query: str) -> None:
        params = parse_qs(query)
        session_id = params.get("session", [""])[0]
        try:
            payload = self.author_manager.get(session_id).capture()
            self._send_json(payload)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _serve_author_session(self, query: str) -> None:
        params = parse_qs(query)
        session_id = params.get("session", [""])[0]
        try:
            self._send_json(self.author_manager.state(session_id))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _serve_author_export(self, query: str) -> None:
        params = parse_qs(query)
        session_id = params.get("session", [""])[0]
        name = params.get("name", ["authored-scenario"])[0]
        try:
            yaml_text = self.author_manager.get(session_id).export_yaml(name=name)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        body = yaml_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/yaml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = self.broadcaster.subscribe()
        last_hb = time.monotonic()
        try:
            while True:
                # Drain pending messages
                pending = list(q)
                del q[:]
                for chunk in pending:
                    self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()

                # Heartbeat every 15 s
                now = time.monotonic()
                if now - last_hb >= 15.0:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    last_hb = now

                time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.broadcaster.unsubscribe(q)

    def _serve_png(self, path: str) -> None:
        # path = /png/<run>/<vm>/<file>
        parts = path[len("/png/"):].split("/")
        if len(parts) < 3:
            self.send_error(400, "Bad path")
            return

        candidate = posixpath.normpath("/".join(parts))
        if ".." in candidate.split("/"):
            self.send_error(400, "Path traversal rejected")
            return

        run, vm, filename = parts[0], parts[1], "/".join(parts[2:])

        for seg in (run, vm, filename):
            if ".." in seg or seg.startswith("/"):
                self.send_error(400, "Path traversal rejected")
                return

        img_path = self.results_root / "functional" / run / vm / filename
        try:
            resolved = img_path.resolve()
            base = (self.results_root / "functional").resolve()
            resolved.relative_to(base)
        except (ValueError, OSError):
            self.send_error(403, "Forbidden")
            return

        if not resolved.exists() or not resolved.is_file():
            self.send_error(404, "Not Found")
            return

        try:
            data = resolved.read_bytes()
        except OSError:
            self.send_error(500, "Read error")
            return

        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_artifact(self, path: str) -> None:
        # path = /artifact/<run>/<vm>/<file>
        parts = path[len("/artifact/"):].split("/")
        if len(parts) < 3:
            self.send_error(400, "Bad path")
            return

        candidate = posixpath.normpath("/".join(parts))
        if ".." in candidate.split("/"):
            self.send_error(400, "Path traversal rejected")
            return

        run, vm, filename = parts[0], parts[1], "/".join(parts[2:])
        for seg in (run, vm, filename):
            if ".." in seg or seg.startswith("/"):
                self.send_error(400, "Path traversal rejected")
                return

        artifact_path = self.results_root / "functional" / run / vm / filename
        try:
            resolved = artifact_path.resolve()
            base = (self.results_root / "functional").resolve()
            resolved.relative_to(base)
        except (ValueError, OSError):
            self.send_error(403, "Forbidden")
            return

        if not resolved.exists() or not resolved.is_file():
            self.send_error(404, "Not Found")
            return

        try:
            data = resolved.read_bytes()
        except OSError:
            self.send_error(500, "Read error")
            return

        ctype, _ = mimetypes.guess_type(str(resolved))
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _make_handler(
    broadcaster: SSEBroadcaster,
    results_root: Path,
    warn_after: int = 90,
    stale_after: int = 180,
    author_manager: Optional[AuthorManager] = None,
):
    """Return a DashboardHandler subclass with broadcaster + results_root bound."""

    class BoundHandler(DashboardHandler):
        pass

    BoundHandler.broadcaster = broadcaster
    BoundHandler.results_root = results_root
    BoundHandler.warn_after = warn_after
    BoundHandler.stale_after = stale_after
    BoundHandler.author_manager = author_manager or AuthorManager()
    return BoundHandler
