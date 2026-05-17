"""mosdat recipes — browse the known platform-constraint corpus.

Subcommands:
    mosdat recipes list                  # list all recipes (slug + title)
    mosdat recipes show <slug>           # full body
    mosdat recipes search "<query>"      # substring FTS on title / symptoms / constraint

Recipe files live at shared/recipes/<slug>.yaml relative to the repo root.
The repo root is resolved as the parent of the automation/ package directory.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import yaml

from automation.recipes.schema import Recipe


def _recipes_dir() -> Path:
    """Return the shared/recipes directory (repo-root relative)."""
    # automation/ package is two levels from repo root: <repo>/automation/commands/
    repo_root = Path(__file__).parent.parent.parent
    return repo_root / "shared" / "recipes"


def _load_all() -> list[tuple[Path, Optional[Recipe]]]:
    """Load every .yaml file in the recipes dir.

    Returns list of (path, recipe_or_None).  Invalid files get None + a warning.
    """
    results: list[tuple[Path, Optional[Recipe]]] = []
    rdir = _recipes_dir()
    if not rdir.is_dir():
        return results
    for path in sorted(rdir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
            recipe = Recipe.model_validate(data)
            results.append((path, recipe))
        except Exception as exc:
            print(f"[recipes] WARNING: skipping {path.name}: {exc}", file=sys.stderr)
            results.append((path, None))
    return results


def _cmd_list(args) -> int:  # noqa: ARG001
    entries = _load_all()
    if not entries:
        print("[recipes] No recipes found.")
        return 0
    valid = [(p, r) for p, r in entries if r is not None]
    if not valid:
        print("[recipes] No valid recipes found.")
        return 1
    col = max(len(r.slug) for _, r in valid) + 2
    for _, recipe in valid:
        print(f"{recipe.slug:<{col}} {recipe.title}")
    return 0


def _cmd_show(args) -> int:
    slug = args.slug
    entries = _load_all()
    for _, recipe in entries:
        if recipe is not None and recipe.slug == slug:
            _print_recipe(recipe)
            return 0
    print(f"[recipes] No recipe with slug '{slug}'.", file=sys.stderr)
    return 1


def _cmd_search(args) -> int:
    query = args.query.lower()
    entries = _load_all()
    matches: list[Recipe] = []
    for _, recipe in entries:
        if recipe is None:
            continue
        haystack = " ".join(
            [recipe.title, recipe.constraint] + recipe.symptoms
        ).lower()
        if query in haystack:
            matches.append(recipe)
    if not matches:
        print(f"[recipes] No recipes matched '{args.query}'.")
        return 1
    for recipe in matches:
        print(f"--- {recipe.slug}: {recipe.title}")
        for symptom in recipe.symptoms:
            print(f"    symptom: {symptom}")
    return 0


def _print_recipe(recipe: Recipe) -> None:
    print(f"slug:  {recipe.slug}")
    print(f"title: {recipe.title}")
    print()
    print("symptoms:")
    for s in recipe.symptoms:
        print(f"  - {s}")
    print()
    print("constraint:")
    for line in recipe.constraint.strip().splitlines():
        print(f"  {line}")
    print()
    print("pivots:")
    for pivot in recipe.pivots:
        rec = " [recommended]" if pivot.recommended else ""
        print(f"  [{pivot.cost}]{rec} {pivot.id}: {pivot.summary}")
    print()
    print("sources:")
    for src in recipe.sources:
        print(f"  - {src}")
    if recipe.related_recipes:
        print()
        print("related:")
        for rel in recipe.related_recipes:
            print(f"  - {rel}")


def run_recipes(args) -> int:
    sub = getattr(args, "recipes_command", None)
    if sub == "list":
        return _cmd_list(args)
    if sub == "show":
        return _cmd_show(args)
    if sub == "search":
        return _cmd_search(args)
    # should not reach here — argparse enforces required=True
    print("[recipes] No subcommand given. Use: list | show <slug> | search <query>", file=sys.stderr)
    return 1
