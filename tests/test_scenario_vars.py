"""Tests for I8 — jinja-style {{ var }} substitution in scenarios.

Covers:
  - Substitution into shell, verify, localize, accept_any, type/key/then_type
  - CLI override beats yaml default
  - Missing var → load-time error with key name
  - Empty vars block / no vars block both work unchanged
  - Numeric / bool var values cast to string
  - Schema validation: vars accepts dict[str, Any]
  - Example scenario (example-vars.yaml) passes schema validation
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

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


def _load_scenario_mod():
    return _load_mod("automation/scenario.py", "automation.scenario")


def _load_var_subst_mod():
    return _load_mod("automation/runners/var_subst.py", "automation.runners.var_subst")


_scenario_mod = _load_scenario_mod()
ScenarioModel = _scenario_mod.ScenarioModel

# Load var_subst once at module level so all tests share the same class objects
_var_subst_mod = _load_var_subst_mod()
MissingVarsError = _var_subst_mod.MissingVarsError

from pydantic import ValidationError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render(steps_raw: list[dict], vars: dict) -> list[dict]:
    return _var_subst_mod.render_steps(steps_raw, vars)


def _validate(yaml_text: str) -> ScenarioModel:
    data = yaml.safe_load(yaml_text)
    return ScenarioModel.model_validate(data)


# ---------------------------------------------------------------------------
# 1. Substitution into string fields
# ---------------------------------------------------------------------------

def test_substitution_in_shell():
    steps = [{"shell": "xdg-open tel:{{ number }}"}]
    result = _render(steps, {"number": "+491234"})
    assert result[0]["shell"] == "xdg-open tel:+491234"


def test_substitution_in_verify():
    steps = [{"verify": "modal shows '{{ title }}'"}]
    result = _render(steps, {"title": "My Server"})
    assert result[0]["verify"] == "modal shows 'My Server'"


def test_substitution_in_localize():
    steps = [{"localize": "button labeled '{{ label }}'", "click": "left"}]
    result = _render(steps, {"label": "Save"})
    assert result[0]["localize"] == "button labeled 'Save'"


def test_substitution_in_accept_any():
    steps = [{"accept_any": ["dial pad shows {{ number }}", "main UI shown"]}]
    result = _render(steps, {"number": "+49"})
    assert result[0]["accept_any"][0] == "dial pad shows +49"
    assert result[0]["accept_any"][1] == "main UI shown"


def test_substitution_in_then_type():
    steps = [{"then_type": "{{ username }}"}]
    result = _render(steps, {"username": "jean@test.com"})
    assert result[0]["then_type"] == "jean@test.com"


def test_substitution_in_key():
    steps = [{"key": "{{ shortcut }}"}]
    result = _render(steps, {"shortcut": "ctrl+shift+p"})
    assert result[0]["key"] == "ctrl+shift+p"


def test_substitution_in_type():
    steps = [{"type": "{{ password }}"}]
    result = _render(steps, {"password": "s3cret"})
    assert result[0]["type"] == "s3cret"


def test_substitution_in_focus():
    steps = [{"focus": "{{ app_name }} window"}]
    result = _render(steps, {"app_name": "Rocket.Chat"})
    assert result[0]["focus"] == "Rocket.Chat window"


def test_substitution_in_verify_not():
    steps = [{"verify": "main UI", "verify_not": "{{ error_msg }}"}]
    result = _render(steps, {"error_msg": "crash dialog"})
    assert result[0]["verify_not"] == "crash dialog"


# ---------------------------------------------------------------------------
# 2. CLI override beats yaml default
# ---------------------------------------------------------------------------

def test_cli_override_beats_yaml_default():
    """CLI vars shadow yaml vars for same key."""
    yaml_vars = {"title": "Default Workspace"}
    cli_vars = {"title": "Southlogic"}
    merged = {**yaml_vars, **cli_vars}

    steps = [{"verify": "modal shows '{{ title }}'"}]
    result = _render(steps, merged)
    assert result[0]["verify"] == "modal shows 'Southlogic'"


def test_cli_only_var():
    """Var defined only on CLI (not in yaml vars) is substituted."""
    cli_vars = {"host": "my.server.com"}
    steps = [{"shell": "ping {{ host }}"}]
    result = _render(steps, cli_vars)
    assert result[0]["shell"] == "ping my.server.com"


# ---------------------------------------------------------------------------
# 3. Missing var → load-time error with key name
# ---------------------------------------------------------------------------

def test_missing_var_raises_at_load_time():
    steps = [{"shell": "xdg-open tel:{{ missing_key }}"}]
    with pytest.raises(MissingVarsError, match="missing_key"):
        _render(steps, {})


def test_missing_var_error_includes_step_index():
    steps = [
        {"shell": "echo ok"},
        {"verify": "screen shows {{ undefined_var }}"},
    ]
    with pytest.raises(MissingVarsError, match="Step 1"):
        _render(steps, {})


def test_missing_var_in_accept_any_raises():
    steps = [{"accept_any": ["good prompt", "{{ no_such_var }}"]}]
    with pytest.raises(MissingVarsError, match="no_such_var"):
        _render(steps, {})


# ---------------------------------------------------------------------------
# 4. Empty vars / no vars block — unchanged behaviour
# ---------------------------------------------------------------------------

def test_no_vars_block_passes_through_unchanged():
    steps = [{"shell": "echo hello"}, {"verify": "terminal shows hello"}]
    result = _render(steps, {})
    assert result[0]["shell"] == "echo hello"
    assert result[1]["verify"] == "terminal shows hello"


def test_empty_vars_block_passes_through_unchanged():
    steps = [{"shell": "echo hi"}]
    result = _render(steps, {})
    assert result[0]["shell"] == "echo hi"


def test_no_template_markers_with_vars_passes_through():
    """String fields without {{ }} are untouched even when vars are provided."""
    steps = [{"shell": "echo static"}]
    result = _render(steps, {"unused_var": "value"})
    assert result[0]["shell"] == "echo static"


# ---------------------------------------------------------------------------
# 5. Numeric / bool var values cast to string
# ---------------------------------------------------------------------------

def test_numeric_var_cast_to_string():
    steps = [{"shell": "sleep {{ seconds }}"}]
    result = _render(steps, {"seconds": 5})
    assert result[0]["shell"] == "sleep 5"


def test_bool_var_cast_to_string():
    steps = [{"verify": "feature enabled: {{ enabled }}"}]
    result = _render(steps, {"enabled": True})
    assert result[0]["verify"] == "feature enabled: True"


def test_float_var_cast_to_string():
    steps = [{"shell": "echo {{ ratio }}"}]
    result = _render(steps, {"ratio": 3.14})
    assert result[0]["shell"] == "echo 3.14"


# ---------------------------------------------------------------------------
# 6. Schema validation: vars accepts dict[str, Any]
# ---------------------------------------------------------------------------

def test_schema_vars_string_values():
    _validate("""
