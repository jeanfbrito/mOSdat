"""R1: routine expansion engine.

``expand_call`` takes a raw routine call dict from a scenario step list and
returns a flat list of expanded step dicts ready for ScenarioModel validation.

Call shapes accepted::

    # short form
    {"routine": "my-routine"}

    # long form with inputs
    {"routine": {"name": "my-routine", "with": {"url": "https://example.com"}}}

Expansion order::

    [*preconditions, *steps (or fallback steps), *postconditions]

``on_failure`` diagnostic steps are returned separately in the result's
``_on_failure`` key (a list); the caller decides when to run them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from automation.routines.loader import load_routine
from automation.routines.schema import Routine


# ---------------------------------------------------------------------------
# Manifest auto-load (R4)
# ---------------------------------------------------------------------------


class _SENTINEL_TYPE:  # noqa: N801
    """Sentinel distinguishing "not yet loaded" from None (no manifest found)."""


_NOT_LOADED = _SENTINEL_TYPE()
# Per-process cache: _NOT_LOADED = not attempted; None = attempted + not found; dict = loaded.
_manifest_cache: Any = _NOT_LOADED


def _capabilities_dir() -> Path:
    """Return shared/binary_capabilities/ relative to project root."""
    return Path(__file__).resolve().parent.parent.parent / "shared" / "binary_capabilities"


def _load_default_manifest() -> dict | None:
    """Load the most-recently-modified manifest from shared/binary_capabilities/.

    Returns None if the directory doesn't exist, is empty, or any IO error
    occurs. Result is cached per-process (module-level singleton).
    """
    global _manifest_cache  # noqa: PLW0603
    if not isinstance(_manifest_cache, _SENTINEL_TYPE):
        return _manifest_cache  # type: ignore[return-value]

    try:
        cap_dir = _capabilities_dir()
        if not cap_dir.is_dir():
            _manifest_cache = None
            return None
        files = sorted(cap_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            _manifest_cache = None
            return None
        import json as _json
        with open(files[0], encoding="utf-8") as f:
            _manifest_cache = _json.load(f)
        return _manifest_cache  # type: ignore[return-value]
    except Exception:
        _manifest_cache = None
        return None


def _load_manifest_by_sha(binary_sha: str) -> dict | None:
    """Load manifest for a specific binary SHA. Returns None if not found."""
    try:
        path = _capabilities_dir() / f"{binary_sha}.json"
        if not path.exists():
            return None
        import json as _json
        with open(path, encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def expand_call(
    call: dict,
    parent_vars: dict,
    capability_manifest: dict | None = None,
    binary_sha: str | None = None,
    _resolving: frozenset = frozenset(),
) -> list[dict]:
    """Expand a routine call dict into a flat list of scenario step dicts.

    :param call: step dict with "routine" key (short or long form)
    :param parent_vars: vars from the parent scenario (for jinja subst)
    :param capability_manifest: optional capability dict for fallback selection.
        If None and binary_sha is given, loads shared/binary_capabilities/<sha>.json.
        If both None, auto-loads the latest-mtime manifest (graceful no-op if missing).
    :param binary_sha: SHA of the binary whose manifest to load (overrides auto-load).
    :param _resolving: set of routine names currently being resolved (cycle detection)
    :returns: flat list of expanded step dicts
    :raises RuntimeError: on cycle detection or unknown routine
    :raises ValueError: on missing required input
    """
    # R4: resolve capability manifest if not explicitly supplied
    if capability_manifest is None:
        if binary_sha is not None:
            capability_manifest = _load_manifest_by_sha(binary_sha)
        else:
            capability_manifest = _load_default_manifest()

    name, call_inputs = _parse_call(call)

    if name in _resolving:
        chain = " -> ".join(sorted(_resolving)) + " -> " + name
        raise RuntimeError(
            f"[routines] Cycle detected in routine calls: {chain}"
        )

    routine = load_routine(name)
    resolved_inputs = _resolve_inputs(routine, call_inputs, parent_vars)

    # Build jinja render env: parent vars + resolved inputs.
    # Pass native types (lists/dicts) through unchanged so jinja filters like
    # `tojson` receive the original value, not its Python repr. Coercing via
    # str() double-encodes complex inputs and yields shell-broken single-quoted
    # payloads downstream.
    render_vars = {**parent_vars, **resolved_inputs}

    # Select main steps: check fallbacks first
    main_steps, fallback_used = _select_steps(
        routine, capability_manifest, resolved_inputs, parent_vars
    )

    # Render all step lists
    rendered_pre = _render_steps(routine.preconditions, render_vars)
    rendered_main = _render_steps(main_steps, render_vars)
    rendered_post = _render_steps(routine.postconditions, render_vars)

    new_resolving = _resolving | {name}

    # Recursively expand any nested routine calls
    expanded_pre = _expand_routine_steps(rendered_pre, parent_vars, capability_manifest, new_resolving)
    expanded_main = _expand_routine_steps(rendered_main, parent_vars, capability_manifest, new_resolving)
    expanded_post = _expand_routine_steps(rendered_post, parent_vars, capability_manifest, new_resolving)

    # Emit routine_call event as a synthetic shell no-op that records metadata
    # (stores event in step label for downstream consumers)
    event_label = (
        f"[routine:{name}] inputs={json.dumps(resolved_inputs, default=str)}"
        + (f" fallback={fallback_used!r}" if fallback_used else "")
    )
    event_step = {"shell": "true", "label": event_label, "_routine_event": True}

    # Build on_failure steps (rendered but not injected into main flow)
    rendered_on_failure = _render_steps(routine.on_failure, render_vars)
    expanded_on_failure = _expand_routine_steps(
        rendered_on_failure, parent_vars, capability_manifest, new_resolving
    )

    result = [event_step] + expanded_pre + expanded_main + expanded_post

    # Attach on_failure as metadata on first step for scenario_loader to extract
    if expanded_on_failure:
        result[0] = dict(result[0], _on_failure=expanded_on_failure)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_call(call: dict) -> tuple[str, dict]:
    """Parse the routine call dict, return (name, inputs_dict)."""
    value = call.get("routine")
    if isinstance(value, str):
        return value.strip(), {}
    if isinstance(value, dict):
        name = value.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"[routines] routine call with dict form requires 'name' key: {call!r}"
            )
        inputs = value.get("with") or {}
        if not isinstance(inputs, dict):
            raise ValueError(
                f"[routines] routine 'with' must be a dict of inputs: {call!r}"
            )
        return name.strip(), inputs
    raise ValueError(
        f"[routines] Invalid routine call shape: {call!r}. "
        "Expected 'routine: name' or 'routine: {{name: ..., with: {{...}}}}'"
    )


def _resolve_inputs(routine: Routine, call_inputs: dict, parent_vars: dict) -> dict:
    """Resolve routine inputs: call args > parent vars > routine defaults.

    Raises ValueError for missing required inputs.
    """
    resolved: dict = {}
    for input_name, inp_def in routine.inputs.items():
        if input_name in call_inputs:
            resolved[input_name] = call_inputs[input_name]
        elif input_name in parent_vars:
            resolved[input_name] = parent_vars[input_name]
        elif not inp_def.required:
            resolved[input_name] = inp_def.default
        else:
            raise ValueError(
                f"[routines] Routine {routine.name!r}: required input "
                f"{input_name!r} not provided (no call arg, no scenario var, no default)"
            )
    # Pass through any extra call inputs not declared in schema (they become vars)
    for k, v in call_inputs.items():
        if k not in resolved:
            resolved[k] = v
    return resolved


def _select_steps(
    routine: Routine,
    capability_manifest: dict | None,
    resolved_inputs: dict,
    parent_vars: dict | None = None,
) -> tuple[list[dict], Optional[str]]:
    """Select main steps: first matching fallback or default steps.

    Returns (steps, fallback_when_expr_or_None).
    The special value ``default`` fires when no other fallback matched.
    """
    if not routine.fallbacks:
        return routine.steps, None

    ctx = {
        "capability": capability_manifest if capability_manifest is not None else {},
        "inputs": resolved_inputs,
        "vars": parent_vars or {},
    }

    default_fallback = None
    for fb in routine.fallbacks:
        if fb.when == "default":
            default_fallback = fb
            continue
        # Only evaluate non-default fallbacks when a manifest is available
        if capability_manifest is None:
            continue
        if _eval_when(fb.when, ctx):
            return fb.steps, fb.when

    # No explicit fallback matched — fire default if present
    if default_fallback is not None:
        return default_fallback.steps, "default"

    return routine.steps, None


def _eval_when(expr: str, ctx: dict) -> bool:
    """Evaluate a jinja2 expression in a context dict, return bool.

    Uses ``Environment.compile_expression`` (expression semantics, not template).
    Returns False on any evaluation error.
    """
    try:
        from jinja2 import Environment, StrictUndefined
        env = Environment(undefined=StrictUndefined)
        compiled = env.compile_expression(expr, undefined_to_none=False)
        result = compiled(**ctx)
        return bool(result)
    except Exception:
        return False


def _render_steps(steps: list[dict], vars: dict) -> list[dict]:
    """Render jinja {{ var }} in all string fields of step dicts."""
    if not steps or not vars:
        return list(steps)
    from automation.runners.var_subst import render_steps
    return render_steps(steps, vars)


def _is_routine_call(step: Any) -> bool:
    """Return True if step is a routine: call."""
    return isinstance(step, dict) and "routine" in step


def _expand_routine_steps(
    steps: list[dict],
    parent_vars: dict,
    capability_manifest: dict | None,
    _resolving: frozenset,
) -> list[dict]:
    """Walk steps, recursively expanding any nested routine calls."""
    result = []
    for step in steps:
        if _is_routine_call(step):
            # Pass already-resolved manifest; skip auto-load in recursive calls.
            expanded = expand_call(
                step,
                parent_vars,
                capability_manifest=capability_manifest,
                _resolving=_resolving,
            )
            result.extend(expanded)
        else:
            result.append(step)
    return result
