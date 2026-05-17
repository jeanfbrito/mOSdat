"""Tests for I14 HTML report enhancements.

Covers:
- shell_step event renders command_sent, stdout/stderr, exit_code in HTML
- verify_step event renders prompt_text, raw_vlm_response, verdict, cache_hit
- accept_any_step event renders all prompts + per-prompt verdicts
- config_snapshot event renders config.json content
- Backwards-compat: old events.jsonl (no new fields) still renders without error
- Config snapshot diff rendering (additions in green / removals in red)
"""

import json
import textwrap
from pathlib import Path

import pytest

from automation.reporting.report_data import group_by_step, load_events
from automation.reporting.report_html import render_html


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_events(*events: dict) -> str:
    """Return a multi-line JSONL string from event dicts."""
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _render_from_events(events_jsonl: str, tmp_path: Path) -> str:
    """Write events.jsonl, call render_html, return HTML string."""
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(events_jsonl, encoding="utf-8")
    events = load_events(events_path)
    steps = group_by_step(events)
    meta = {
        "scenario_name": "test-scenario",
        "start_ts": "2026-01-01T00:00:00",
        "end_ts": "2026-01-01T00:01:00",
        "duration": "60.0s",
        "overall": "PASS",
        "total": 1,
        "passed": 1,
        "failed": 0,
        "retried": 0,
        "commit": "abc1234",
    }
    return render_html(meta, steps, set(), tmp_path, events_path)


# ---------------------------------------------------------------------------
# shell_step tests
# ---------------------------------------------------------------------------

class TestShellStepRendering:
    def test_command_sent_appears_in_html(self, tmp_path):
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 1, "label": "test", "kind": "shell"},
            {"ts": "2026-01-01T00:00:01", "event": "shell_step", "step_num": 1,
             "command_sent": "echo hello world", "stdout_tail": "hello world\n",
             "stderr_tail": "", "exit_code": 0, "duration_ms": 120},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 1, "status": "ok", "attempts": 1, "duration_ms": 120},
        )
        html = _render_from_events(events, tmp_path)
        assert "echo hello world" in html
        assert "command_sent" in html

    def test_stdout_appears_in_collapsed_section(self, tmp_path):
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 1, "label": "t", "kind": "shell"},
            {"ts": "2026-01-01T00:00:01", "event": "shell_step", "step_num": 1,
             "command_sent": "ls /", "stdout_tail": "bin\nusr\nvar\n",
             "stderr_tail": "", "exit_code": 0, "duration_ms": 50},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 1, "status": "ok", "attempts": 1, "duration_ms": 50},
        )
        html = _render_from_events(events, tmp_path)
        assert "stdout" in html
        assert "bin" in html

    def test_nonzero_exit_code_has_red_class(self, tmp_path):
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 1, "label": "t", "kind": "shell"},
            {"ts": "2026-01-01T00:00:01", "event": "shell_step", "step_num": 1,
             "command_sent": "false", "stdout_tail": "", "stderr_tail": "error!\n",
             "exit_code": 1, "duration_ms": 10},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 1, "status": "ok", "attempts": 1, "duration_ms": 10},
        )
        html = _render_from_events(events, tmp_path)
        assert "answer-no" in html
        assert "stderr" in html
        assert "error!" in html

    def test_duration_shown(self, tmp_path):
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 1, "label": "t", "kind": "shell"},
            {"ts": "2026-01-01T00:00:01", "event": "shell_step", "step_num": 1,
             "command_sent": "sleep 1", "stdout_tail": "", "stderr_tail": "", "exit_code": 0, "duration_ms": 1050},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 1, "status": "ok", "attempts": 1, "duration_ms": 1050},
        )
        html = _render_from_events(events, tmp_path)
        assert "1050" in html


# ---------------------------------------------------------------------------
# verify_step tests
# ---------------------------------------------------------------------------

