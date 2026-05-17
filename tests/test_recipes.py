"""Tests for mosdat recipes subcommand and recipe corpus.

Coverage:
- Schema validation of each of the 6 seed recipes
- list returns all valid recipes
- show returns body for known slug, errors for unknown
- search matches on title (case-insensitive substring)
- search matches on symptoms
- search no-match returns empty list with non-zero exit
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from automation.recipes.schema import Recipe
from automation.commands.recipes import _load_all, _recipes_dir, run_recipes

SEED_SLUGS = [
    "settings-electron-linux",
    "webview-focus-stealing",
    "vnc-keysyms-syntax",
    "vnc-framebuffer-virtio",
    "redux-persist-migrations-version",
    "second-instance-ipc-xauthority",
]

RECIPES_DIR = Path(__file__).parent.parent / "shared" / "recipes"


# ──────────────────────────────────────────────
# Schema validation — one test per seed recipe
# ──────────────────────────────────────────────


@pytest.mark.parametrize("slug", SEED_SLUGS)
def test_seed_recipe_schema_valid(slug: str) -> None:
    path = RECIPES_DIR / f"{slug}.yaml"
    assert path.exists(), f"Missing seed recipe file: {path}"
    data = yaml.safe_load(path.read_text())
    recipe = Recipe.model_validate(data)
    assert recipe.slug == slug
    assert recipe.title
    assert len(recipe.symptoms) >= 1
    assert recipe.constraint.strip()
    assert len(recipe.pivots) >= 1
    assert len(recipe.sources) >= 1
    for pivot in recipe.pivots:
        assert pivot.cost in ("low", "medium", "high", "external")
        assert isinstance(pivot.recommended, bool)


# ──────────────────────────────────────────────
# list
# ──────────────────────────────────────────────


def test_list_returns_all_valid_recipes(capsys) -> None:
    args = types.SimpleNamespace(recipes_command="list")
    rc = run_recipes(args)
    assert rc == 0
    out = capsys.readouterr().out
    for slug in SEED_SLUGS:
        assert slug in out


# ──────────────────────────────────────────────
# show
# ──────────────────────────────────────────────


def test_show_known_slug_prints_body(capsys) -> None:
    args = types.SimpleNamespace(recipes_command="show", slug="settings-electron-linux")
    rc = run_recipes(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "settings-electron-linux" in out
    assert "Opening Settings" in out


def test_show_unknown_slug_returns_nonzero(capsys) -> None:
    args = types.SimpleNamespace(recipes_command="show", slug="does-not-exist")
    rc = run_recipes(args)
    assert rc != 0


# ──────────────────────────────────────────────
# search
# ──────────────────────────────────────────────


def test_search_matches_title_case_insensitive(capsys) -> None:
    args = types.SimpleNamespace(recipes_command="search", query="CHROMIUM WEBVIEW")
    rc = run_recipes(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "webview-focus-stealing" in out


def test_search_matches_on_symptoms(capsys) -> None:
    # symptom unique to vnc-keysyms-syntax
    args = types.SimpleNamespace(recipes_command="search", query="ctrl+comma")
    rc = run_recipes(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "vnc-keysyms-syntax" in out


def test_search_no_match_returns_nonzero(capsys) -> None:
    args = types.SimpleNamespace(
        recipes_command="search",
        query="zzz_no_such_thing_xyzzy_99999",
    )
    rc = run_recipes(args)
    assert rc != 0


def test_search_matches_constraint_text(capsys) -> None:
    # constraint text unique to redux-persist-migrations-version
    args = types.SimpleNamespace(recipes_command="search", query="redux-persist")
    rc = run_recipes(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "redux-persist-migrations-version" in out


# ──────────────────────────────────────────────
# load_all skips invalid files with warning
# ──────────────────────────────────────────────


def test_load_all_skips_invalid_yaml(tmp_path, capsys) -> None:
    bad = tmp_path / "bad-recipe.yaml"
    bad.write_text("slug: bad-recipe\n# missing required fields\n")
    good_path = RECIPES_DIR / "settings-electron-linux.yaml"
    good_data = yaml.safe_load(good_path.read_text())

    import automation.commands.recipes as _mod

    def _fake_glob(pattern):
        from unittest.mock import MagicMock
        # return bad file first, then the real good file
        return iter([bad, good_path])

    fake_dir = tmp_path
    with patch.object(_mod, "_recipes_dir", return_value=fake_dir):
        with patch.object(Path, "glob", lambda self, pat: _fake_glob(pat)):
            entries = _load_all()

    # at least one None (the bad file)
    nones = [r for _, r in entries if r is None]
    assert nones, "Expected at least one invalid-recipe None entry"
    err = capsys.readouterr().err
    assert "WARNING" in err
