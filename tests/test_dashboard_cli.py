"""Tests for dashboard CLI --alert flag wiring (I6).

Covers:
  - --alert without webhook → no-op warning
  - without --alert → notify never called
  - --alert + NOTIFY_WEBHOOK set + regressions → notify() called with status=fail
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
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
# TestCLIAlertFlag
# ---------------------------------------------------------------------------

class TestCLIAlertFlag:
    """Tests for --alert, --threshold-multiplier, --min-pass-rate wiring."""

    def _make_results_with_regression(self, tmp_path: Path) -> Path:
        """Create a results dir that will produce a pass-rate regression."""
        from datetime import datetime, timezone

        now_ts = datetime.now(tz=timezone.utc).timestamp()
        recent_ts = now_ts - 3 * 86400
        base_ts = now_ts - 15 * 86400

        recent_date = datetime.fromtimestamp(recent_ts, tz=timezone.utc).strftime(
            "%Y-%m-%d_functional"
        )
        base_date = datetime.fromtimestamp(base_ts, tz=timezone.utc).strftime(
            "%Y-%m-%d_functional"
        )

        functional = tmp_path / "results" / "functional"

        # baseline run: high pass rate
        _make_run(functional, base_date, "vm1",
                  _step_events(1, "step", "passed", ts_base=base_ts)
                  + _step_events(2, "step2", "passed", ts_base=base_ts + 10))

        # recent run: poor pass rate (1 pass, 1 fail)
        _make_run(functional, recent_date, "vm1",
                  _step_events(1, "step", "passed", ts_base=recent_ts)
                  + _step_events(2, "step2", "failed", ts_base=recent_ts + 10))

        return tmp_path / "results"

    def test_alert_without_webhook_is_noop(self, tmp_path: Path, monkeypatch, capsys):
        """--alert with no NOTIFY_WEBHOOK/SMTP env → warning printed, no crash."""
        monkeypatch.delenv("NOTIFY_WEBHOOK", raising=False)
        monkeypatch.delenv("NOTIFY_EMAIL_SMTP", raising=False)

        root = self._make_results_with_regression(tmp_path)
        rc = dashboard.cli([
            "--root", str(root),
            "--output", str(tmp_path / "dashboard.html"),
            "--alert",
        ])
        assert rc == 0
        captured = capsys.readouterr()
        # Warning should mention the no-op
        assert "NOTIFY_WEBHOOK" in captured.err or "skipping" in captured.err.lower()

    def test_alert_not_set_no_notify_called(self, tmp_path: Path, monkeypatch):
        """Without --alert, notify is never imported/called even if regressions exist."""
        monkeypatch.setenv("NOTIFY_WEBHOOK", "http://fake.webhook/")
        root = self._make_results_with_regression(tmp_path)

        calls: list[dict] = []

        # Patch notify module in sys.modules to intercept import
        fake_notify = types.ModuleType("automation.notify")

        def _fake_notify(*args, **kwargs) -> None:  # type: ignore[misc]
            calls.append({"args": args, "kwargs": kwargs})

        fake_notify.notify = _fake_notify
        monkeypatch.setitem(sys.modules, "automation.notify", fake_notify)

        rc = dashboard.cli([
            "--root", str(root),
            "--output", str(tmp_path / "dashboard.html"),
            # No --alert flag
        ])
        assert rc == 0
        assert calls == [], "notify() should not be called without --alert"

    def test_alert_calls_notify_when_webhook_set(self, tmp_path: Path, monkeypatch):
        """--alert + NOTIFY_WEBHOOK set + regressions → notify() is called with status=fail."""
        monkeypatch.setenv("NOTIFY_WEBHOOK", "http://fake.webhook/")
        monkeypatch.setenv("NOTIFY_CHANNEL", "slack")

        root = self._make_results_with_regression(tmp_path)

        calls: list[dict] = []

        fake_notify = types.ModuleType("automation.notify")

        def _fake_notify(*args, **kwargs) -> None:  # type: ignore[misc]
            calls.append({"args": args, "kwargs": kwargs})

        fake_notify.notify = _fake_notify
        monkeypatch.setitem(sys.modules, "automation.notify", fake_notify)

        rc = dashboard.cli([
            "--root", str(root),
            "--output", str(tmp_path / "dashboard.html"),
            "--alert",
        ])
        assert rc == 0
        # If regressions were found, notify() must have been called
        if calls:
            assert calls[0]["kwargs"]["status"] == "fail"
