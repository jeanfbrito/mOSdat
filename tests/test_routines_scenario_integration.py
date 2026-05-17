"""R1: scenario_loader integration tests for routine: expansion."""

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from automation.routines.schema import Routine, RoutineInput


def _make_routine_yaml(name: str, steps: list) -> str:
    return yaml.dump({"name": name, "description": "test", "steps": steps})


def _write_scenario(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test-scenario.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def _load(path: Path):
    from automation.runners.scenario_loader import load_test_yaml
    return load_test_yaml(path)


def _patch_load_routine(routine: Routine):
    return patch("automation.routines.runner.load_routine", return_value=routine)


def _patch_load_routine_map(mapping: dict):
    def _load(name):
        if name not in mapping:
            raise FileNotFoundError(f"Routine {name!r} not found")
        return mapping[name]
    return patch("automation.routines.runner.load_routine", side_effect=_load)


# ---------------------------------------------------------------------------
# 1. Scenario with - routine: validates + expands correctly
# ---------------------------------------------------------------------------

def test_scenario_with_routine_expands(tmp_path):
    routine = Routine.model_validate({
        "name": "my-routine",
        "description": "A routine",
        "steps": [{"shell": "echo from-routine"}],
    })
    scenario = """\
        name: test
        steps:
          - routine: my-routine
          - shell: echo direct
    """
    path = _write_scenario(tmp_path, scenario)
    with _patch_load_routine(routine):
        name, steps, vars_, _ = _load(path)
    shells = [s.shell for s in steps if s.shell]
    assert "echo from-routine" in shells
    assert "echo direct" in shells


# ---------------------------------------------------------------------------
# 2. Cycle in routines errors at load time
# ---------------------------------------------------------------------------

def test_cycle_in_routines_errors_at_load_time(tmp_path):
    # routine-a calls routine-a (self-cycle)
    routine = Routine.model_validate({
        "name": "routine-a",
        "description": "self-cycle",
        "steps": [{"routine": "routine-a"}],
    })
    scenario = """\
        name: test
        steps:
          - routine: routine-a
    """
    path = _write_scenario(tmp_path, scenario)
    with _patch_load_routine(routine):
        with pytest.raises(RuntimeError, match="[Cc]ycle"):
            _load(path)


# ---------------------------------------------------------------------------
# 3. Unknown routine name errors at load time
# ---------------------------------------------------------------------------

def test_unknown_routine_errors_at_load_time(tmp_path):
    from automation.routines.loader import load_routine
    # Use real routines_dir which won't have this routine
    scenario = """\
        name: test
        steps:
          - routine: definitely-not-a-real-routine-xyz
    """
    path = _write_scenario(tmp_path, scenario)
    # Patch routines_dir to empty tmp so load_routine raises FileNotFoundError
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        load_routine.cache_clear()
        with pytest.raises((FileNotFoundError, RuntimeError)):
            _load(path)


# ---------------------------------------------------------------------------
# 4. Routine input mismatch errors at load time
# ---------------------------------------------------------------------------

def test_routine_missing_required_input_errors_at_load_time(tmp_path):
    routine = Routine.model_validate({
        "name": "needs-url",
        "description": "needs url",
        "inputs": {
            "url": {"name": "url", "required": True},
        },
        "steps": [{"shell": "open {{ url }}"}],
    })
    scenario = """\
        name: test
        steps:
          - routine: needs-url
    """
    path = _write_scenario(tmp_path, scenario)
    with _patch_load_routine(routine):
        with pytest.raises(ValueError, match="required input"):
            _load(path)
