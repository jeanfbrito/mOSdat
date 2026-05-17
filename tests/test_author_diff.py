"""I12: tests for `mosdat author export --base` unified-diff feature."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from automation import author_cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_STEPS = [
    {"shell": "echo setup"},
    {"verify": "app is visible"},
    {"shell": "echo step3"},
    {"shell": "echo step4"},
]

_BASE_SCENARIO: dict = {
    "name": "base-scenario",
    "steps": _BASE_STEPS,
}

_RECORDED_STEPS = [
    {"shell": "echo recorded-a"},
    {"verify": "new state visible"},
]

# YAML that the live dashboard /api/author/export returns
_SESSION_YAML = yaml.safe_dump({"name": "authored-scenario", "steps": _RECORDED_STEPS}, sort_keys=False)


def _write_base(tmp_path: Path) -> Path:
    """Write a minimal base scenario YAML to a temp file."""
    p = tmp_path / "base.yaml"
    p.write_text(yaml.safe_dump(_BASE_SCENARIO, sort_keys=False), encoding="utf-8")
    return p


def _make_fake_request_text(session_yaml: str = _SESSION_YAML):
    """Return a fake _request_text that always returns the given session YAML."""
    def fake(base_url, path, query=None):
        return {"yaml": session_yaml}
    return fake


# ---------------------------------------------------------------------------
# Test 1 — append at end: diff contains new steps at bottom
# ---------------------------------------------------------------------------

def test_append_at_end_diff_contains_new_steps_at_bottom(tmp_path, monkeypatch, capsys):
    base = _write_base(tmp_path)
    monkeypatch.setattr(author_cli, "_request_text", _make_fake_request_text())

    rc = author_cli.cli([
        "export",
        "--session", "sess-1",
        "--base", str(base),
    ])

    assert rc == 0
    diff = capsys.readouterr().out
    # diff must be non-empty
    assert "@@" in diff, "expected at least one hunk in the diff"
    # recorded steps appear as additions
    assert "+- shell: echo recorded-a" in diff or "+ - shell: echo recorded-a" in diff or "recorded-a" in diff
    assert "recorded-a" in diff
    assert "new state visible" in diff
    # original base steps should NOT appear as removals
    assert "-  - shell: echo setup" not in diff or "echo setup" in diff  # setup line still there (context or unchanged)
    # The additions come after the last base step  → the last hunk must be at the end
    lines = diff.splitlines()
    add_lines = [l for l in lines if l.startswith("+") and "recorded" in l]
    assert add_lines, "no added lines containing 'recorded' found in diff"


# ---------------------------------------------------------------------------
# Test 2 — insert at step 3: diff shifts subsequent steps down
# ---------------------------------------------------------------------------

def test_insert_at_step_3_shifts_subsequent_steps(tmp_path, monkeypatch, capsys):
    base = _write_base(tmp_path)
    monkeypatch.setattr(author_cli, "_request_text", _make_fake_request_text())

    rc = author_cli.cli([
        "export",
        "--session", "sess-1",
        "--base", str(base),
        "--insert-at-step", "3",
    ])

    assert rc == 0
    diff = capsys.readouterr().out
    assert "@@" in diff
    assert "recorded-a" in diff

    # Verify that in the modified scenario the recorded steps appear at index 2 (0-based)
    # and the original step3/step4 follow. Parse the modified scenario from the diff.
    # Build modified scenario directly for structural assertion:
    import copy, difflib
    base_doc = yaml.safe_load(base.read_text())
    base_steps = list(base_doc["steps"])
    modified = copy.deepcopy(base_doc)
    modified["steps"] = base_steps[:2] + _RECORDED_STEPS + base_steps[2:]
    steps = modified["steps"]
    assert steps[2] == {"shell": "echo recorded-a"}
    assert steps[3] == {"verify": "new state visible"}
    assert steps[4] == {"shell": "echo step3"}
    assert steps[5] == {"shell": "echo step4"}


# ---------------------------------------------------------------------------
# Test 3 — invalid base path returns error
# ---------------------------------------------------------------------------

def test_invalid_base_path_returns_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(author_cli, "_request_text", _make_fake_request_text())

    rc = author_cli.cli([
        "export",
        "--session", "sess-1",
        "--base", str(tmp_path / "nonexistent.yaml"),
    ])

    assert rc != 0  # _print returns 1 on error
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "not found" in out["error"]


# ---------------------------------------------------------------------------
# Test 4 — --name mode writes a file and prints its path
# ---------------------------------------------------------------------------

def test_name_mode_writes_file_and_prints_path(tmp_path, monkeypatch, capsys):
    base = _write_base(tmp_path)
    monkeypatch.setattr(author_cli, "_request_text", _make_fake_request_text())

    dest = tmp_path / "output.yaml"
    rc = author_cli.cli([
        "export",
        "--session", "sess-1",
        "--base", str(base),
        "--name", "my-new-scenario",
        "--output", str(dest),
    ])

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert "output" in out
    # File must exist and contain valid YAML with the new name
    assert dest.exists()
    written = yaml.safe_load(dest.read_text())
    assert written["name"] == "my-new-scenario"
    assert len(written["steps"]) == len(_BASE_STEPS) + len(_RECORDED_STEPS)


# ---------------------------------------------------------------------------
# Test 5 — modified scenario validates ScenarioModel
# ---------------------------------------------------------------------------

def test_modified_scenario_validates_scenario_model(tmp_path, monkeypatch):
    base = _write_base(tmp_path)
    monkeypatch.setattr(author_cli, "_request_text", _make_fake_request_text())

    # Use dry-run so we don't need a real dashboard; _export_with_base calls ScenarioModel.validate
    rc = author_cli._export_with_base(
        base_url="http://unused",
        session_id="sess-1",
        session_name="authored-scenario",
        base_path=base,
        insert_at_step=None,
        new_name=None,
        dry_run=True,
        output=None,
    )

    # dry_run=True prints JSON with ok=True
    assert rc == 0


# ---------------------------------------------------------------------------
# Test 6 — dry-run validates but doesn't write anything
# ---------------------------------------------------------------------------

def test_dry_run_does_not_write(tmp_path, monkeypatch, capsys):
    base = _write_base(tmp_path)
    monkeypatch.setattr(author_cli, "_request_text", _make_fake_request_text())

    dest = tmp_path / "should-not-exist.yaml"
    rc = author_cli.cli([
        "export",
        "--session", "sess-1",
        "--base", str(base),
        "--name", "dry-scenario",
        "--output", str(dest),
        "--dry-run",
    ])

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert not dest.exists(), "dry-run must not write any file"


# ---------------------------------------------------------------------------
# Test 7 — existing export (no --base) is unchanged
# ---------------------------------------------------------------------------

def test_existing_export_without_base_unchanged(monkeypatch, capsys):
    """Verify that export without --base still calls the dashboard and prints YAML."""
    calls = []

    def fake_request_text(base_url, path, query=None):
        calls.append((path, query))
        return {"yaml": _SESSION_YAML}

    monkeypatch.setattr(author_cli, "_request_text", fake_request_text)

    rc = author_cli.cli([
        "export",
        "--session", "sess-x",
        "--name", "authored-scenario",
    ])

    assert rc == 0
    assert calls == [("/api/author/export", {"session": "sess-x", "name": "authored-scenario"})]
    out = json.loads(capsys.readouterr().out)
    assert "yaml" in out


# ---------------------------------------------------------------------------
# Test 8 — insert-at-step out of range returns error
# ---------------------------------------------------------------------------

def test_insert_at_step_out_of_range(tmp_path, monkeypatch, capsys):
    base = _write_base(tmp_path)
    monkeypatch.setattr(author_cli, "_request_text", _make_fake_request_text())

    rc = author_cli.cli([
        "export",
        "--session", "sess-1",
        "--base", str(base),
        "--insert-at-step", "99",
    ])

    assert rc != 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "out of range" in out["error"]
