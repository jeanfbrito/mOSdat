"""Tests for I7 — accept_any union verify.

Covers:
  - Schema validation: accept_any accepted, empty rejected, both verify+accept_any rejected
  - Runtime: first yes → pass, first no + second yes → pass, all no → fail
  - Logs include per-prompt verdict (vlm_verify events with kind=accept_any)
"""

from __future__ import annotations

import importlib.util
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
import yaml

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Load scenario module
# ---------------------------------------------------------------------------

def _load_scenario_mod():
    spec = importlib.util.spec_from_file_location(
        "automation.scenario",
        _PROJ / "automation" / "scenario.py",
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "automation"
    sys.modules["automation.scenario"] = mod
    spec.loader.exec_module(mod)
    return mod


_scenario_mod = _load_scenario_mod()
ScenarioModel = _scenario_mod.ScenarioModel

from pydantic import ValidationError  # noqa: E402


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------

def _validate(yaml_text: str):
    data = yaml.safe_load(yaml_text)
    return ScenarioModel.model_validate(data)


def test_accept_any_two_prompts_valid():
    _validate("""
steps:
  - accept_any:
      - "dial pad is open with empty field"
      - "main UI unchanged"
    verify_timeout: 15
""")


def test_accept_any_single_prompt_valid():
    _validate("""
steps:
  - accept_any:
      - "settings panel visible"
""")


def test_accept_any_empty_list_rejected():
    with pytest.raises(ValidationError, match="non-empty"):
        _validate("""
steps:
  - accept_any: []
""")


def test_accept_any_and_verify_together_rejected():
    with pytest.raises(ValidationError, match="mutually exclusive"):
        _validate("""
steps:
  - verify: "some prompt"
    accept_any:
      - "other prompt"
""")


def test_accept_any_with_verify_not_allowed():
    """verify_not is allowed alongside accept_any (it's on _StepBase)."""
    # accept_any does not currently use verify_not, but schema must not reject it
    _validate("""
steps:
  - accept_any:
      - "dial pad visible"
    verify_not: "crash dialog visible"
    verify_timeout: 10
""")


def test_plain_verify_still_works():
    """Backwards compat: plain verify: string still valid."""
    _validate("""
steps:
  - verify: "RC login page visible"
    verify_timeout: 20
""")


def test_example_scenario_validates():
    """The example-accept-any.yaml scenario passes schema validation."""
    path = _PROJ / "shared" / "scenarios" / "functional" / "linux" / "example-accept-any.yaml"
    data = yaml.safe_load(path.read_text())
    ScenarioModel.model_validate(data)


def test_global_shortcut_scenario_validates():
    """3325-global-shortcut.yaml (uses routines) passes schema validation after loader expansion."""
    from automation.runners.scenario_loader import load_test_yaml
    path = _PROJ / "shared" / "scenarios" / "functional" / "linux" / "3325-global-shortcut.yaml"
    # Use load_test_yaml: it expands `routine:` calls before model_validate.
    # Bare ScenarioModel.model_validate fails on raw `routine:` shorthand
    # because RoutineStep is not in the step union — routines are an authoring
    # convenience, not a runtime step type.
    load_test_yaml(path)


# ---------------------------------------------------------------------------
# Runtime tests — _VerifyMixin._verify_accept_any
# ---------------------------------------------------------------------------

def _make_verify_mixin(vlm_verdicts: list[bool]):
    """Build a _VerifyMixin instance with a stubbed VLM and screenshotter."""
    spec = importlib.util.spec_from_file_location(
        "automation.runners.functional_verify",
        _PROJ / "automation" / "runners" / "functional_verify.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # Stub TYPE_CHECKING imports
    mod.__package__ = "automation.runners"
    spec.loader.exec_module(mod)
    VerifyMixin = mod._VerifyMixin

    events = []

    class FakeInstance(VerifyMixin):
        def __init__(self):
            self.screenshot_dir = None
            self.log = lambda msg: None  # suppress output
            self._verdicts = list(vlm_verdicts)
            self._verdict_idx = 0
            # Mock screenshotter
            self.screenshotter = MagicMock()
            fake_img = MagicMock()
            self.screenshotter.capture.return_value = (fake_img, (1280, 800))
            self.screenshotter.wait_for_stable = MagicMock()
            # Mock VLM
            self.vlm = MagicMock()
            self.vlm.verify.side_effect = self._next_verdict

        def _next_verdict(self, *args, **kwargs):
            if self._verdict_idx < len(self._verdicts):
                v = self._verdicts[self._verdict_idx]
                self._verdict_idx += 1
                return v
            return False

        def _emit(self, event_type, **kwargs):
            events.append({"type": event_type, **kwargs})

        def _save_screenshot(self, *args, **kwargs):
            pass

    instance = FakeInstance()
    return instance, events


def test_accept_any_first_prompt_yes_passes():
    """First prompt returns yes → returns True immediately."""
    instance, events = _make_verify_mixin([True])
    result = instance._verify_accept_any(
        ["prompt A", "prompt B"],
        timeout=5,
        step_num=1,
    )
    assert result is True
    # Only one VLM call made (short-circuit on first yes)
    assert instance.vlm.verify.call_count == 1
    # Event logged with kind=accept_any and answer=yes
    accept_events = [e for e in events if e.get("kind") == "accept_any"]
    assert any(e["answer"] == "yes" for e in accept_events)


def test_accept_any_first_no_second_yes_passes():
    """First prompt no, second prompt yes → returns True."""
    instance, events = _make_verify_mixin([False, True])
    result = instance._verify_accept_any(
        ["prompt A", "prompt B"],
        timeout=10,
        step_num=2,
    )
    assert result is True
    assert instance.vlm.verify.call_count == 2
    accept_events = [e for e in events if e.get("kind") == "accept_any"]
    answers = [e["answer"] for e in accept_events]
    assert "no" in answers
    assert "yes" in answers


def test_accept_any_all_no_returns_false():
    """All prompts return no within timeout → returns False."""
    # Use a very short timeout so the test doesn't hang
    instance, events = _make_verify_mixin([False, False, False, False])
    result = instance._verify_accept_any(
        ["prompt A", "prompt B"],
        timeout=1,
        step_num=3,
    )
    assert result is False
    accept_events = [e for e in events if e.get("kind") == "accept_any"]
    assert all(e["answer"] == "no" for e in accept_events)


def test_accept_any_events_include_prompt_index():
    """Each VLM call emits an event with prompt_index field."""
    instance, events = _make_verify_mixin([False, True])
    instance._verify_accept_any(["p0", "p1"], timeout=5, step_num=4)
    accept_events = [e for e in events if e.get("kind") == "accept_any"]
    indices = [e["prompt_index"] for e in accept_events]
    assert 0 in indices
    assert 1 in indices


def test_accept_any_events_include_question_text():
    """Each event carries the prompt text (truncated to 80 chars)."""
    instance, events = _make_verify_mixin([True])
    instance._verify_accept_any(["my specific prompt text"], timeout=5, step_num=5)
    accept_events = [e for e in events if e.get("kind") == "accept_any"]
    assert any("my specific prompt text" in e["question"] for e in accept_events)
