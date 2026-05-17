"""Pydantic schema for mosdat routine YAML files (R1).

Each routine lives at shared/routines/<slug>.yaml and describes a
parameterized, reusable sequence of scenario steps with optional
pre/postcondition verification and fallback branches.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, field_validator, model_validator

from automation.routines import CURRENT_SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS


_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Keys that mark a step dict as a verify step (precondition/postcondition)
_VERIFY_KEYS = {"verify", "verify_not", "verify_input", "verify_click", "accept_any"}


def _is_verify_step(step: Any) -> bool:
    """Return True if step dict contains at least one verify key."""
    if not isinstance(step, dict):
        return False
    return bool(_VERIFY_KEYS & step.keys())


class RoutineInput(BaseModel):
    """Declaration of one input parameter accepted by a routine."""

    name: str
    type: Literal["string", "int", "bool", "list"] = "string"
    required: bool = True
    default: Any = None

    @model_validator(mode="after")
    def _check_default_vs_required(self) -> "RoutineInput":
        if self.required and self.default is not None:
            raise ValueError(
                f"Input {self.name!r}: required=True but default is set — "
                "use required=False when a default is provided"
            )
        return self


class RoutineFallback(BaseModel):
    """Conditional fallback branch selected when a capability expression matches."""

    when: str  # jinja2 expression evaluated against {capability, inputs, vars}
    steps: list[dict]

    @field_validator("when")
    @classmethod
    def _when_valid_expression(cls, v: str) -> str:
        """Reject empty strings and syntactically invalid jinja2 expressions."""
        if not v or not v.strip():
            raise ValueError(
                "fallback 'when' must not be empty"
            )
        # The reserved value 'default' is always valid
        if v.strip() == "default":
            return v
        # Use compile_expression to detect parse errors at load time
        try:
            from jinja2 import Environment
            env = Environment()
            env.compile_expression(v)
        except Exception as exc:
            raise ValueError(
                f"fallback 'when' is not a valid jinja2 expression: {v!r} — {exc}"
            ) from exc
        return v

    @field_validator("steps")
    @classmethod
    def _steps_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("fallback steps must not be empty")
        return v


class Routine(BaseModel):
    """Full routine definition loaded from shared/routines/<name>.yaml."""

    schema_version: str = "v1"
    name: str
    description: str = ""
    inputs: dict[str, RoutineInput] = {}
    preconditions: list[dict] = []
    steps: list[dict] = []
    postconditions: list[dict] = []
    fallbacks: list[RoutineFallback] = []
    on_failure: list[dict] = []
    tags: list[str] = []

    @field_validator("schema_version")
    @classmethod
    def _schema_version_supported(cls, v: str) -> str:
        if v == CURRENT_SCHEMA_VERSION:
            return v
        if v in SUPPORTED_SCHEMA_VERSIONS:
            # Older supported version — migration already applied by loader.
            return v
        raise ValueError(
            f"schema_version {v!r} is newer than this mosdat ({CURRENT_SCHEMA_VERSION}); "
            f"upgrade mosdat or downgrade the routine. "
            f"Supported versions: {SUPPORTED_SCHEMA_VERSIONS}"
        )

    @field_validator("name")
    @classmethod
    def _name_is_kebab(cls, v: str) -> str:
        if not _KEBAB_RE.match(v):
            raise ValueError(
                f"routine name must be kebab-case (lowercase letters, digits, "
                f"hyphens only): {v!r}"
            )
        return v

    @field_validator("preconditions", "postconditions")
    @classmethod
    def _only_verify_steps(cls, steps: list, info) -> list:
        field = info.field_name
        for i, step in enumerate(steps):
            if not _is_verify_step(step):
                raise ValueError(
                    f"{field}[{i}] must be a verify step "
                    f"(must contain one of: {sorted(_VERIFY_KEYS)}); "
                    f"got keys: {sorted(step.keys()) if isinstance(step, dict) else type(step)}"
                )
        return steps
