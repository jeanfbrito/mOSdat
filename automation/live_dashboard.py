"""Live event-stream dashboard for mosdat functional runs.

Usage:
    mosdat live --port 8080 [--results results/]

Architecture:
    - EventWatcher: polls events.jsonl mtimes every N ms, tails new lines, detects new PNGs
    - SSEBroadcaster: fan-out to all connected SSE clients
    - DashboardHandler: HTTP handler (stdlib only) serving HTML + /stream + /png/...

Public API (re-exported for tests and callers):
    SSEBroadcaster, EventWatcher  — from automation.live_events
    DashboardHandler, _make_handler — from automation.live_dashboard_handler
"""

from __future__ import annotations

import argparse
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from automation.authoring import AuthorManager
from automation.live_events import EventWatcher, SSEBroadcaster  # noqa: F401 (re-export)
from automation.live_dashboard_handler import DashboardHandler, _make_handler  # noqa: F401 (re-export)

# ---------------------------------------------------------------------------
# Heartbeat log (for phase signalling in tests)
# ---------------------------------------------------------------------------
_HB_LOG = Path("/tmp/agent-dashboard-hb.log")


def _hb(msg: str) -> None:
    try:
        with _HB_LOG.open("a") as fh:
            fh.write(f"{time.time():.3f} {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def cli(args: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="mosdat live", description="Live event-stream dashboard")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    parser.add_argument("--results", type=Path, default=Path("results"), metavar="DIR",
                        help="Results root directory (default: results/)")
    parser.add_argument("--refresh-ms", type=int, default=500, dest="refresh_ms",
                        help="Watcher poll interval ms (default: 500)")
    parser.add_argument("--warn-after", type=int, default=90, dest="warn_after",
                        help="Mark VM as warning/stale after N seconds without events (default: 90)")
    parser.add_argument("--stale-after", type=int, default=180, dest="stale_after",
                        help="Mark VM stale after N seconds without events (default: 180)")
    parser.add_argument("--config", type=Path, default=None,
                        help="mosdat config path; enables browser authoring sessions")
    parsed = parser.parse_args(args)

    results_root = parsed.results.resolve()
    _hb("cli_start")

    broadcaster = SSEBroadcaster()
    watcher = EventWatcher(results_root, broadcaster, refresh_ms=parsed.refresh_ms, debug_hook=_hb)

    watcher_thread = threading.Thread(target=watcher.run_forever, daemon=True, name="event-watcher")
    watcher_thread.start()
    _hb("watcher_thread_started")

    handler_cls = _make_handler(
        broadcaster,
        results_root,
        warn_after=parsed.warn_after,
        stale_after=parsed.stale_after,
        author_manager=AuthorManager(parsed.config),
    )
    server = ThreadingHTTPServer(("", parsed.port), handler_cls)
    server.daemon_threads = True

    print(f"[mOSdat] Dashboard live at http://localhost:{parsed.port}")
    print(f"[mOSdat] Watching: {results_root}/functional/")
    print("[mOSdat] Press Ctrl-C to stop.")
    _hb("server_start")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mOSdat] Dashboard stopped.")
    finally:
        watcher.stop()
        server.server_close()
        _hb("server_stop")

    return 0