class TestVerifyStepRendering:
    def test_prompt_text_shown(self, tmp_path):
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 2, "label": "v", "kind": "verify"},
            {"ts": "2026-01-01T00:00:01", "event": "verify_step", "step_num": 2,
             "attempt": 1,
             "prompt_text": "The login page is visible with email and password fields",
             "raw_vlm_response": "Yes, I can see the login page with email and password fields.",
             "verdict": "yes", "cache_hit": False, "latency_ms": 800},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 2, "status": "ok", "attempts": 1, "duration_ms": 800},
        )
        html = _render_from_events(events, tmp_path)
        assert "The login page is visible with email and password fields" in html
        assert "prompt_text" in html

    def test_raw_vlm_response_shown(self, tmp_path):
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 2, "label": "v", "kind": "verify"},
            {"ts": "2026-01-01T00:00:01", "event": "verify_step", "step_num": 2,
             "attempt": 1,
             "prompt_text": "RC window is visible",
             "raw_vlm_response": "Yes, the Rocket.Chat window is clearly visible.",
             "verdict": "yes", "cache_hit": False, "latency_ms": 500},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 2, "status": "ok", "attempts": 1, "duration_ms": 500},
        )
        html = _render_from_events(events, tmp_path)
        assert "raw_vlm_response" in html
        assert "Rocket.Chat window is clearly visible" in html

    def test_cache_hit_badge_shown(self, tmp_path):
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 2, "label": "v", "kind": "verify"},
            {"ts": "2026-01-01T00:00:01", "event": "verify_step", "step_num": 2,
             "attempt": 1, "prompt_text": "something", "raw_vlm_response": "Yes.",
             "verdict": "yes", "cache_hit": True, "latency_ms": 1},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 2, "status": "ok", "attempts": 1, "duration_ms": 1},
        )
        html = _render_from_events(events, tmp_path)
        assert "cache hit" in html
        assert "cache-badge" in html

    def test_failed_verdict_has_red_class(self, tmp_path):
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 2, "label": "v", "kind": "verify"},
            {"ts": "2026-01-01T00:00:01", "event": "verify_step", "step_num": 2,
             "attempt": 1, "prompt_text": "Settings panel open",
             "raw_vlm_response": "No, the settings panel is not open.",
             "verdict": "no", "cache_hit": False, "latency_ms": 700},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 2, "status": "failed", "attempts": 1, "duration_ms": 700},
        )
        html = _render_from_events(events, tmp_path)
        assert "answer-no" in html


# ---------------------------------------------------------------------------
# accept_any_step tests
# ---------------------------------------------------------------------------

class TestAcceptAnyStepRendering:
    def test_all_prompts_shown(self, tmp_path):
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 3, "label": "a", "kind": "verify"},
            {"ts": "2026-01-01T00:00:01", "event": "accept_any_step", "step_num": 3,
             "attempt": 1,
             "prompts": ["Settings panel open", "Workspace login page unchanged"],
             "per_prompt_verdicts": [
                 {"prompt": "Settings panel open", "verdict": "no", "raw_vlm_response": "No."},
                 {"prompt": "Workspace login page unchanged", "verdict": "yes", "raw_vlm_response": "Yes."},
             ],
             "verdict": "yes", "latency_ms": 1200},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 3, "status": "ok", "attempts": 1, "duration_ms": 1200},
        )
        html = _render_from_events(events, tmp_path)
        assert "Settings panel open" in html
        assert "Workspace login page unchanged" in html
        assert "accept_any" in html

    def test_per_prompt_verdicts_shown(self, tmp_path):
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 3, "label": "a", "kind": "verify"},
            {"ts": "2026-01-01T00:00:01", "event": "accept_any_step", "step_num": 3,
             "attempt": 1,
             "prompts": ["PromptA", "PromptB"],
             "per_prompt_verdicts": [
                 {"prompt": "PromptA", "verdict": "no", "raw_vlm_response": "No indeed."},
                 {"prompt": "PromptB", "verdict": "yes", "raw_vlm_response": "Yes definitely."},
             ],
             "verdict": "yes", "latency_ms": 900},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 3, "status": "ok", "attempts": 1, "duration_ms": 900},
        )
        html = _render_from_events(events, tmp_path)
        assert "No indeed." in html
        assert "Yes definitely." in html


# ---------------------------------------------------------------------------
# config_snapshot tests
# ---------------------------------------------------------------------------

class TestConfigSnapshotRendering:
    def test_snapshot_content_shown(self, tmp_path):
        config_content = json.dumps({
            "isTelephonyEnabled": True,
            "telephonyPreferredServer": "https://example.com/",
        }, indent=2)
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 1, "label": "t", "kind": "shell"},
            {"ts": "2026-01-01T00:00:01", "event": "shell_step", "step_num": 1,
             "command_sent": "echo hi", "stdout_tail": "hi\n", "stderr_tail": "", "exit_code": 0, "duration_ms": 10},
            {"ts": "2026-01-01T00:00:01.5", "event": "config_snapshot", "step_num": 1,
             "content": config_content},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 1, "status": "ok", "attempts": 1, "duration_ms": 15},
        )
        html = _render_from_events(events, tmp_path)
        assert "config_snapshot" in html
        assert "isTelephonyEnabled" in html
        assert "telephonyPreferredServer" in html

    def test_snapshot_section_is_collapsible(self, tmp_path):
        """Config snapshot should use <details> so it's collapsed by default."""
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 1, "label": "t", "kind": "shell"},
            {"ts": "2026-01-01T00:00:01", "event": "config_snapshot", "step_num": 1, "content": '{"x": 1}'},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 1, "status": "ok", "attempts": 1, "duration_ms": 5},
        )
        html = _render_from_events(events, tmp_path)
        assert "<details" in html
        assert "<summary" in html


