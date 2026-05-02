"""Pydantic schema for functional test scenario YAML files (I3).

Validates top-level fields and every step type so typos like
``verfy_timeout`` surface as a clear ValidationError rather than
silently being ignored at runtime.

Usage::

    from automation.scenario import ScenarioModel
    model = ScenarioModel.model_validate(yaml_data)

``model.steps`` and ``model.cleanup`` contain validated step unions.
Unknown extra keys in any step are rejected, so ``verfy_timeout`` or
``loaclize`` will raise immediately with a message pointing at the
offending key.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Shared step base — fields common to all step types
# ---------------------------------------------------------------------------

class _StepBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verify: Optional[str] = None
    verify_not: Optional[str] = None
    verify_input: Optional[str] = None
    verify_click: Optional[str] = None
    verify_timeout: int = 10
    verify_consistent: bool = False
    retries: int = 3
    wait: int = 0
    focus: Optional[str] = None
    on_failure_agent: Optional[dict[str, Any]] = None
    checkpoint: Optional[str] = None
    # Diff-based click verification (compare before/after crops around click point)
    verify_click_diff: bool = False
    verify_click_diff_prompt: Optional[str] = None
    verify_click_diff_crop: int = 80
    # Canary-byte typing verification
    canary: bool = False
    canary_verify: Optional[str] = None
    canary_char: str = "q"

    @model_validator(mode="after")
    def _check_canary_config(self) -> "_StepBase":
        if self.canary and self.canary_verify is None:
            raise ValueError(
                "canary: true requires canary_verify to be set (config error)"
            )
        return self


# ---------------------------------------------------------------------------
# Concrete step types — each carries a discriminator literal
# ---------------------------------------------------------------------------

class LaunchStep(_StepBase):
    launch: str
    launch_window: Optional[str] = None
    launch_timeout: Optional[int] = None


class KeyStep(_StepBase):
    key: Optional[str] = None
    then_key: Optional[str] = None
    then_key_pre: Optional[str] = None
    key_pre: Optional[str] = None
    then_type: Optional[str] = None
    type: Optional[str] = None

    @model_validator(mode="after")
    def _require_at_least_one_key(self) -> "KeyStep":
        has_key = self.key or self.then_key or self.then_key_pre or self.key_pre
        has_type = self.then_type or self.type
        if not has_key and not has_type:
            raise ValueError(
                "KeyStep requires at least one of: key, then_key, then_key_pre, "
                "key_pre, then_type, type"
            )
        return self


class TypeStep(_StepBase):
    then_type: Optional[str] = None
    type: Optional[str] = None
    then_key: Optional[str] = None
    key: Optional[str] = None
    then_key_pre: Optional[str] = None
    key_pre: Optional[str] = None

    @model_validator(mode="after")
    def _require_type_text(self) -> "TypeStep":
        if not self.then_type and not self.type:
            raise ValueError("TypeStep requires then_type or type")
        return self


class ClickStep(_StepBase):
    localize: str
    then_type: Optional[str] = None
    type: Optional[str] = None
    then_key: Optional[str] = None
    key: Optional[str] = None
    then_key_pre: Optional[str] = None
    key_pre: Optional[str] = None
    precheck_click: bool = False
    localize_consistent: bool = False


# ClickStep and LocalizeStep are the same shape; alias for semantic clarity
LocalizeStep = ClickStep


class VerifyStep(_StepBase):
    verify: str  # override Optional — required on VerifyStep


class ShellStep(_StepBase):
    shell: str


class WaitStep(_StepBase):
    wait: int

    @model_validator(mode="after")
    def _require_nonzero_wait(self) -> "WaitStep":
        if self.wait <= 0:
            raise ValueError("WaitStep requires wait > 0")
        return self


class IfVisibleStep(_StepBase):
    if_visible: str
    then: list["AnyStep"] = Field(default_factory=list)


class PopupSweepStep(_StepBase):
    """Marker step that triggers a popup sweep (future use)."""
    popup_sweep: Literal[True]


class CheckpointStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checkpoint: str


# ---------------------------------------------------------------------------
# Step union — order matters: pydantic tries each in sequence
# Discriminator by presence of key fields handled via model_validator below.
# ---------------------------------------------------------------------------

AnyStep = Union[
    CheckpointStep,
    ShellStep,
    LaunchStep,
    IfVisibleStep,
    PopupSweepStep,
    ClickStep,     # covers LocalizeStep (has localize field)
    VerifyStep,
    WaitStep,
    KeyStep,
    TypeStep,
]

# Allow IfVisibleStep.then to reference AnyStep (forward ref)
IfVisibleStep.model_rebuild()


# ---------------------------------------------------------------------------
# Top-level scenario model
# ---------------------------------------------------------------------------

class ScenarioModel(BaseModel):
    model_config = ConfigDict(extra="allow")  # allow top-level extras (vars, checkpoints, etc.)

    name: str = ""
    description: Optional[str] = None
    app: Optional[str] = None
    vars: Optional[dict[str, str]] = None
    checkpoints: Optional[dict[str, Any]] = None
    cleanup: Optional[list[AnyStep]] = None
    steps: list[AnyStep]

    @model_validator(mode="before")
    @classmethod
    def _ensure_steps(cls, data: Any) -> Any:
        if isinstance(data, dict) and "steps" not in data:
            raise ValueError("scenario must contain a 'steps' list")
        return data
