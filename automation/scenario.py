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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# I9: Named phase definition
# ---------------------------------------------------------------------------

class PhaseDef(BaseModel):
    """A named phase grouping consecutive steps.

    ``from_step`` is 1-indexed and inclusive.  A step belongs to phase P if
    ``P.from_step <= step_index < next_phase.from_step`` (or end of steps for
    the last phase).
    """
    model_config = ConfigDict(extra="forbid")

    id: str    # short identifier, e.g. "A"
    name: str  # human-readable description
    from_step: int  # 1-indexed, inclusive first step of this phase


# ---------------------------------------------------------------------------
# Bug-confirmation support models
# ---------------------------------------------------------------------------

class IssueRef(BaseModel):
    """Reference to a tracked bug for kind=bug-confirmation scenarios."""
    id: str  # "3308" or "RocketChat/Rocket.Chat.Electron#3308"
    url: str  # full GitHub URL; using str (not HttpUrl) to avoid pydantic url-validator headaches
    title: Optional[str] = None
    suspected_pr: Optional[str] = None
    reporter_version: Optional[str] = None  # e.g. "4.14.0"


class ExpectedEnv(BaseModel):
    """Reporter's claimed environment, used to render an env-delta table in the report."""
    ozone: Optional[Literal["wayland", "x11", "auto"]] = None
    display_server: Optional[Literal["wayland", "x11"]] = None
    install: Optional[Literal["flatpak", "snap", "appimage", "deb", "rpm"]] = None
    app_version: Optional[str] = None
    os: Optional[str] = None


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
    # Bug-confirmation mode: if False, step failures are non-fatal (default True → existing behaviour)
    must_pass: bool = True
    # I7: union verify — succeed on first prompt that returns yes
    accept_any: Optional[list[str]] = None
    # I10: human-readable step label (from YAML comment or explicit field)
    label: Optional[str] = None
    # M3 prep: optional pointer motion style metadata for click/hover-like steps
    motion: Optional[Literal["instant", "linear", "bezier"]] = None
    # M3 prep: optional dwell time before/after pointer action (ms)
    dwell_ms: Optional[int] = None

    @model_validator(mode="after")
    def _check_canary_config(self) -> "_StepBase":
        if self.canary and self.canary_verify is None:
            raise ValueError(
                "canary: true requires canary_verify to be set (config error)"
            )
        # I7: verify and accept_any are mutually exclusive
        if self.verify is not None and self.accept_any is not None:
            raise ValueError(
                "accept_any and verify are mutually exclusive — use one or the other"
            )
        # I7: accept_any must not be an empty list
        if self.accept_any is not None and len(self.accept_any) == 0:
            raise ValueError("accept_any must be a non-empty list of prompt strings")
        if self.dwell_ms is not None and self.dwell_ms < 0:
            raise ValueError("dwell_ms must be >= 0")
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
    click: Optional[Literal["left", "right"]] = None
    hover: bool = False
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


class AcceptAnyStep(_StepBase):
    """I7: union verify — iterate prompts, succeed on first VLM 'yes'."""
    accept_any: list[str]  # override Optional — required here, non-empty enforced below
    verify: Optional[str] = None  # must NOT be set alongside accept_any

    @model_validator(mode="after")
    def _check_accept_any(self) -> "AcceptAnyStep":
        if not self.accept_any:
            raise ValueError("accept_any must be a non-empty list of prompt strings")
        if self.verify is not None:
            raise ValueError(
                "accept_any and verify are mutually exclusive — use one or the other"
            )
        return self


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


class ImportStep(BaseModel):
    """I11: inline step import reference — dict form ``- import: <name>``."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    import_: str = Field(alias="import")


# ---------------------------------------------------------------------------
# Step union — order matters: pydantic tries each in sequence
# Discriminator by presence of key fields handled via model_validator below.
# ---------------------------------------------------------------------------

AnyStep = Union[
    CheckpointStep,
    ImportStep,      # I11: must precede ShellStep (import key not in _StepBase)
    ShellStep,
    LaunchStep,
    IfVisibleStep,
    PopupSweepStep,
    ClickStep,       # covers LocalizeStep (has localize field)
    AcceptAnyStep,   # I7: before VerifyStep — requires accept_any key
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
    kind: Literal["functional", "bug-confirmation"] = "functional"
    issue: Optional[IssueRef] = None
    bug_signal: Optional[str] = None              # yes/no VLM prompt; YES = bug visible
    precondition_check: Optional[str] = None      # yes/no VLM prompt; YES = scenario reached the path
    expected_env: Optional[ExpectedEnv] = None
    app: Optional[str] = None
    vars: Optional[dict[str, Any]] = None  # I8: accepts Any; rendered to str at load time
    checkpoints: Optional[dict[str, Any]] = None
    # I11: imported fragment manifest (lint/documentation; actual loading via !import steps)
    imports: list[str] = Field(default_factory=list)
    # I14: opt-in config.json snapshots — default off (perf-sensitive)
    report_config_snapshots: bool = False
    # I9: named phases — optional; scenarios without phases: work unchanged
    phases: Optional[list[PhaseDef]] = None
    cleanup: Optional[list[AnyStep]] = None
    steps: list[AnyStep]

    @model_validator(mode="before")
    @classmethod
    def _ensure_steps(cls, data: Any) -> Any:
        if isinstance(data, dict) and "steps" not in data:
            raise ValueError("scenario must contain a 'steps' list")
        return data

    @model_validator(mode="after")
    def _check_phases(self) -> "ScenarioModel":
        """I9: validate phases list — unique ids, strictly increasing from_step."""
        if not self.phases:
            return self
        seen_ids: set[str] = set()
        prev_from_step: Optional[int] = None
        for phase in self.phases:
            if phase.id in seen_ids:
                raise ValueError(
                    f"phases: duplicate id {phase.id!r} — each phase id must be unique"
                )
            seen_ids.add(phase.id)
            if prev_from_step is not None and phase.from_step <= prev_from_step:
                raise ValueError(
                    f"phases: from_step must be strictly increasing "
                    f"(phase {phase.id!r} has from_step={phase.from_step} "
                    f"but previous phase has from_step={prev_from_step})"
                )
            if phase.from_step < 1:
                raise ValueError(
                    f"phases: from_step must be >= 1 (phase {phase.id!r} has from_step={phase.from_step})"
                )
            prev_from_step = phase.from_step
        return self

    @model_validator(mode="after")
    def _check_bug_confirmation(self) -> "ScenarioModel":
        if self.kind == "bug-confirmation":
            if not self.bug_signal:
                raise ValueError("kind=bug-confirmation requires bug_signal (yes/no VLM prompt)")
            if not self.precondition_check:
                raise ValueError("kind=bug-confirmation requires precondition_check (yes/no VLM prompt)")
            if not self.issue:
                raise ValueError("kind=bug-confirmation requires issue.id and issue.url")
        return self