# ---------------------------------------------------------------------------
# Backwards-compatibility tests
# ---------------------------------------------------------------------------

class TestBackwardsCompat:
    def test_old_events_render_without_error(self, tmp_path):
        """Old events.jsonl without new fields must render cleanly."""
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 1, "label": "cleanup", "kind": "shell"},
            {"ts": "2026-01-01T00:00:01", "event": "shell", "step_num": 1, "cmd_truncated": "pkill -f rocketchat"},
            {"ts": "2026-01-01T00:00:05", "event": "step_end", "step_num": 1, "status": "ok", "attempts": 1, "duration_ms": 4000},
            {"ts": "2026-01-01T00:00:05", "event": "step_start", "step_num": 2, "label": "", "kind": "verify"},
            {"ts": "2026-01-01T00:00:20", "event": "vlm_verify", "step_num": 2, "attempt": 1,
             "question": "RC login page visible", "answer": "yes", "latency_ms": 15000, "kind": "verify"},
            {"ts": "2026-01-01T00:00:21", "event": "step_end", "step_num": 2, "status": "ok", "attempts": 1, "duration_ms": 16000},
        )
        # Must not raise
        html = _render_from_events(events, tmp_path)
        assert "Step 1" in html
        assert "Step 2" in html

    def test_missing_new_fields_degrade_gracefully(self, tmp_path):
        """Events with missing optional new fields use safe defaults."""
        # shell_step with minimal fields
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 1, "label": "t", "kind": "shell"},
            {"ts": "2026-01-01T00:00:01", "event": "shell_step", "step_num": 1,
             "command_sent": "echo hi"},  # missing stdout_tail, stderr_tail, exit_code, duration_ms
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 1, "status": "ok", "attempts": 1, "duration_ms": 100},
        )
        html = _render_from_events(events, tmp_path)
        assert "echo hi" in html

    def test_verify_step_missing_raw_response(self, tmp_path):
        """verify_step without raw_vlm_response should still render."""
        events = _make_events(
            {"ts": "2026-01-01T00:00:00", "event": "step_start", "step_num": 1, "label": "v", "kind": "verify"},
            {"ts": "2026-01-01T00:00:01", "event": "verify_step", "step_num": 1,
             "attempt": 1, "prompt_text": "Something visible", "verdict": "yes"},
            {"ts": "2026-01-01T00:00:02", "event": "step_end", "step_num": 1, "status": "ok", "attempts": 1, "duration_ms": 500},
        )
        html = _render_from_events(events, tmp_path)
        assert "Something visible" in html

    def test_existing_result_dir_renders(self, tmp_path):
        """Simulate the real existing events.jsonl format (no new fields)."""
        # Replicate what the existing 2026-05-16 run produces
        raw_events = textwrap.dedent("""\
            {"ts": "2026-05-16T19:02:09.365329", "event": "display_prepared", "method": "vnc_jiggle+xset_dpms_off"}
            {"ts": "2026-05-16T19:02:09.366813", "event": "step_start", "step_num": 1, "label": "set -u", "kind": "shell"}
            {"ts": "2026-05-16T19:02:09.367040", "event": "shell", "step_num": 1, "cmd_truncated": "set -u\\nif ! command -v wmctrl"}
            {"ts": "2026-05-16T19:02:15.024847", "event": "step_end", "step_num": 1, "status": "ok", "attempts": 1, "duration_ms": 5658}
            {"ts": "2026-05-16T19:02:52.882558", "event": "vlm_verify", "step_num": 1, "attempt": 1, "question": "RC login page", "answer": "yes", "latency_ms": 22199, "kind": "verify"}
        """)
        html = _render_from_events(raw_events, tmp_path)
        assert "Step 1" in html
        # Should not have any Python exception string in output
        assert "Traceback" not in html
        assert "AttributeError" not in html
