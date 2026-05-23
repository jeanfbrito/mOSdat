"""R1: expand_call tests for automation.routines.runner."""

from pathlib import Path
from unittest.mock import patch

import pytest

from automation.routines.schema import Routine, RoutineFallback, RoutineInput
from automation.routines.runner import expand_call


def _make_routine(**kwargs) -> Routine:
    defaults = dict(name="my-routine", description="test", steps=[])
    defaults.update(kwargs)
    return Routine.model_validate(defaults)


def _patch_load(routine: Routine):
    """Context manager that patches load_routine to return a fixed Routine."""
    from unittest.mock import patch as _patch
    return _patch("automation.routines.runner.load_routine", return_value=routine)


# ---------------------------------------------------------------------------
# Basic call shapes
# ---------------------------------------------------------------------------

def test_expand_short_call():
    r = _make_routine(steps=[{"shell": "echo hi"}])
    with _patch_load(r):
        result = expand_call({"routine": "my-routine"}, {})
    shells = [s.get("shell") for s in result if "shell" in s]
    assert "echo hi" in shells


def test_expand_long_call_with_with():
    r = _make_routine(
        inputs={"url": RoutineInput(name="url")},
        steps=[{"shell": "open {{ url }}"}],
    )
    with _patch_load(r):
        result = expand_call(
            {"routine": {"name": "my-routine", "with": {"url": "https://x.com"}}},
            {},
        )
    shells = [s.get("shell") for s in result if "shell" in s]
    assert any("https://x.com" in s for s in shells)


# ---------------------------------------------------------------------------
# Input resolution priority
# ---------------------------------------------------------------------------

def test_input_call_arg_beats_parent_var():
    r = _make_routine(
        inputs={"x": RoutineInput(name="x")},
        steps=[{"shell": "echo {{ x }}"}],
    )
    with _patch_load(r):
        result = expand_call(
            {"routine": {"name": "my-routine", "with": {"x": "from-call"}}},
            {"x": "from-var"},
        )
    shells = [s.get("shell") for s in result if "shell" in s]
    assert any("from-call" in s for s in shells)


def test_input_parent_var_used_when_no_call_arg():
    r = _make_routine(
        inputs={"x": RoutineInput(name="x")},
        steps=[{"shell": "echo {{ x }}"}],
    )
    with _patch_load(r):
        result = expand_call({"routine": "my-routine"}, {"x": "from-var"})
    shells = [s.get("shell") for s in result if "shell" in s]
    assert any("from-var" in s for s in shells)


def test_input_default_used_when_optional():
    r = _make_routine(
        inputs={"x": RoutineInput(name="x", required=False, default="default-val")},
        steps=[{"shell": "echo {{ x }}"}],
    )
    with _patch_load(r):
        result = expand_call({"routine": "my-routine"}, {})
    shells = [s.get("shell") for s in result if "shell" in s]
    assert any("default-val" in s for s in shells)


def test_missing_required_input_raises():
    r = _make_routine(
        inputs={"url": RoutineInput(name="url", required=True)},
        steps=[{"shell": "open {{ url }}"}],
    )
    with _patch_load(r):
        with pytest.raises(ValueError, match="required input"):
            expand_call({"routine": "my-routine"}, {})


# ---------------------------------------------------------------------------
# Jinja substitution into steps
# ---------------------------------------------------------------------------

def test_jinja_subst_in_steps():
    r = _make_routine(
        inputs={"title": RoutineInput(name="title")},
        steps=[{"verify": "window titled {{ title }} is visible"}],
    )
    with _patch_load(r):
        result = expand_call(
            {"routine": {"name": "my-routine", "with": {"title": "Workspace"}}},
            {},
        )
    verifies = [s.get("verify") for s in result if "verify" in s]
    assert any("Workspace" in v for v in verifies)


# ---------------------------------------------------------------------------
# Fallback selection
# ---------------------------------------------------------------------------

def test_fallback_when_capability_matches():
    fallback_steps = [{"shell": "echo fallback"}]
    default_steps = [{"shell": "echo default"}]
    r = _make_routine(
        steps=default_steps,
        fallbacks=[
            RoutineFallback(when="capability.wayland", steps=fallback_steps)
        ],
    )
    with _patch_load(r):
        result = expand_call(
            {"routine": "my-routine"},
            {},
            capability_manifest={"wayland": True},
        )
    shells = [s.get("shell") for s in result if "shell" in s and not s.get("_routine_event")]
    assert any("fallback" in s for s in shells)


def test_fallback_no_manifest_uses_default():
    fallback_steps = [{"shell": "echo fallback"}]
    default_steps = [{"shell": "echo default"}]
    r = _make_routine(
        steps=default_steps,
        fallbacks=[
            RoutineFallback(when="capability.wayland", steps=fallback_steps)
        ],
    )
    with _patch_load(r):
        result = expand_call(
            {"routine": "my-routine"},
            {},
            capability_manifest=None,
        )
    shells = [s.get("shell") for s in result if "shell" in s and not s.get("_routine_event")]
    assert any("default" in s for s in shells)


# ---------------------------------------------------------------------------
# Nested routine expansion
# ---------------------------------------------------------------------------

def test_nested_routine_expansion():
    inner = _make_routine(name="inner-routine", steps=[{"shell": "echo inner"}])
    outer = _make_routine(
        name="outer-routine",
        steps=[{"routine": "inner-routine"}, {"shell": "echo outer"}],
    )

    def _load(name, platform=None):
        if name == "inner-routine":
            return inner
        return outer

    with patch("automation.routines.runner.load_routine", side_effect=_load):
        result = expand_call({"routine": "outer-routine"}, {})

    shells = [s.get("shell") for s in result if "shell" in s and not s.get("_routine_event")]
    assert "echo inner" in shells
    assert "echo outer" in shells


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------

def test_cycle_detection_raises():
    r = _make_routine(steps=[{"routine": "my-routine"}])
    with _patch_load(r):
        with pytest.raises(RuntimeError, match="Cycle detected"):
            expand_call({"routine": "my-routine"}, {})
