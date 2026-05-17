"""R4: capability-aware fallback selection tests.

Tests for manifest auto-load, jinja2 when: evaluation, schema validation,
and the 'mosdat routines explain' CLI subcommand.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from automation.routines.runner import (
    _SENTINEL_TYPE,
    _eval_when,
    _load_default_manifest,
    _load_manifest_by_sha,
    _select_steps,
    expand_call,
)
from automation.routines.schema import Routine, RoutineFallback, RoutineInput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_DATA_DIR = Path(__file__).parent / "test_data" / "routines"


def _make_routine(**kwargs) -> Routine:
    defaults = dict(name="my-routine", description="test", steps=[])
    defaults.update(kwargs)
    return Routine.model_validate(defaults)


def _patch_load(routine: Routine):
    return patch("automation.routines.runner.load_routine", return_value=routine)


def _manifest_with_accelerators(acc: dict) -> dict:
    return {"asar_sha": "test1234", "accelerators": acc, "popups": {}}


# ---------------------------------------------------------------------------
# 1. Fallback selected when manifest matches: capability.accelerators['alt+w']
# ---------------------------------------------------------------------------

def test_fallback_selected_altw_swallowed():
    """Fallback fires when capability.accelerators['alt+w'] == 'swallowed'."""
    fallback_steps = [{"shell": "echo fallback-altw"}]
    default_steps = [{"shell": "echo default"}]
    r = _make_routine(
        steps=default_steps,
        fallbacks=[
            RoutineFallback(
                when="capability.accelerators['alt+w'] == 'swallowed'",
                steps=fallback_steps,
            )
        ],
    )
    manifest = _manifest_with_accelerators({"alt+w": "swallowed"})
    main_steps, fallback_used = _select_steps(r, manifest, {})
    assert fallback_used == "capability.accelerators['alt+w'] == 'swallowed'"
    assert main_steps == fallback_steps


# ---------------------------------------------------------------------------
# 2. Default fallback selected when no others match
# ---------------------------------------------------------------------------

def test_default_fallback_selected_when_no_others_match():
    """'default' fallback fires when no explicit condition matched."""
    default_fallback_steps = [{"shell": "echo default-fallback"}]
    r = _make_routine(
        steps=[{"shell": "echo main"}],
        fallbacks=[
            RoutineFallback(
                when="capability.accelerators['ctrl+,'] == 'no_accel'",
                steps=[{"shell": "echo conditional"}],
            ),
            RoutineFallback(when="default", steps=default_fallback_steps),
        ],
    )
    # Manifest does NOT have ctrl+, == 'no_accel'
    manifest = _manifest_with_accelerators({"ctrl+,": "ok"})
    main_steps, fallback_used = _select_steps(r, manifest, {})
    assert fallback_used == "default"
    assert main_steps == default_fallback_steps


# ---------------------------------------------------------------------------
# 3. No fallback selected when manifest is None
# ---------------------------------------------------------------------------

def test_no_fallback_when_manifest_is_none():
    """When manifest is None, all non-default fallbacks are skipped."""
    fallback_steps = [{"shell": "echo fallback"}]
    main_steps = [{"shell": "echo main"}]
    r = _make_routine(
        steps=main_steps,
        fallbacks=[
            RoutineFallback(
                when="capability.accelerators['alt+w'] == 'swallowed'",
                steps=fallback_steps,
            )
        ],
    )
    steps, fallback_used = _select_steps(r, None, {})
    assert fallback_used is None
    assert steps == main_steps


# ---------------------------------------------------------------------------
# 4. No fallback when expression returns false
# ---------------------------------------------------------------------------

def test_no_fallback_when_expression_false():
    """Fallback not selected when expression evaluates to False."""
    r = _make_routine(
        steps=[{"shell": "echo main"}],
        fallbacks=[
            RoutineFallback(
                when="capability.accelerators['alt+w'] == 'swallowed'",
                steps=[{"shell": "echo fallback"}],
            )
        ],
    )
    manifest = _manifest_with_accelerators({"alt+w": "pass"})  # NOT 'swallowed'
    steps, fallback_used = _select_steps(r, manifest, {})
    assert fallback_used is None
    assert steps == [{"shell": "echo main"}]


# ---------------------------------------------------------------------------
# 5. Invalid jinja syntax in when: fails schema validation
# ---------------------------------------------------------------------------

def test_invalid_jinja_when_fails_schema_validation():
    """Invalid jinja2 syntax in when: raises ValidationError at schema load time."""
    with pytest.raises(ValidationError, match="valid jinja2 expression"):
        RoutineFallback(
            when="capability.accelerators[[[",  # unclosed bracket — invalid
            steps=[{"shell": "echo x"}],
        )


def test_empty_when_fails_schema_validation():
    """Empty when: string raises ValidationError."""
    with pytest.raises(ValidationError, match="must not be empty"):
        RoutineFallback(when="", steps=[{"shell": "echo x"}])


# ---------------------------------------------------------------------------
# 6. Multiple matching fallbacks → first wins
# ---------------------------------------------------------------------------

def test_multiple_matching_fallbacks_first_wins():
    """When multiple fallbacks match, the first declared one wins."""
    r = _make_routine(
        steps=[{"shell": "echo main"}],
        fallbacks=[
            RoutineFallback(
                when="capability.wayland == true",
                steps=[{"shell": "echo first-match"}],
            ),
            RoutineFallback(
                when="capability.wayland == true",
                steps=[{"shell": "echo second-match"}],
            ),
        ],
    )
    manifest = {"wayland": True}
    steps, fallback_used = _select_steps(r, manifest, {})
    assert steps == [{"shell": "echo first-match"}]


# ---------------------------------------------------------------------------
# 7. Expression can reference inputs.X
# ---------------------------------------------------------------------------

def test_fallback_references_inputs():
    """when: expression can reference inputs dict."""
    r = _make_routine(
        steps=[{"shell": "echo main"}],
        fallbacks=[
            RoutineFallback(
                when="inputs.accel_key == 'override'",
                steps=[{"shell": "echo inputs-fallback"}],
            )
        ],
    )
    manifest = {}  # empty manifest — inputs-based fallback should still fire
    resolved_inputs = {"accel_key": "override"}
    steps, fallback_used = _select_steps(r, manifest, resolved_inputs)
    assert fallback_used == "inputs.accel_key == 'override'"
    assert steps == [{"shell": "echo inputs-fallback"}]


# ---------------------------------------------------------------------------
# 8. Expression can reference vars.X (parent scenario vars)
# ---------------------------------------------------------------------------

def test_fallback_references_parent_vars():
    """when: expression can reference vars dict (parent scenario vars)."""
    r = _make_routine(
        steps=[{"shell": "echo main"}],
        fallbacks=[
            RoutineFallback(
                when="vars.env == 'ci'",
                steps=[{"shell": "echo ci-fallback"}],
            )
        ],
    )
    manifest = {}
    steps, fallback_used = _select_steps(r, manifest, {}, parent_vars={"env": "ci"})
    assert fallback_used == "vars.env == 'ci'"
    assert steps == [{"shell": "echo ci-fallback"}]


# ---------------------------------------------------------------------------
# 9. Auto-load picks latest-mtime manifest from shared/binary_capabilities/
# ---------------------------------------------------------------------------

def test_auto_load_picks_latest_mtime(tmp_path):
    """_load_default_manifest() returns the manifest with the most recent mtime."""
    cap_dir = tmp_path / "binary_capabilities"
    cap_dir.mkdir()

    old = cap_dir / "old_sha.json"
    new = cap_dir / "new_sha.json"

    old.write_text(json.dumps({"asar_sha": "old", "accelerators": {"ctrl+,": "ok"}}))
    new.write_text(json.dumps({"asar_sha": "new", "accelerators": {"ctrl+,": "no_accel"}}))

    # Ensure new has a later mtime
    import time
    time.sleep(0.01)
    new.touch()

    # Reset cache so the patched directory is used
    import automation.routines.runner as _runner
    original_cache = _runner._manifest_cache
    _runner._manifest_cache = _runner._NOT_LOADED

    try:
        with patch.object(_runner, "_capabilities_dir", return_value=cap_dir):
            result = _load_default_manifest()
    finally:
        _runner._manifest_cache = original_cache

    assert result is not None
    assert result["asar_sha"] == "new"


# ---------------------------------------------------------------------------
# 10. Explicit binary_sha arg overrides auto-load
# ---------------------------------------------------------------------------

def test_explicit_binary_sha_overrides_autoload(tmp_path):
    """expand_call with binary_sha loads the specific manifest file."""
    cap_dir = tmp_path / "binary_capabilities"
    cap_dir.mkdir()

    sha = "abc123def456"
    specific = cap_dir / f"{sha}.json"
    specific.write_text(
        json.dumps({"asar_sha": sha, "accelerators": {"alt+w": "swallowed"}})
    )

    r = _make_routine(
        steps=[{"shell": "echo main"}],
        fallbacks=[
            RoutineFallback(
                when="capability.accelerators['alt+w'] == 'swallowed'",
                steps=[{"shell": "echo sha-fallback"}],
            )
        ],
    )

    import automation.routines.runner as _runner
    with patch.object(_runner, "_capabilities_dir", return_value=cap_dir):
        with _patch_load(r):
            result = expand_call(
                {"routine": "my-routine"},
                {},
                binary_sha=sha,
            )

    shells = [s.get("shell") for s in result if "shell" in s and not s.get("_routine_event")]
    assert any("sha-fallback" in s for s in shells)


# ---------------------------------------------------------------------------
# 11. Missing manifest dir → graceful no-op (no error)
# ---------------------------------------------------------------------------

def test_missing_manifest_dir_graceful_noop(tmp_path):
    """_load_default_manifest() returns None when directory doesn't exist."""
    nonexistent = tmp_path / "no_such_dir"

    import automation.routines.runner as _runner
    original_cache = _runner._manifest_cache
    _runner._manifest_cache = _runner._NOT_LOADED

    try:
        with patch.object(_runner, "_capabilities_dir", return_value=nonexistent):
            result = _load_default_manifest()
    finally:
        _runner._manifest_cache = original_cache

    assert result is None


