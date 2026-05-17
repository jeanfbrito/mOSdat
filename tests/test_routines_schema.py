"""R1: schema validation tests for automation.routines.schema."""

import pytest
from pydantic import ValidationError

from automation.routines.schema import Routine, RoutineFallback, RoutineInput


# ---------------------------------------------------------------------------
# RoutineInput
# ---------------------------------------------------------------------------

def test_routine_input_valid_minimal():
    inp = RoutineInput(name="url", type="string", required=True)
    assert inp.name == "url"
    assert inp.type == "string"
    assert inp.required is True


def test_routine_input_optional_with_default():
    inp = RoutineInput(name="retries", type="int", required=False, default=3)
    assert inp.default == 3


def test_routine_input_required_with_default_raises():
    with pytest.raises(ValidationError, match="required=True but default is set"):
        RoutineInput(name="x", required=True, default="oops")


# ---------------------------------------------------------------------------
# Routine — name validation
# ---------------------------------------------------------------------------

def test_routine_name_kebab_valid():
    r = Routine(name="launch-rocketchat", steps=[], description="test")
    assert r.name == "launch-rocketchat"


def test_routine_name_kebab_with_digits():
    r = Routine(name="setup-x11-v2", steps=[], description="test")
    assert r.name == "setup-x11-v2"


def test_routine_name_camel_case_rejected():
    with pytest.raises(ValidationError, match="kebab-case"):
        Routine(name="launchRocketChat", steps=[], description="test")


def test_routine_name_uppercase_rejected():
    with pytest.raises(ValidationError, match="kebab-case"):
        Routine(name="Launch-Chat", steps=[], description="test")


def test_routine_name_leading_hyphen_rejected():
    with pytest.raises(ValidationError, match="kebab-case"):
        Routine(name="-bad-name", steps=[], description="test")


# ---------------------------------------------------------------------------
# Routine — preconditions/postconditions must be verify steps
# ---------------------------------------------------------------------------

def test_precondition_verify_step_valid():
    r = Routine(
        name="my-routine",
        description="test",
        preconditions=[{"verify": "app is visible"}],
        steps=[],
    )
    assert len(r.preconditions) == 1


def test_postcondition_verify_not_valid():
    r = Routine(
        name="my-routine",
        description="test",
        postconditions=[{"verify_not": "error dialog visible"}],
        steps=[],
    )
    assert len(r.postconditions) == 1


def test_precondition_non_verify_rejected():
    with pytest.raises(ValidationError, match="must be a verify step"):
        Routine(
            name="my-routine",
            description="test",
            preconditions=[{"shell": "echo hello"}],
            steps=[],
        )


def test_postcondition_non_verify_rejected():
    with pytest.raises(ValidationError, match="must be a verify step"):
        Routine(
            name="my-routine",
            description="test",
            postconditions=[{"wait": 2}],
            steps=[],
        )


# ---------------------------------------------------------------------------
# Routine — full valid model
# ---------------------------------------------------------------------------

def test_full_valid_routine():
    r = Routine(
        name="open-settings",
        description="Opens RC settings panel",
        inputs={
            "timeout": RoutineInput(name="timeout", type="int", required=False, default=10)
        },
        preconditions=[{"verify": "RC window visible"}],
        steps=[{"shell": "echo step1"}, {"verify": "settings open"}],
        postconditions=[{"verify": "settings panel visible"}],
        tags=["ui", "settings"],
    )
    assert r.name == "open-settings"
    assert len(r.steps) == 2
    assert r.tags == ["ui", "settings"]


def test_routine_defaults():
    r = Routine(name="bare-routine", description="minimal")
    assert r.schema_version == "v1"
    assert r.steps == []
    assert r.preconditions == []
    assert r.postconditions == []
    assert r.fallbacks == []
    assert r.on_failure == []
    assert r.tags == []
