"""Tests for per-platform routine path resolution.

Covers:
  - ``shared/routines/<slug>.yaml`` is used when no platform is given (Linux default).
  - ``shared/routines/<platform>/<slug>.yaml`` is preferred when ``platform`` is set
    and a variant exists.
  - Top-level routine is used as a fallback when no platform-specific variant exists.
  - Both real shipped YAML files (Linux + Windows ``cleanup-rocketchat`` and
    ``launch-rocketchat``) load cleanly via the scenario_loader machinery.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from automation.routines.loader import (
    load_routine,
    resolve_routine_path,
    routines_dir,
)


_LINUX_YAML = """\
name: sample-routine
description: linux variant
steps:
  - shell: echo linux
"""

_WIN_YAML = """\
name: sample-routine
description: windows variant
steps:
  - shell: echo windows
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ---------------------------------------------------------------------------
# resolve_routine_path
# ---------------------------------------------------------------------------

def test_resolve_no_platform_uses_top_level(tmp_path):
    _write(tmp_path / "sample-routine.yaml", _LINUX_YAML)
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        p = resolve_routine_path("sample-routine")
    assert p == tmp_path / "sample-routine.yaml"


def test_resolve_with_platform_prefers_variant(tmp_path):
    _write(tmp_path / "sample-routine.yaml", _LINUX_YAML)
    _write(tmp_path / "windows" / "sample-routine.yaml", _WIN_YAML)
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        p = resolve_routine_path("sample-routine", platform="windows")
    assert p == tmp_path / "windows" / "sample-routine.yaml"


def test_resolve_with_platform_falls_back_to_top_level(tmp_path):
    # Only top-level exists; platform requested but no variant.
    _write(tmp_path / "sample-routine.yaml", _LINUX_YAML)
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        p = resolve_routine_path("sample-routine", platform="windows")
    assert p == tmp_path / "sample-routine.yaml"


# ---------------------------------------------------------------------------
# load_routine with platform
# ---------------------------------------------------------------------------

def test_load_routine_linux_uses_top_level(tmp_path):
    _write(tmp_path / "sample-routine.yaml", _LINUX_YAML)
    _write(tmp_path / "windows" / "sample-routine.yaml", _WIN_YAML)
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        load_routine.cache_clear()
        # No platform — Linux/default behaviour.
        r = load_routine("sample-routine")
    assert r.description == "linux variant"


def test_load_routine_linux_explicit_platform_uses_top_level(tmp_path):
    # Only top-level exists; platform=linux falls back to top-level.
    _write(tmp_path / "sample-routine.yaml", _LINUX_YAML)
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        load_routine.cache_clear()
        r = load_routine("sample-routine", platform="linux")
    assert r.description == "linux variant"


def test_load_routine_windows_uses_variant(tmp_path):
    _write(tmp_path / "sample-routine.yaml", _LINUX_YAML)
    _write(tmp_path / "windows" / "sample-routine.yaml", _WIN_YAML)
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        load_routine.cache_clear()
        r = load_routine("sample-routine", platform="windows")
    assert r.description == "windows variant"


def test_load_routine_missing_raises(tmp_path):
    with patch("automation.routines.loader.routines_dir", return_value=tmp_path):
        load_routine.cache_clear()
        with pytest.raises(FileNotFoundError):
            load_routine("nope", platform="windows")


# ---------------------------------------------------------------------------
# Real shipped YAMLs load cleanly
# ---------------------------------------------------------------------------

def test_real_linux_cleanup_loads():
    load_routine.cache_clear()
    r = load_routine("cleanup-rocketchat")
    assert r.name == "cleanup-rocketchat"
    # Linux variant lives at top-level shared/routines/.
    assert resolve_routine_path("cleanup-rocketchat") == routines_dir() / "cleanup-rocketchat.yaml"


def test_real_windows_cleanup_loads():
    load_routine.cache_clear()
    r = load_routine("cleanup-rocketchat", platform="windows")
    assert r.name == "cleanup-rocketchat"
    p = resolve_routine_path("cleanup-rocketchat", platform="windows")
    assert p == routines_dir() / "windows" / "cleanup-rocketchat.yaml"


def test_real_linux_launch_loads():
    load_routine.cache_clear()
    r = load_routine("launch-rocketchat")
    assert r.name == "launch-rocketchat"


def test_real_windows_launch_loads():
    load_routine.cache_clear()
    r = load_routine("launch-rocketchat", platform="windows")
    assert r.name == "launch-rocketchat"
    p = resolve_routine_path("launch-rocketchat", platform="windows")
    assert p == routines_dir() / "windows" / "launch-rocketchat.yaml"


# ---------------------------------------------------------------------------
# End-to-end: scenario_loader threads platform through
# ---------------------------------------------------------------------------

_SCENARIO_YAML = """\
name: tiny-scenario
steps:
  - routine: sample-routine
"""


def test_scenario_loader_uses_platform_routine(tmp_path, monkeypatch):
    """load_test_yaml(platform="windows") routes routine calls to the windows variant."""
    from automation.routines import loader as loader_mod

    routines_root = tmp_path / "routines"
    _write(routines_root / "sample-routine.yaml", _LINUX_YAML)
    _write(routines_root / "windows" / "sample-routine.yaml", _WIN_YAML)

    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(_SCENARIO_YAML)

    monkeypatch.setattr(loader_mod, "routines_dir", lambda: routines_root)
    load_routine.cache_clear()

    from automation.runners.scenario_loader import load_test_yaml

    # Windows: expanded shell step text comes from the windows variant.
    _, steps_win, _, _ = load_test_yaml(scenario_path, platform="windows")
    win_shells = [s.shell for s in steps_win if s.shell]
    assert any("echo windows" in s for s in win_shells)
    assert not any("echo linux" in s for s in win_shells)

    # Linux (no platform): top-level variant.
    load_routine.cache_clear()
    _, steps_lin, _, _ = load_test_yaml(scenario_path)
    lin_shells = [s.shell for s in steps_lin if s.shell]
    assert any("echo linux" in s for s in lin_shells)
    assert not any("echo windows" in s for s in lin_shells)
