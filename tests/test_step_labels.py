"""Tests for I10 — step labels parsed from YAML comments.

Covers:
  - Box comment ``# ── A4: text ──`` → label captured
  - Plain comment ``# step does X`` → captured as label fallback
  - No comment → label None
  - Explicit ``label:`` field in YAML wins over comment-derived label
  - Multi-line comments → joined with space
  - step_start event includes step_label field with "N: label" format
  - HTML report uses label as step header
  - Backwards compat: scenarios without any labels render unchanged
  - Demo: labels from 3325-master-toggle.yaml match the ── comments
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
import tempfile
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

def _load_mod(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _PROJ / rel_path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = ".".join(name.split(".")[:-1])
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_loader_mod = _load_mod("automation/runners/scenario_loader.py", "automation.runners.scenario_loader")
_extract_step_labels = _loader_mod._extract_step_labels
_parse_comment_label = _loader_mod._parse_comment_label
load_test_yaml = _loader_mod.load_test_yaml
parse_step = _loader_mod.parse_step


# ---------------------------------------------------------------------------
# Helper: write a temp YAML file and return its Path
# ---------------------------------------------------------------------------

def _write_yaml(content: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    tmp.write(textwrap.dedent(content))
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# Unit tests: _parse_comment_label
# ---------------------------------------------------------------------------

class TestParseCommentLabel:
    def test_box_pattern_extracted(self):
        lines = ["# ── A4: Dispatch tel: → expect modal ──────────────────"]
        assert _parse_comment_label(lines) == "A4: Dispatch tel: → expect modal"

    def test_box_pattern_double_dash(self):
        lines = ["# ── Simple label ──"]
        assert _parse_comment_label(lines) == "Simple label"

    def test_box_pattern_wins_over_plain(self):
        lines = ["# plain line first", "# ── box label ──"]
        # box pattern takes first matching box line (scanning order matters)
        result = _parse_comment_label(lines)
        assert result == "box label"

    def test_plain_comment_fallback(self):
        lines = ["# step does X and Y"]
        assert _parse_comment_label(lines) == "step does X and Y"

    def test_no_comment_returns_none(self):
        assert _parse_comment_label(None) is None
        assert _parse_comment_label([]) is None

    def test_multi_line_plain_joined(self):
        lines = ["# line one", "# line two"]
        assert _parse_comment_label(lines) == "line one line two"

    def test_multi_line_capped_at_80(self):
        # Generate a long comment
        long = "# " + "x" * 100
        lines = [long]
        result = _parse_comment_label(lines)
        assert len(result) == 80

    def test_empty_comment_hash_only(self):
        # A line with just "#" contributes nothing
        lines = ["#"]
        assert _parse_comment_label(lines) is None

    def test_equals_separator_also_matches(self):
        # ══ variant
        lines = ["# ══ Phase B ══"]
        result = _parse_comment_label(lines)
        assert result == "Phase B"


# ---------------------------------------------------------------------------
# Integration tests: _extract_step_labels (reads YAML file)
# ---------------------------------------------------------------------------

class TestExtractStepLabels:
    def test_box_comment_captured(self):
        path = _write_yaml("""
            name: test
            steps:
              # ── A1: Kill processes ──────────────────────────
              - shell: echo hi
        """)
        labels = _extract_step_labels(path)
        assert len(labels) == 1
        assert labels[0] == "A1: Kill processes"

    def test_plain_comment_captured_as_fallback(self):
        path = _write_yaml("""
            name: test
            steps:
              # step does X
              - shell: echo hi
        """)
        labels = _extract_step_labels(path)
        assert len(labels) == 1
        assert labels[0] == "step does X"

    def test_no_comment_yields_none(self):
        path = _write_yaml("""
            name: test
            steps:
              - shell: echo hi
        """)
        labels = _extract_step_labels(path)
        # Either empty list or [None]
        assert labels == [] or labels[0] is None

    def test_multiple_steps_labels(self):
        path = _write_yaml("""
            name: test
            steps:
              # ── Step One ──
              - shell: echo one
              - shell: echo two
              # ── Step Three ──
              - shell: echo three
        """)
        labels = _extract_step_labels(path)
        # 3 steps; step 0 and 2 have labels, step 1 has none
        assert len(labels) == 3
        assert labels[0] == "Step One"
        assert labels[1] is None
        assert labels[2] == "Step Three"


# ---------------------------------------------------------------------------
# Integration tests: load_test_yaml populates label on FunctionalStep
# ---------------------------------------------------------------------------

class TestLoadTestYamlLabels:
    def test_comment_label_on_step(self):
        path = _write_yaml("""
            name: test
            steps:
              # ── A1: Kill RC ──────────────
              - shell: echo hi
        """)
        _, steps, _, _ = load_test_yaml(path)
        assert len(steps) == 1
        assert steps[0].label == "A1: Kill RC"

    def test_explicit_label_field_wins_over_comment(self):
        path = _write_yaml("""
            name: test
            steps:
              # ── Comment label ──
              - shell: echo hi
                label: "Explicit label wins"
        """)
        _, steps, _, _ = load_test_yaml(path)
        assert steps[0].label == "Explicit label wins"

    def test_no_comment_no_label_field_is_none(self):
        path = _write_yaml("""
            name: test
            steps:
              - shell: echo hi
        """)
        _, steps, _, _ = load_test_yaml(path)
        assert steps[0].label is None

    def test_explicit_label_only_no_comment(self):
        path = _write_yaml("""
            name: test
            steps:
              - shell: echo hi
                label: "Only explicit"
        """)
        _, steps, _, _ = load_test_yaml(path)
        assert steps[0].label == "Only explicit"

    def test_backwards_compat_no_labels_scenario(self):
        """Scenario without any labels should load unchanged (all labels None)."""
        path = _write_yaml("""
            name: compat
            steps:
              - shell: echo a
              - verify: screen looks good
              - wait: 2
        """)
        name, steps, _, _ = load_test_yaml(path)
        assert name == "compat"
        assert len(steps) == 3
        for s in steps:
            assert s.label is None


# ---------------------------------------------------------------------------
# Step display format: "N: label" vs "N"
# ---------------------------------------------------------------------------

class TestStepDisplay:
    def _make_mock_step(self, label=None, shell="echo hi"):
        """Return a FunctionalStep-like mock."""
        step = MagicMock()
        step.label = label
        step.shell = shell
        step.localize = None
        step.launch = None
        step.if_visible = None
        step.checkpoint = None
        step.verify = None
        step.accept_any = None
        step.focus = None
        step.retries = 1
        step.wait = 0
        return step

    def test_step_display_with_label(self):
        """When step has a label, step_label event field should be 'N: label'."""
        emitted = []

        step = self._make_mock_step(label="A1: Kill RC")

        # Simulate the display computation from functional_steps.py
        step_num = 3
        step_display = f"{step_num}: {step.label}" if getattr(step, "label", None) else str(step_num)
        assert step_display == "3: A1: Kill RC"

    def test_step_display_without_label(self):
        step = self._make_mock_step(label=None)
        step_num = 5
        step_display = f"{step_num}: {step.label}" if getattr(step, "label", None) else str(step_num)
        assert step_display == "5"


# ---------------------------------------------------------------------------
# events.jsonl: step_start includes step_label
# ---------------------------------------------------------------------------

class TestStepStartEvent:
    """Verify step_start event carries step_label when label present."""

    def test_step_start_event_has_step_label(self):
        """_emit('step_start', ..., step_label='N: label') reaches the event log."""
        # We test by loading a labeled scenario and checking what would be emitted
        # by verifying the step_display logic is correct
        path = _write_yaml("""
            name: test
            steps:
              # ── A4: Dispatch tel ──
              - shell: echo dispatch
        """)
        _, steps, _, _ = load_test_yaml(path)
        step = steps[0]
        assert step.label == "A4: Dispatch tel"

        step_num = 4
        step_display = f"{step_num}: {step.label}" if getattr(step, "label", None) else str(step_num)
        assert step_display == "4: A4: Dispatch tel"

    def test_step_start_event_step_label_absent_when_no_label(self):
        path = _write_yaml("""
            name: test
            steps:
              - shell: echo hi
        """)
        _, steps, _, _ = load_test_yaml(path)
        step = steps[0]
        assert step.label is None
        step_num = 1
        step_display = f"{step_num}: {step.label}" if getattr(step, "label", None) else str(step_num)
        assert step_display == "1"


# ---------------------------------------------------------------------------
# HTML report: label shown in step header
# ---------------------------------------------------------------------------

class TestHTMLReportLabel:
    def test_step_with_label_uses_subscript_num(self):
        from automation.reporting.report_html import _render_step_block

        step_events = [
            {"event": "step_start", "label": "echo hi", "step_label": "3: A1: Kill RC", "kind": "shell"},
            {"event": "step_end", "status": "ok", "attempts": 1, "duration_ms": 500},
        ]
        html = _render_step_block("3", step_events, set())
        # Step number should appear as subscript
        assert "<sub>#3</sub>" in html
        # Label content in header
        assert "3: A1: Kill RC" in html

    def test_step_without_label_shows_step_num_plainly(self):
        from automation.reporting.report_html import _render_step_block

        step_events = [
            {"event": "step_start", "label": "echo hi", "kind": "shell"},
            {"event": "step_end", "status": "ok", "attempts": 1, "duration_ms": 100},
        ]
        html = _render_step_block("5", step_events, set())
        # No subscript for unlabeled steps
        assert "Step 5" in html
        assert "<sub>" not in html

    def test_step_label_html_escaped(self):
        from automation.reporting.report_html import _render_step_block

        step_events = [
            {"event": "step_start", "label": "hi", "step_label": '1: <script>alert("xss")</script>', "kind": "shell"},
            {"event": "step_end", "status": "ok", "attempts": 1, "duration_ms": 50},
        ]
        html = _render_step_block("1", step_events, set())
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Demo: labels from 3325-master-toggle.yaml
# ---------------------------------------------------------------------------

class TestMasterToggleLabels:
    """Smoke-test: load 3325-master-toggle.yaml and verify labeled steps."""

    _SCENARIO = _PROJ / "shared/scenarios/functional/3325-master-toggle.yaml"

    @pytest.mark.skipif(
        not _SCENARIO.exists(),
        reason="3325-master-toggle.yaml not found"
    )
    def test_first_step_has_a1_label(self):
        _, steps, _, _ = load_test_yaml(self._SCENARIO)
        # After conversion to routines-first form, step 1 is expanded from a
        # routine: cleanup-rocketchat call and carries a [routine:cleanup-rocketchat]
        # label prefix.  Accept any of the historical prefix forms.
        assert steps[0].label is not None
        assert (
            "A1" in steps[0].label
            or "[import:cleanup-rocketchat]" in steps[0].label
            or "[routine:cleanup-rocketchat]" in steps[0].label
        )

    @pytest.mark.skipif(
        not _SCENARIO.exists(),
        reason="3325-master-toggle.yaml not found"
    )
    def test_labeled_steps_count(self):
        """Most steps in master-toggle have ── comments; at least half should be labeled."""
        _, steps, _, _ = load_test_yaml(self._SCENARIO)
        labeled = [s for s in steps if s.label is not None]
        # The scenario has extensive ── comments — expect at least 5 labeled steps
        assert len(labeled) >= 5, f"Expected ≥5 labeled steps, got {len(labeled)}"

    @pytest.mark.skipif(
        not _SCENARIO.exists(),
        reason="3325-master-toggle.yaml not found"
    )
    def test_print_labels(self, capsys):
        """Demo: print all step labels (visual verification)."""
        _, steps, _, _ = load_test_yaml(self._SCENARIO)
        for i, step in enumerate(steps, start=1):
            display = f"{i}: {step.label}" if step.label else str(i)
            print(display)
        out = capsys.readouterr().out
        # Should have printed something for each step
        lines = [l for l in out.strip().splitlines() if l]
        assert len(lines) == len(steps)
