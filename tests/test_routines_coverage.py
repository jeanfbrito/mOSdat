"""R6: tests for automation.routines.coverage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from automation.routines.coverage import (
    find_scenario_references,
    find_last_test_results,
    build_report,
    render_markdown,
    ScenarioRef,
    TestResult,
    CoverageRow,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROUTINE_YAML = """\
name: open-settings
description: Open settings panel
schema_version: v1
tags: [settings, ui]
steps:
  - shell: echo open-settings
"""

_NESTED_ROUTINE_YAML = """\
name: cleanup-rocketchat
description: Kill RC
schema_version: v1
tags: [setup]
steps:
  - shell: echo cleanup
"""

_CALLING_ROUTINE_YAML = """\
name: launch-rocketchat
description: Launch RC
schema_version: v1
tags: [launch]
steps:
  - routine: cleanup-rocketchat
  - shell: echo launch
"""


def _write_scenario(path: Path, name: str, steps: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"name": name, "steps": steps}))


# ---------------------------------------------------------------------------
# 1. find_scenario_references — short form `- routine: name`
# ---------------------------------------------------------------------------

def test_find_references_short_form(tmp_path):
    rdir = tmp_path / "routines"
    rdir.mkdir()
    (rdir / "open-settings.yaml").write_text(_ROUTINE_YAML)

    sdir = tmp_path / "scenarios"
    (sdir / "functional").mkdir(parents=True)
    _write_scenario(
        sdir / "functional" / "test-a.yaml",
        "Test A",
        [{"routine": "open-settings"}],
    )

    with patch("automation.routines.coverage._project_root", return_value=tmp_path), \
         patch("automation.routines.loader.routines_dir", return_value=rdir):
        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        refs = find_scenario_references(scenarios_root=sdir)

    assert "open-settings" in refs
    assert len(refs["open-settings"]) == 1
    assert refs["open-settings"][0].scenario_name == "Test A"


# ---------------------------------------------------------------------------
# 2. find_scenario_references — long dict form `- routine: {name: ..., with: {...}}`
# ---------------------------------------------------------------------------

def test_find_references_long_form(tmp_path):
    rdir = tmp_path / "routines"
    rdir.mkdir()
    (rdir / "open-settings.yaml").write_text(_ROUTINE_YAML)

    sdir = tmp_path / "scenarios"
    (sdir / "functional").mkdir(parents=True)
    _write_scenario(
        sdir / "functional" / "test-b.yaml",
        "Test B",
        [{"routine": {"name": "open-settings", "with": {"url": "https://example.com"}}}],
    )

    with patch("automation.routines.coverage._project_root", return_value=tmp_path), \
         patch("automation.routines.loader.routines_dir", return_value=rdir):
        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        refs = find_scenario_references(scenarios_root=sdir)

    assert "open-settings" in refs
    assert refs["open-settings"][0].scenario_name == "Test B"


# ---------------------------------------------------------------------------
# 3. find_scenario_references — non-routine steps are ignored
# ---------------------------------------------------------------------------

def test_find_references_ignores_non_routine_steps(tmp_path):
    sdir = tmp_path / "scenarios"
    (sdir / "functional").mkdir(parents=True)
    _write_scenario(
        sdir / "functional" / "test-c.yaml",
        "Test C",
        [{"shell": "echo hello"}, {"verify": "Something visible"}],
    )

    with patch("automation.routines.coverage._project_root", return_value=tmp_path):
        refs = find_scenario_references(scenarios_root=sdir)
    assert refs == {}


# ---------------------------------------------------------------------------
# 4. find_scenario_references — walks nested routine steps
# ---------------------------------------------------------------------------

def test_find_references_nested_routine(tmp_path):
    rdir = tmp_path / "routines"
    rdir.mkdir()
    (rdir / "cleanup-rocketchat.yaml").write_text(_NESTED_ROUTINE_YAML)
    (rdir / "launch-rocketchat.yaml").write_text(_CALLING_ROUTINE_YAML)

    sdir = tmp_path / "scenarios"
    (sdir / "functional").mkdir(parents=True)
    _write_scenario(
        sdir / "functional" / "test-d.yaml",
        "Test D",
        [{"routine": "launch-rocketchat"}],
    )

    with patch("automation.routines.coverage._project_root", return_value=tmp_path), \
         patch("automation.routines.loader.routines_dir", return_value=rdir):
        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        refs = find_scenario_references(scenarios_root=sdir)

    # Direct reference
    assert "launch-rocketchat" in refs
    # Nested reference discovered via routine body
    assert "cleanup-rocketchat" in refs


# ---------------------------------------------------------------------------
# 5. find_scenario_references — missing/unreadable scenario YAML is skipped
# ---------------------------------------------------------------------------

def test_find_references_handles_missing_yaml(tmp_path):
    sdir = tmp_path / "scenarios"
    (sdir / "functional").mkdir(parents=True)
    bad = sdir / "functional" / "bad.yaml"
    bad.write_bytes(b"\x00\xff\xfe invalid binary")  # not valid YAML

    # Should not raise
    refs = find_scenario_references(scenarios_root=sdir)
    assert isinstance(refs, dict)


# ---------------------------------------------------------------------------
# 6. find_last_test_results — parses JSONL correctly
# ---------------------------------------------------------------------------

def test_find_last_test_results_parses_jsonl(tmp_path):
    history = tmp_path / "history.jsonl"
    history.write_text(
        json.dumps({
            "routine": "open-settings",
            "vm": "ubuntu2204",
            "fixture": "rc-launched",
            "exit_code": 0,
            "duration_ms": 5000,
            "timestamp": "2026-05-17T10:00:00Z",
        }) + "\n"
    )
    results = find_last_test_results(history_path=history)
    assert "open-settings" in results
    r = results["open-settings"]
    assert r.vm == "ubuntu2204"
    assert r.exit_code == 0
    assert r.timestamp == "2026-05-17T10:00:00Z"


# ---------------------------------------------------------------------------
# 7. find_last_test_results — picks most-recent entry per routine
# ---------------------------------------------------------------------------

def test_find_last_test_results_picks_most_recent(tmp_path):
    history = tmp_path / "history.jsonl"
    lines = [
        json.dumps({
            "routine": "open-settings", "vm": "ubuntu2204", "fixture": "",
            "exit_code": 1, "duration_ms": 1000, "timestamp": "2026-05-17T08:00:00Z",
        }),
        json.dumps({
            "routine": "open-settings", "vm": "fedora42", "fixture": "",
            "exit_code": 0, "duration_ms": 2000, "timestamp": "2026-05-17T09:00:00Z",
        }),
    ]
    history.write_text("\n".join(lines) + "\n")

    results = find_last_test_results(history_path=history)
    # Last line wins (most recently appended)
    assert results["open-settings"].vm == "fedora42"
    assert results["open-settings"].exit_code == 0


# ---------------------------------------------------------------------------
# 8. find_last_test_results — handles missing file gracefully
# ---------------------------------------------------------------------------

def test_find_last_test_results_missing_file(tmp_path):
    results = find_last_test_results(history_path=tmp_path / "nonexistent.jsonl")
    assert results == {}


# ---------------------------------------------------------------------------
# 9. build_report — status logic (unused / untested / failing / ok)
# ---------------------------------------------------------------------------

def test_build_report_status_logic(tmp_path):
    rdir = tmp_path / "routines"
    rdir.mkdir()
    for slug in ["r-unused", "r-untested", "r-failing", "r-ok"]:
        (rdir / f"{slug}.yaml").write_text(
            f"name: {slug}\ndescription: test\nschema_version: v1\ntags: []\nsteps: []\n"
        )

    sdir = tmp_path / "scenarios"
    (sdir / "functional").mkdir(parents=True)
    # r-untested, r-failing, r-ok all referenced by a scenario
    _write_scenario(
        sdir / "functional" / "test.yaml",
        "Big test",
        [
            {"routine": "r-untested"},
            {"routine": "r-failing"},
            {"routine": "r-ok"},
        ],
    )

    history = tmp_path / "history.jsonl"
    history.write_text(
        json.dumps({"routine": "r-failing", "vm": "u", "fixture": "", "exit_code": 1,
                    "duration_ms": 1, "timestamp": "2026-05-17T00:00:00Z"}) + "\n"
        + json.dumps({"routine": "r-ok", "vm": "u", "fixture": "", "exit_code": 0,
                      "duration_ms": 1, "timestamp": "2026-05-17T00:00:01Z"}) + "\n"
    )

    with patch("automation.routines.coverage._project_root", return_value=tmp_path), \
         patch("automation.routines.loader.routines_dir", return_value=rdir):
        from automation.routines.loader import load_routine as lr; lr.cache_clear()
        from automation.routines.loader import list_routines as listR
        rows = build_report(scenarios_root=sdir, history_path=history)

    by_name = {r.name: r for r in rows}
    assert by_name["r-unused"].status == "unused"
    assert by_name["r-untested"].status == "untested"
    assert by_name["r-failing"].status == "failing"
    assert by_name["r-ok"].status == "ok"


# ---------------------------------------------------------------------------
# 10. render_markdown — snapshot check
# ---------------------------------------------------------------------------

def test_render_markdown_snapshot():
    rows = [
        CoverageRow(
            name="open-settings",
            definition_path="shared/routines/open-settings.yaml",
            tags=["settings", "ui"],
            scenarios_using=[ScenarioRef("shared/scenarios/functional/3325.yaml", "Feature test", 5)],
            last_test_result=TestResult(
                routine="open-settings",
                vm="ubuntu2204",
                fixture="rc-launched",
                exit_code=0,
                duration_ms=4000,
                timestamp="2026-05-17T10:00:00Z",
            ),
            status="ok",
        ),
        CoverageRow(
            name="enable-telephony-toggle",
            definition_path="shared/routines/enable-telephony-toggle.yaml",
            tags=["telephony"],
            scenarios_using=[],
            last_test_result=None,
            status="unused",
        ),
    ]
    md = render_markdown(rows)
    assert "| Routine | Tags | Scenarios | Last test | Status |" in md
    assert "open-settings" in md
    assert "enable-telephony-toggle" in md
    assert "Total: 2" in md
    assert "ok: 1" in md
    assert "unused: 1" in md
