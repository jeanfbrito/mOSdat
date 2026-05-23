"""R1: routine file discovery and loading.

Routines live at shared/routines/<name>.yaml relative to the project root.
The project root is resolved as the parent of the automation/ package directory.
"""

from __future__ import annotations

import sys
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from automation.routines import CURRENT_SCHEMA_VERSION
from automation.routines.schema import Routine


def _migrate_to_current(data: dict) -> dict:
    """Apply schema migrations so older routines validate against the current schema.

    Migration pattern:
        Each migration is a version-guarded block:

            version = data.get("schema_version", "v1")
            if version == "v1":
                # transform data to v2 shape
                data = _migrate_v1_to_v2(data)
                data["schema_version"] = "v2"

        Add blocks in ascending version order.  The function must be idempotent
        for routines already at CURRENT_SCHEMA_VERSION.

        Returning ``data`` unchanged (the v1 stub below) is correct as long as
        there is only one supported version and no shape changes are needed.
    """
    # v1 → current: no structural changes yet.  Stub for future migrations.
    version = data.get("schema_version", CURRENT_SCHEMA_VERSION)
    if version == CURRENT_SCHEMA_VERSION:
        return data
    # Future migration blocks go here, e.g.:
    #   if version == "v1":
    #       data = _migrate_v1_to_v2(data)
    #       version = data["schema_version"] = "v2"
    return data


def routines_dir() -> Path:
    """Return shared/routines/ (repo-root relative)."""
    # automation/ package: <repo>/automation/routines/loader.py
    # repo root is three levels up (routines → automation → repo)
    repo_root = Path(__file__).parent.parent.parent
    return repo_root / "shared" / "routines"


def resolve_routine_path(slug: str, platform: Optional[str] = None) -> Path:
    """Locate the YAML file for a routine slug, preferring a platform variant.

    Lookup order:
      1. ``shared/routines/<platform>/<slug>.yaml`` (when ``platform`` is given)
      2. ``shared/routines/<slug>.yaml`` (top-level / Linux fallback)

    Returns the path to whichever file exists first. If neither exists, returns
    the platform-specific path so the caller's FileNotFoundError reports the
    most useful location.
    """
    rdir = routines_dir()
    if platform:
        platform_path = rdir / platform / f"{slug}.yaml"
        if platform_path.exists():
            return platform_path
    top_path = rdir / f"{slug}.yaml"
    if top_path.exists():
        return top_path
    # Neither exists — surface the platform path if one was requested so the
    # error message reflects the most specific attempted location.
    if platform:
        return rdir / platform / f"{slug}.yaml"
    return top_path


@lru_cache(maxsize=256)
def load_routine(slug: str, platform: Optional[str] = None) -> Routine:
    """Load and validate a single routine by slug name.

    When ``platform`` is set, ``shared/routines/<platform>/<slug>.yaml`` is
    preferred over ``shared/routines/<slug>.yaml``. Linux / top-level routines
    remain at ``shared/routines/<slug>.yaml`` for backward compatibility.

    Raises FileNotFoundError if the file does not exist.
    Raises pydantic.ValidationError if the file is invalid.
    """
    path = resolve_routine_path(slug, platform)
    if not path.exists():
        raise FileNotFoundError(
            f"[routines] Routine {slug!r} not found at {path}"
        )
    data = yaml.safe_load(path.read_text())
    data = _migrate_to_current(data)
    return Routine.model_validate(data)


def list_routines() -> list[Routine]:
    """Scan shared/routines/, load all valid routines, warn + skip invalid.

    Returns routines sorted by name.
    """
    rdir = routines_dir()
    if not rdir.is_dir():
        return []

    results: list[Routine] = []
    for path in sorted(rdir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
            data = _migrate_to_current(data)
            routine = Routine.model_validate(data)
            results.append(routine)
        except Exception as exc:
            warnings.warn(
                f"[routines] Skipping invalid routine {path.name}: {exc}",
                stacklevel=2,
            )

    return sorted(results, key=lambda r: r.name)
