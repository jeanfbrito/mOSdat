"""R1: loader tests for automation.routines.loader."""

import warnings
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from automation.routines.loader import list_routines, load_routine, routines_dir


_VALID_YAML = """\
name: test-routine
description: A test routine
steps:
  - shell: echo hello
"""

_INVALID_YAML = """\
name: BadName__notKebab
description: Invalid
steps: []
"""


def _write_routine(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# load_routine
# ---------------------------------------------------------------------------

def test_load_valid_routine(tmp_path):
    _write_routine(tmp_path, "test-routine", _VALID_YAML)
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        # Clear lru_cache to avoid stale entries from other tests
        from automation.routines.loader import load_routine as lr
        lr.cache_clear()
        routine = lr("test-routine")
    assert routine.name == "test-routine"
    assert routine.description == "A test routine"


def test_load_missing_routine_raises(tmp_path):
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        from automation.routines.loader import load_routine as lr
        lr.cache_clear()
        with pytest.raises(FileNotFoundError, match="not found"):
            lr("nonexistent")


def test_load_invalid_routine_raises(tmp_path):
    _write_routine(tmp_path, "bad-routine", _INVALID_YAML)
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        from automation.routines.loader import load_routine as lr
        lr.cache_clear()
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            lr("bad-routine")


# ---------------------------------------------------------------------------
# list_routines
# ---------------------------------------------------------------------------

def test_list_empty_dir(tmp_path):
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        result = list_routines()
    assert result == []


def test_list_missing_dir(tmp_path):
    missing = tmp_path / "no-such-dir"
    with patch("automation.routines.loader.routines_dir", return_value=missing):
        result = list_routines()
    assert result == []


def test_list_returns_sorted(tmp_path):
    _write_routine(tmp_path, "z-last", "name: z-last\ndescription: z\nsteps: []\n")
    _write_routine(tmp_path, "a-first", "name: a-first\ndescription: a\nsteps: []\n")
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        result = list_routines()
    assert [r.name for r in result] == ["a-first", "z-last"]


def test_list_skips_invalid_with_warning(tmp_path):
    _write_routine(tmp_path, "good-routine", _VALID_YAML)
    _write_routine(tmp_path, "bad-routine", _INVALID_YAML)
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = list_routines()
    assert len(result) == 1
    assert result[0].name == "test-routine"
    assert any("bad-routine" in str(warning.message) for warning in w)
