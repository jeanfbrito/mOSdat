"""R3: fixture system for routine testing.

Fixtures describe a pre-baked VM state used to test a routine in isolation.
They live at shared/fixtures/<name>.yaml.

Schema:
  Fixture(BaseModel)
    name: str (kebab-case)
    description: str
    vm_state: dict  # e.g. {rc_killed, userdata_wiped, config, launched}
    setup_steps: list[dict]   # optional explicit steps run before the routine
    teardown_steps: list[dict]  # restore baseline after test

Loader: load_fixture(slug) -> Fixture
"""

from __future__ import annotations

import re
import warnings
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator, model_validator

_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def fixtures_dir() -> Path:
    """Return shared/fixtures/ (repo-root relative)."""
    repo_root = Path(__file__).parent.parent.parent
    return repo_root / "shared" / "fixtures"


class Fixture(BaseModel):
    """Full fixture definition loaded from shared/fixtures/<name>.yaml."""

    model_config = {"extra": "forbid"}

    name: str
    description: str = ""
    vm_state: dict = {}
    setup_steps: list[dict] = []
    teardown_steps: list[dict] = []

    @field_validator("name")
    @classmethod
    def _name_is_kebab(cls, v: str) -> str:
        if not _KEBAB_RE.match(v):
            raise ValueError(
                f"fixture name must be kebab-case (lowercase letters, digits, "
                f"hyphens only): {v!r}"
            )
        return v

    @field_validator("setup_steps", "teardown_steps")
    @classmethod
    def _steps_are_dicts(cls, steps: list) -> list:
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(
                    f"steps[{i}] must be a dict, got {type(step).__name__}"
                )
        return steps


@lru_cache(maxsize=256)
def load_fixture(slug: str) -> Fixture:
    """Load and validate a single fixture by slug name.

    Raises FileNotFoundError if the file does not exist.
    Raises pydantic.ValidationError if the file is invalid.
    """
    path = fixtures_dir() / f"{slug}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"[fixtures] Fixture {slug!r} not found at {path}"
        )
    data = yaml.safe_load(path.read_text())
    return Fixture.model_validate(data)


def list_fixtures() -> list[Fixture]:
    """Scan shared/fixtures/, load all valid fixtures, warn + skip invalid.

    Returns fixtures sorted by name.
    """
    fdir = fixtures_dir()
    if not fdir.is_dir():
        return []

    results: list[Fixture] = []
    for path in sorted(fdir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
            fixture = Fixture.model_validate(data)
            results.append(fixture)
        except Exception as exc:
            warnings.warn(
                f"[fixtures] Skipping invalid fixture {path.name}: {exc}",
                stacklevel=2,
            )

    return sorted(results, key=lambda f: f.name)
