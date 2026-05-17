"""R7: schema versioning tests for automation.routines."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from automation.routines import CURRENT_SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS
from automation.routines.schema import Routine
from automation.routines.loader import _migrate_to_current, load_routine


# ---------------------------------------------------------------------------
# 1. Explicit schema_version: v1 is accepted
# ---------------------------------------------------------------------------

def test_explicit_v1_accepted():
    r = Routine(name="my-routine", description="test", schema_version="v1", steps=[])
    assert r.schema_version == "v1"


# ---------------------------------------------------------------------------
# 2. Missing schema_version defaults to v1
# ---------------------------------------------------------------------------

def test_missing_schema_version_defaults_to_v1():
    # schema_version has a default of "v1", so omitting it must still pass.
    r = Routine(name="my-routine", description="test", steps=[])
    assert r.schema_version == CURRENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 3. Unknown future version raises ValidationError with clear message
# ---------------------------------------------------------------------------

def test_unknown_future_version_rejected():
    with pytest.raises(ValidationError, match="newer than this mosdat"):
        Routine(name="my-routine", description="test", schema_version="v99", steps=[])


def test_unknown_future_version_message_includes_versions():
    with pytest.raises(ValidationError) as exc_info:
        Routine(name="my-routine", description="test", schema_version="v99", steps=[])
    msg = str(exc_info.value)
    assert "v99" in msg
    assert CURRENT_SCHEMA_VERSION in msg


# ---------------------------------------------------------------------------
# 4. Migration hook is called during load_routine
# ---------------------------------------------------------------------------

def test_migration_hook_called(tmp_path):
    """_migrate_to_current is invoked for each loaded routine."""
    routine_yaml = "name: test-routine\ndescription: test\nsteps: []\n"
    (tmp_path / "test-routine.yaml").write_text(routine_yaml)

    call_log: list[dict] = []

    def _spy_migrate(data: dict) -> dict:
        call_log.append(dict(data))
        return data

    with (
        patch("automation.routines.loader.routines_dir", return_value=tmp_path),
        patch("automation.routines.loader._migrate_to_current", side_effect=_spy_migrate),
    ):
        load_routine.cache_clear()
        load_routine("test-routine")

    assert len(call_log) == 1, "Migration hook was not called exactly once"
    assert call_log[0]["name"] == "test-routine"


# ---------------------------------------------------------------------------
# 5. `mosdat routines version` prints current version
# ---------------------------------------------------------------------------

def test_cli_version_prints_current():
    result = subprocess.run(
        [sys.executable, "-m", "automation.main", "routines", "version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert CURRENT_SCHEMA_VERSION in result.stdout
    # The supported list is also printed
    assert "supported:" in result.stdout


# ---------------------------------------------------------------------------
# 6. Future migration stub interface (placeholder)
# ---------------------------------------------------------------------------

def test_migration_stub_is_idempotent():
    """_migrate_to_current returns data unchanged when already at current version."""
    data = {"schema_version": CURRENT_SCHEMA_VERSION, "name": "x", "steps": []}
    result = _migrate_to_current(dict(data))
    assert result["schema_version"] == CURRENT_SCHEMA_VERSION
    assert result == data
