"""Tests for cursor-teleport-misses-hover-handlers recipe (Task 1).

Validates:
- Recipe file exists and schema-validates
- All required fields present
- Pivots have correct cost values and recommended flags
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from automation.recipes.schema import Recipe

RECIPES_DIR = Path(__file__).parent.parent / "shared" / "recipes"
SLUG = "cursor-teleport-misses-hover-handlers"


def _load_recipe() -> Recipe:
    path = RECIPES_DIR / f"{SLUG}.yaml"
    data = yaml.safe_load(path.read_text())
    return Recipe.model_validate(data)


class TestCursorRecipeSchema:
    def test_file_exists(self):
        assert (RECIPES_DIR / f"{SLUG}.yaml").exists()

    def test_schema_validates(self):
        recipe = _load_recipe()
        assert recipe.slug == SLUG

    def test_title_present(self):
        recipe = _load_recipe()
        assert recipe.title.strip()

    def test_symptoms_non_empty(self):
        recipe = _load_recipe()
        assert len(recipe.symptoms) >= 1

    def test_constraint_non_empty(self):
        recipe = _load_recipe()
        assert recipe.constraint.strip()

    def test_pivots_non_empty(self):
        recipe = _load_recipe()
        assert len(recipe.pivots) >= 1

    def test_pivot_costs_valid(self):
        recipe = _load_recipe()
        valid_costs = {"low", "medium", "high", "external"}
        for pivot in recipe.pivots:
            assert pivot.cost in valid_costs

    def test_at_least_one_recommended_pivot(self):
        recipe = _load_recipe()
        assert any(p.recommended for p in recipe.pivots)

    def test_sources_non_empty(self):
        recipe = _load_recipe()
        assert len(recipe.sources) >= 1

    def test_related_recipes_includes_webview(self):
        recipe = _load_recipe()
        assert "webview-focus-stealing" in recipe.related_recipes

    def test_no_tags_field_in_schema(self):
        """Schema has no tags field; extra keys silently ignored by pydantic extra=ignore."""
        recipe = _load_recipe()
        assert not hasattr(recipe, "tags")
