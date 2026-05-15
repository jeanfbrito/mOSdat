"""Tests for SSEBroadcaster in automation/live_dashboard.py."""

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