vars:
  workspace_title: "Workspace"
  dial_number: "+49123"
steps:
  - shell: echo ok
""")


def test_schema_vars_numeric_values():
    """vars: dict values may be numbers — schema allows Any."""
    _validate("""
vars:
  retries: 3
  timeout: 30.5
steps:
  - shell: echo ok
""")


def test_schema_vars_bool_values():
    """vars: dict values may be bools — schema allows Any."""
    _validate("""
vars:
  enabled: true
steps:
  - shell: echo ok
""")


def test_schema_no_vars_block():
    """Scenario without vars: block is valid."""
    _validate("""
steps:
  - shell: echo ok
""")


def test_schema_empty_vars_block():
    """Empty vars: {} is valid."""
    _validate("""
vars: {}
steps:
  - shell: echo ok
""")


# ---------------------------------------------------------------------------
# 7. Example scenario validates against schema
# ---------------------------------------------------------------------------

def test_example_vars_scenario_validates():
    path = _PROJ / "shared" / "scenarios" / "functional" / "linux" / "example-vars.yaml"
    data = yaml.safe_load(path.read_text())
    ScenarioModel.model_validate(data)


# ---------------------------------------------------------------------------
# 8. Multiple vars in one string
# ---------------------------------------------------------------------------

def test_multiple_vars_in_one_string():
    steps = [{"shell": "xdg-open {{ scheme }}:{{ number }}"}]
    result = _render(steps, {"scheme": "tel", "number": "+1234"})
    assert result[0]["shell"] == "xdg-open tel:+1234"


def test_whitespace_variants_in_template():
    """{{ key }}, {{key}}, and {{  key  }} should all expand."""
    var_subst = _load_var_subst_mod()
    steps = [
        {"shell": "a={{key}} b={{ key }} c={{  key  }}"},
    ]
    result = _render(steps, {"key": "X"})
    assert result[0]["shell"] == "a=X b=X c=X"
