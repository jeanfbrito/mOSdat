"""R3: tests for automation.routines.fixtures — Fixture schema + loader."""

from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from automation.routines.fixtures import Fixture, load_fixture, list_fixtures


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(content)
    return p


VALID_YAML = """\
name: my-fixture
description: A test fixture
vm_state:
  rc_killed: true
  userdata_wiped: true
  launched: false
setup_steps:
  - shell: echo hello
teardown_steps: []
"""

VALID_NO_STEPS_YAML = """\
name: bare-fixture
description: Minimal fixture with no steps
vm_state:
  rc_killed: true
"""


# ---------------------------------------------------------------------------
# 1. Schema validation — valid fixture parses correctly
# ---------------------------------------------------------------------------

def test_schema_valid_fixture():
    fx = Fixture.model_validate(yaml.safe_load(VALID_YAML))
    assert fx.name == "my-fixture"
    assert fx.description == "A test fixture"
    assert fx.vm_state == {"rc_killed": True, "userdata_wiped": True, "launched": False}
    assert len(fx.setup_steps) == 1
    assert fx.setup_steps[0] == {"shell": "echo hello"}
    assert fx.teardown_steps == []


# ---------------------------------------------------------------------------
# 2. Schema validation — invalid name (not kebab-case) raises
# ---------------------------------------------------------------------------

def test_schema_invalid_name_raises():
    with pytest.raises(ValidationError, match="kebab-case"):
        Fixture.model_validate({"name": "Bad_Name", "description": "bad"})


# ---------------------------------------------------------------------------
# 3. Schema validation — extra fields forbidden (strict-extra pattern)
# ---------------------------------------------------------------------------

def test_schema_extra_field_forbidden():
    with pytest.raises(ValidationError):
        Fixture.model_validate({
            "name": "my-fixture",
            "description": "x",
            "unknown_field": "should fail",
        })


# ---------------------------------------------------------------------------
# 4. load_fixture — valid file loads correctly
# ---------------------------------------------------------------------------

def test_load_valid_fixture(tmp_path):
    _write(tmp_path, "my-fixture", VALID_YAML)
    with patch("automation.routines.fixtures.fixtures_dir", return_value=tmp_path):
        load_fixture.cache_clear()
        fx = load_fixture("my-fixture")
    assert fx.name == "my-fixture"
    assert fx.vm_state["rc_killed"] is True


# ---------------------------------------------------------------------------
# 5. load_fixture — missing file raises FileNotFoundError
# ---------------------------------------------------------------------------

def test_load_missing_fixture_raises(tmp_path):
    with patch("automation.routines.fixtures.fixtures_dir", return_value=tmp_path):
        load_fixture.cache_clear()
        with pytest.raises(FileNotFoundError, match="not found"):
            load_fixture("nonexistent")


# ---------------------------------------------------------------------------
# 6. list_fixtures — returns all valid fixtures sorted by name
# ---------------------------------------------------------------------------

def test_list_fixtures_sorted(tmp_path):
    _write(tmp_path, "z-last", "name: z-last\ndescription: z\nvm_state: {}\n")
    _write(tmp_path, "a-first", "name: a-first\ndescription: a\nvm_state: {}\n")
    with patch("automation.routines.fixtures.fixtures_dir", return_value=tmp_path):
        load_fixture.cache_clear()
        result = list_fixtures()
    assert [f.name for f in result] == ["a-first", "z-last"]


# ---------------------------------------------------------------------------
# 7. vm_state shape — dict with arbitrary keys accepted
# ---------------------------------------------------------------------------

def test_vm_state_accepts_arbitrary_keys():
    fx = Fixture.model_validate({
        "name": "my-fixture",
        "description": "x",
        "vm_state": {
            "rc_killed": True,
            "userdata_wiped": True,
            "config": {"isTelephonyEnabled": True, "servers": []},
            "launched": False,
        },
    })
    assert fx.vm_state["config"]["isTelephonyEnabled"] is True


# ---------------------------------------------------------------------------
# 8. setup_steps — validates as list of dicts
# ---------------------------------------------------------------------------

def test_setup_steps_must_be_dicts():
    with pytest.raises(ValidationError):
        Fixture.model_validate({
            "name": "my-fixture",
            "description": "x",
            "vm_state": {},
            "setup_steps": ["not a dict"],
        })
