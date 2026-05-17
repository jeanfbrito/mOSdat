"""I8: Jinja-style ``{{ var }}`` substitution for scenario YAML strings.

All string fields in every step are rendered against a flat ``vars`` dict
before the scenario is executed. Variable references use Jinja2 syntax
(``{{ key }}`` or ``{{ key }}`` with optional whitespace).

Rendering happens **before** ``ScenarioModel.model_validate`` so Pydantic
type checks run on the final rendered values.

Only flat ``str -> str`` (or ``str -> Any`` cast to str) is supported in v1.
Nested dicts/lists inside vars are not supported.

Missing variable (referenced in template but not in vars) raises
``MissingVarsError`` at load time — the error lists the missing key name and
the step index (0-based).
"""

from __future__ import annotations

from typing import Any, Optional


class MissingVarsError(ValueError):
    """Raised when a template references a variable not in vars dict."""


def _render(text: str, env, step_index: int) -> str:
    """Render a single string field. Raises MissingVarsError on undefined var."""
    from jinja2 import UndefinedError

    try:
        return env.from_string(text).render()
    except UndefinedError as exc:
        # Extract the variable name from the Jinja2 error message
        # e.g. "'workspace_title' is undefined"
        raise MissingVarsError(
            f"Step {step_index}: undefined variable in template: {exc}"
        ) from exc


def _maybe_render(text: Optional[str], env, step_index: int) -> Optional[str]:
    if text is None:
        return None
    return _render(text, env, step_index)


def _render_list(items: Optional[list], env, step_index: int) -> Optional[list]:
    if items is None:
        return None
    return [_render(s, env, step_index) if isinstance(s, str) else s for s in items]


def _render_agent(agent: Optional[dict], env, step_index: int) -> Optional[dict]:
    if not agent:
        return agent
    result = dict(agent)
    for field in ("goal", "success_check"):
        if isinstance(result.get(field), str):
            result[field] = _render(result[field], env, step_index)
    return result


def render_steps(steps: list[dict], vars: dict[str, Any]) -> list[dict]:
    """Render all string fields in a list of raw YAML step dicts.

    ``steps`` is the raw list from ``yaml.safe_load`` — dicts, not
    ``FunctionalStep`` objects. Rendering happens before parsing so that
    the parsed objects already contain expanded values.

    ``vars`` is the merged dict (yaml vars + CLI overrides, all cast to str).

    Returns a new list of dicts with substitutions applied.
    """
    try:
        from jinja2 import Environment, StrictUndefined
    except ImportError:
        # Fallback: regex-based substitution that raises on missing var
        return _render_steps_regex(steps, vars)

    # Cast all var values to str (numbers/bools → string)
    str_vars = {k: str(v) for k, v in vars.items()}

    env = Environment(undefined=StrictUndefined)
    env.globals.update(str_vars)

    rendered = []
    for i, step in enumerate(steps):
        rendered.append(_render_step_dict(step, env, i))
    return rendered


def _render_step_dict(step: dict, env, step_index: int) -> dict:
    """Render all string values in a single step dict (recursively for 'then')."""
    # Fields that are plain strings to render
    _STRING_FIELDS = (
        "shell", "verify", "verify_not", "verify_input", "verify_click",
        "localize", "then_type", "type", "then_key", "key",
        "then_key_pre", "key_pre", "focus", "if_visible",
        "launch", "launch_window",
        "verify_click_diff_prompt", "canary_verify", "canary_char",
    )
    result = dict(step)

    for field in _STRING_FIELDS:
        if isinstance(result.get(field), str):
            result[field] = _render(result[field], env, step_index)

    # accept_any: list of strings
    if result.get("accept_any") is not None:
        result["accept_any"] = _render_list(result["accept_any"], env, step_index)

    # on_failure_agent: dict with string fields
    if result.get("on_failure_agent") is not None:
        result["on_failure_agent"] = _render_agent(result["on_failure_agent"], env, step_index)

    # Nested steps (if_visible then:)
    if result.get("then") is not None:
        result["then"] = [_render_step_dict(s, env, step_index) for s in result["then"]]

    return result


# ---------------------------------------------------------------------------
# Regex fallback (when jinja2 is somehow unavailable)
# ---------------------------------------------------------------------------

import re as _re

_VAR_RE = _re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _sub_regex(text: str, vars: dict, step_index: int) -> str:
    def replace(m):
        key = m.group(1)
        if key not in vars:
            raise MissingVarsError(
                f"Step {step_index}: undefined variable '{{{{ {key} }}}}' in template"
            )
        return str(vars[key])
    return _VAR_RE.sub(replace, text)


def _render_steps_regex(steps: list[dict], vars: dict[str, Any]) -> list[dict]:
    str_vars = {k: str(v) for k, v in vars.items()}
    rendered = []
    for i, step in enumerate(steps):
        rendered.append(_render_step_dict_regex(step, str_vars, i))
    return rendered


def _render_step_dict_regex(step: dict, vars: dict, step_index: int) -> dict:
    _STRING_FIELDS = (
        "shell", "verify", "verify_not", "verify_input", "verify_click",
        "localize", "then_type", "type", "then_key", "key",
        "then_key_pre", "key_pre", "focus", "if_visible",
        "launch", "launch_window",
        "verify_click_diff_prompt", "canary_verify", "canary_char",
    )
    result = dict(step)

    for field in _STRING_FIELDS:
        if isinstance(result.get(field), str):
            result[field] = _sub_regex(result[field], vars, step_index)

    if result.get("accept_any") is not None:
        result["accept_any"] = [
            _sub_regex(s, vars, step_index) if isinstance(s, str) else s
            for s in result["accept_any"]
        ]

    if result.get("on_failure_agent") is not None:
        agent = dict(result["on_failure_agent"])
        for field in ("goal", "success_check"):
            if isinstance(agent.get(field), str):
                agent[field] = _sub_regex(agent[field], vars, step_index)
        result["on_failure_agent"] = agent

    if result.get("then") is not None:
        result["then"] = [_render_step_dict_regex(s, vars, step_index) for s in result["then"]]

    return result
