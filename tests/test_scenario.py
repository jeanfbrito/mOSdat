"""Tests for I3 — pydantic scenario schema validation (automation/scenario.py).

Covers:
  - Bad YAMLs (missing steps, typo'd keys, wrong types) → ValidationError
  - Canonical smoke-linux.yaml parses clean
  - load_test_yaml exits 4 on schema violation (SystemExit)
"""

import sys
import types
import importlib.util
from pathlib import Path

import pytest
import yaml

_PROJ = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Load scenario module directly (avoid __init__ chain)
# ---------------------------------------------------------------------------

_smod = importlib.util.spec_from_file_location(
    "automation.scenario",
    _PROJ / "automation" / "scenario.py",
)
_scenario_mod = importlib.util.module_from_spec(_smod)
_scenario_mod.__package__ = "automation"
sys.modules["automation.scenario"] = _scenario_mod
_smod.loader.exec_module(_scenario_mod)

ScenarioModel = _scenario_mod.ScenarioModel

from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Load functional.py (needed for load_test_yaml exit-4 tests)
# ---------------------------------------------------------------------------

for _sn in ("automation.vlm.client", "automation.vlm.input", "automation.vlm.screenshot"):
    if _sn not in sys.modules:
        _m = types.ModuleType(_sn)
        _m.VLMClient = object
        _m.InputInjector = object
        _m.Screenshotter = object
        _m.VLMError = Exception
        sys.modules[_sn] = _m

if "automation.vlm.agent" not in sys.modules:
    _agent_mod = types.ModuleType("automation.vlm.agent")
    from unittest.mock import MagicMock
    _agent_mod.AgentLoop = MagicMock
    sys.modules["automation.vlm.agent"] = _agent_mod

_fspec = importlib.util.spec_from_file_location(
    "automation.runners.functional",
    _PROJ / "automation" / "runners" / "functional.py",
)
_fmod = importlib.util.module_from_spec(_fspec)
_fmod.__package__ = "automation.runners"
sys.modules["automation.runners.functional"] = _fmod
_fspec.loader.exec_module(_fmod)

load_test_yaml = _fmod.load_test_yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate(data: dict):
    """Validate a raw dict through ScenarioModel. Raises ValidationError on failure."""
    return ScenarioModel.model_validate(data)


# ---------------------------------------------------------------------------
# I3a: missing steps key
# ---------------------------------------------------------------------------

class TestMissingSteps:
    def test_no_steps_key_raises(self):
        with pytest.raises(ValidationError, match="steps"):
            _validate({"name": "bad"})

    def test_empty_steps_ok(self):
        model = _validate({"name": "empty", "steps": []})
        assert model.steps == []


# ---------------------------------------------------------------------------
# I3b: typo'd step keys are rejected
# ---------------------------------------------------------------------------

class TestTypoKeys:
    def test_verfy_timeout_typo_rejected(self):
        data = {
            "steps": [
                {"shell": "echo hi", "verfy_timeout": 30}
            ]
        }
        with pytest.raises(ValidationError):
            _validate(data)

    def test_loaclize_typo_rejected(self):
        data = {
            "steps": [
                {"loaclize": "the login button"}
            ]
        }
        with pytest.raises(ValidationError):
            _validate(data)

    def test_verifiy_not_typo_rejected(self):
        data = {
            "steps": [
                {"shell": "echo", "verifiy_not": "error visible"}
            ]
        }
        with pytest.raises(ValidationError):
            _validate(data)


# ---------------------------------------------------------------------------
# I3c: wrong types
# ---------------------------------------------------------------------------

class TestWrongTypes:
    def test_verify_timeout_string_rejected(self):
        # verify_timeout must be int; passing a non-numeric string should fail
        data = {
            "steps": [
                {"shell": "echo", "verify_timeout": "not-an-int"}
            ]
        }
        with pytest.raises(ValidationError):
            _validate(data)

    def test_retries_negative_accepted(self):
        # pydantic accepts any int for retries — no range constraint defined
        model = _validate({"steps": [{"shell": "echo", "retries": -1}]})
        assert model.steps


# ---------------------------------------------------------------------------
# I3d: valid step variants parse cleanly
# ---------------------------------------------------------------------------

class TestValidSteps:
    def test_shell_step(self):
        model = _validate({"steps": [{"shell": "echo ok"}]})
        assert len(model.steps) == 1

    def test_launch_step(self):
        model = _validate({"steps": [{"launch": "/usr/bin/app", "wait": 10}]})
        assert len(model.steps) == 1

    def test_key_step(self):
        model = _validate({"steps": [{"key": "escape"}]})
        assert len(model.steps) == 1

    def test_localize_step(self):
        model = _validate({"steps": [
            {"localize": "the login button", "then_type": "hello", "then_key": "enter"}
        ]})
        assert len(model.steps) == 1

    def test_if_visible_step(self):
        model = _validate({"steps": [
            {"if_visible": "close button", "then": [{"key": "escape"}]}
        ]})
        assert len(model.steps) == 1

    def test_checkpoint_step(self):
        model = _validate({"steps": [{"checkpoint": "after-login"}]})
        assert len(model.steps) == 1

    def test_top_level_fields(self):
        model = _validate({
            "name": "smoke",
            "description": "A smoke test",
            "app": "Rocket.Chat",
            "vars": {"workspace_url": "https://example.com"},
            "steps": [{"shell": "echo ok"}],
        })
        assert model.name == "smoke"
        assert model.vars == {"workspace_url": "https://example.com"}

    def test_cleanup_list(self):
        model = _validate({
            "steps": [{"shell": "echo test"}],
            "cleanup": [{"shell": "pkill app"}],
        })
        assert model.cleanup is not None
        assert len(model.cleanup) == 1


# ---------------------------------------------------------------------------
# I3e: canonical smoke-linux.yaml parses clean
# ---------------------------------------------------------------------------

class TestCanonicalSmoke:
    def test_smoke_linux_yaml_parses_clean(self):
        yaml_path = _PROJ / "shared" / "scenarios" / "functional" / "rocketchat-smoke-linux.yaml"
        if not yaml_path.exists():
            pytest.skip("rocketchat-smoke-linux.yaml not found")
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        # Should not raise
        model = _validate(data)
        assert len(model.steps) > 0


# ---------------------------------------------------------------------------
# I3f: load_test_yaml exits 4 on schema violation
# ---------------------------------------------------------------------------

class TestLoadTestYamlExit4:
    def test_typo_key_causes_exit_4(self, tmp_path):
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(
            "name: bad\nsteps:\n  - shell: echo\n    verfy_timeout: 30\n"
        )
        with pytest.raises(SystemExit) as exc_info:
            load_test_yaml(str(bad_yaml))
        assert exc_info.value.code == 4

    def test_missing_steps_causes_exit_4(self, tmp_path):
        bad_yaml = tmp_path / "nosteps.yaml"
        bad_yaml.write_text("name: nosteps\ndescription: missing steps key\n")
        with pytest.raises(SystemExit) as exc_info:
            load_test_yaml(str(bad_yaml))
        assert exc_info.value.code == 4

    def test_valid_yaml_does_not_exit(self, tmp_path):
        good_yaml = tmp_path / "good.yaml"
        good_yaml.write_text(
            "name: good\nsteps:\n  - shell: echo ok\n"
        )
        # Should not raise SystemExit
        name, steps, vars_, ckpts = load_test_yaml(str(good_yaml))
        assert name == "good"
        assert len(steps) == 1
