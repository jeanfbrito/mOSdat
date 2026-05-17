"""Pydantic schema for mosdat recipe YAML files.

Each recipe lives at shared/recipes/<slug>.yaml and describes a known platform
constraint plus ordered pivots (workaround strategies).
"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, field_validator


class Pivot(BaseModel):
    id: str
    summary: str
    cost: Literal["low", "medium", "high", "external"]
    recommended: bool = False


class Recipe(BaseModel):
    slug: str
    title: str
    symptoms: List[str]
    constraint: str
    pivots: List[Pivot]
    sources: List[str]
    related_recipes: List[str] = []

    @field_validator("slug")
    @classmethod
    def slug_kebab_safe(cls, v: str) -> str:
        import re
        if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", v):
            raise ValueError(
                f"slug must be kebab-case (lowercase letters, digits, hyphens): {v!r}"
            )
        return v
