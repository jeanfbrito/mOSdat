"""YAML scenario parsing for functional UI tests."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class FunctionalStep:
    localize: Optional[str] = None
    then_type: Optional[str] = None
    then_key: Optional[str] = None
    then_key_pre: Optional[str] = None
    verify: Optional[str] = None
    verify_not: Optional[str] = None
    verify_input: Optional[str] = None
    verify_click: Optional[str] = None
    verify_timeout: int = 10
    retries: int = 3
    launch: Optional[str] = None
    wait: int = 0
    focus: Optional[str] = None
    shell: Optional[str] = None
    if_visible: Optional[str] = None
    then_steps: Optional[list] = None
    verify_consistent: bool = False
    precheck_click: bool = False
    localize_consistent: bool = False
    launch_window: Optional[str] = None
    launch_timeout: Optional[int] = None
    on_failure_agent: Optional[dict] = None
    checkpoint: Optional[str] = None
    verify_click_diff: bool = False
    verify_click_diff_prompt: Optional[str] = None
    verify_click_diff_crop: int = 80
    canary: bool = False
    canary_verify: Optional[str] = None
    canary_char: str = "q"
    must_pass: bool = True


def resolve_vars(steps: list[FunctionalStep], vars: dict) -> list[FunctionalStep]:
    """Substitute {key} placeholders in step fields."""
    resolved = []
    for step in steps:
        resolved.append(FunctionalStep(
            localize=_sub(step.localize, vars) if step.localize else None,
            then_type=_sub(step.then_type, vars) if step.then_type else None,
            then_key=step.then_key,
            then_key_pre=step.then_key_pre,
            verify=_sub(step.verify, vars) if step.verify else None,
            verify_not=_sub(step.verify_not, vars) if step.verify_not else None,
            verify_input=_sub(step.verify_input, vars) if step.verify_input else None,
            verify_click=_sub(step.verify_click, vars) if step.verify_click else None,
            verify_timeout=step.verify_timeout,
            retries=step.retries,
            launch=_sub(step.launch, vars) if step.launch else None,
            wait=step.wait,
            focus=step.focus,
            shell=_sub(step.shell, vars) if step.shell else None,
            if_visible=_sub(step.if_visible, vars) if step.if_visible else None,
            then_steps=resolve_vars(step.then_steps, vars) if step.then_steps else None,
            verify_consistent=step.verify_consistent,
            precheck_click=step.precheck_click,
            localize_consistent=step.localize_consistent,
            launch_window=step.launch_window,
            launch_timeout=step.launch_timeout,
            on_failure_agent=_resolve_agent_vars(step.on_failure_agent, vars),
            checkpoint=step.checkpoint,
            verify_click_diff=step.verify_click_diff,
            verify_click_diff_prompt=step.verify_click_diff_prompt,
            verify_click_diff_crop=step.verify_click_diff_crop,
            canary=step.canary,
            canary_verify=_sub(step.canary_verify, vars) if step.canary_verify else None,
            canary_char=step.canary_char,
            must_pass=step.must_pass,
        ))
    return resolved


def _sub(text: Optional[str], vars: dict) -> Optional[str]:
    if not text:
        return text
    for key, value in vars.items():
        text = text.replace(f"{{{key}}}", value)
    return text


def _resolve_agent_vars(agent: Optional[dict], vars: dict) -> Optional[dict]:
    """Substitute {key} placeholders in on_failure_agent dict string values."""
    if not agent:
        return agent
    result = dict(agent)
    for field_name in ("goal", "success_check"):
        if isinstance(result.get(field_name), str):
            result[field_name] = _sub(result[field_name], vars)
    return result


def parse_on_failure_agent(raw: Optional[dict]) -> Optional[dict]:
    """Validate and normalize an on_failure_agent dict from YAML."""
    if not raw:
        return None
    if "goal" not in raw or not isinstance(raw["goal"], str):
        raise ValueError("on_failure_agent requires a 'goal' string field")
    return {
        "goal": raw["goal"],
        "budget_turns": int(raw.get("budget_turns", 10)),
        "success_check": raw.get("success_check"),
    }


def load_test_yaml(path, _unused=None) -> tuple[str, list[FunctionalStep], dict, dict]:
    """Load a YAML functional test file."""
    import sys as _sys
    import yaml

    path = Path(path)
    with open(path) as f:
        data = yaml.safe_load(f)

    try:
        from automation.scenario import ScenarioModel
        ScenarioModel.model_validate(data)
    except ImportError:
        pass
    except Exception as validation_error:
        _sys.stderr.write(
            f"[scenario] Validation error in {path}:\n{validation_error}\n"
        )
        _sys.exit(4)

    name = data.get("name", path.stem)
    vars = data.get("vars", {})
    checkpoints_config = data.get("checkpoints", {}) or {}
    steps = []
    for raw in data.get("steps", []):
        steps.append(parse_step(raw))
    return name, steps, vars, checkpoints_config


def parse_step(raw: dict) -> FunctionalStep:
    """Parse a single raw YAML step dict into a FunctionalStep."""
    if "checkpoint" in raw and len(raw) == 1:
        return FunctionalStep(checkpoint=str(raw["checkpoint"]))

    then_steps = None
    if "then" in raw:
        then_steps = [parse_step(step) for step in raw["then"]]
    return FunctionalStep(
        localize=raw.get("localize"),
        then_type=raw.get("then_type") or raw.get("type"),
        then_key=raw.get("then_key") or raw.get("key"),
        then_key_pre=raw.get("then_key_pre") or raw.get("key_pre"),
        verify=raw.get("verify"),
        verify_not=raw.get("verify_not"),
        verify_input=raw.get("verify_input"),
        verify_click=raw.get("verify_click"),
        verify_timeout=int(raw.get("verify_timeout", 10)),
        retries=int(raw.get("retries", 3)),
        launch=raw.get("launch"),
        wait=int(raw.get("wait", 0)),
        focus=raw.get("focus"),
        shell=raw.get("shell"),
        if_visible=raw.get("if_visible"),
        then_steps=then_steps,
        verify_consistent=bool(raw.get("verify_consistent", False)),
        precheck_click=bool(raw.get("precheck_click", False)),
        localize_consistent=bool(raw.get("localize_consistent", False)),
        launch_window=raw.get("launch_window"),
        launch_timeout=int(raw["launch_timeout"]) if "launch_timeout" in raw else None,
        on_failure_agent=parse_on_failure_agent(raw.get("on_failure_agent")),
        checkpoint=raw.get("checkpoint"),
        verify_click_diff=bool(raw.get("verify_click_diff", False)),
        verify_click_diff_prompt=raw.get("verify_click_diff_prompt"),
        verify_click_diff_crop=int(raw.get("verify_click_diff_crop", 80)),
        canary=bool(raw.get("canary", False)),
        canary_verify=raw.get("canary_verify"),
        canary_char=raw.get("canary_char", "q"),
    )