# ---------------------------------------------------------------------------
# 12. mosdat routines explain <name> CLI prints expected output
# ---------------------------------------------------------------------------

def test_explain_cli_with_explain_example_routine(tmp_path, capsys):
    """mosdat routines explain prints fallback status + step list."""
    import yaml

    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(
        json.dumps({"asar_sha": "testsha1", "accelerators": {"alt+w": "swallowed"}})
    )

    test_yaml = _TEST_DATA_DIR / "explain-example.yaml"
    data = yaml.safe_load(test_yaml.read_text())

    import argparse
    from automation.routines.schema import Routine as _Routine
    from automation.commands.routines import _cmd_explain

    routine = _Routine.model_validate(data)
    args = argparse.Namespace(
        name="explain-example",
        manifest=str(manifest_file),
        with_inputs=[],
    )
    with patch("automation.commands.routines.load_routine", return_value=routine):
        rc = _cmd_explain(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "explain-example" in out
    assert "ACTIVE" in out
    # The alt+w expression should appear in the output
    assert "alt+w" in out or "swallowed" in out
    assert "shell:" in out


# ---------------------------------------------------------------------------
# Bonus: explain CLI with no manifest shows "none" path
# ---------------------------------------------------------------------------

def test_explain_cli_no_manifest_shows_main_steps(tmp_path, capsys):
    """mosdat routines explain without manifest reports no fallback active."""
    import yaml

    test_yaml = _TEST_DATA_DIR / "explain-example.yaml"
    data = yaml.safe_load(test_yaml.read_text())

    import argparse
    import automation.routines.runner as _runner
    from automation.routines.schema import Routine as _Routine
    from automation.commands.routines import _cmd_explain

    routine = _Routine.model_validate(data)

    original_cache = _runner._manifest_cache
    _runner._manifest_cache = None  # force no manifest
    try:
        args = argparse.Namespace(
            name="explain-example",
            manifest=None,
            with_inputs=[],
        )
        with patch("automation.commands.routines.load_routine", return_value=routine):
            rc = _cmd_explain(args)
    finally:
        _runner._manifest_cache = original_cache

    assert rc == 0
    out = capsys.readouterr().out
    assert "none" in out.lower() or "main steps" in out.lower()
