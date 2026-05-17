"""YAML scenario parsing for functional UI tests."""

import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# I11: sentinel key used to mark import steps in raw dicts
_IMPORT_KEY = "import"

# I11: directory where fragment files live (relative to project root)
_IMPORTS_DIR_NAME = "shared/scenarios/imports"

# I10: regex for "box" comment style: # ── <label> ──+
# Label must contain at least one alphanumeric char (avoids matching pure-separator lines like # ══════ )
_BOX_COMMENT_RE = re.compile(r"^#\s*[─═\-]{2,}\s*(?P<label>.*\w.*?)\s*[─═\-]{2,}\s*$")


@dataclass
class FunctionalStep:
    localize: Optional[str] = None
    click: Optional[str] = None
    hover: bool = False
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
    # I7: union verify
    accept_any: Optional[list] = None
    # I10: human-readable step label (from YAML comment or explicit label: field)
    label: Optional[str] = None


def resolve_vars(steps: list[FunctionalStep], vars: dict) -> list[FunctionalStep]:
    """Substitute {key} placeholders in step fields."""
    resolved = []
    for step in steps:
        resolved.append(FunctionalStep(
            localize=_sub(step.localize, vars) if step.localize else None,
            click=step.click,
            hover=step.hover,
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
            accept_any=[_sub(p, vars) for p in step.accept_any] if step.accept_any else None,
            label=step.label,
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


def _extract_step_labels(path: Path) -> list[Optional[str]]:
    """I10: parse YAML with ruamel.yaml (round-trip) to extract comments above each step.

    Returns a list of labels indexed by step position (0-based) in the ``steps:`` list.
    Falls back to empty list (no labels) if ruamel is unavailable or parsing fails.

    ruamel.yaml stores comments in two places:
    - Comment before the first step: ``steps_seq.ca.comment[1]`` (a list of CommentToken)
    - Comment between step N-1 and step N: on the last key of item N-1 in
      ``item_N_minus_1.ca.items[last_key][2]`` (the "after-value-on-next-line" slot)
    """
    try:
        from ruamel.yaml import YAML as _RYAML
        from ruamel.yaml.comments import CommentedSeq as _CSeq
    except ImportError:
        warnings.warn(
            "[I10] ruamel.yaml not available — step labels from comments disabled",
            stacklevel=3,
        )
        return []

    ry = _RYAML()
    ry.preserve_quotes = True
    try:
        with open(path) as _f:
            doc = ry.load(_f)
    except Exception:
        return []

    steps_raw = doc.get("steps") if isinstance(doc, dict) else None
    if not isinstance(steps_raw, _CSeq):
        return []

    n = len(steps_raw)
    # Build a raw-comment-token list per step index
    # Index 0: from steps_raw.ca.comment[1]
    # Index i (i>0): from steps_raw[i-1].ca.items[<last_key>][2]
    raw_tokens: list[Optional[list]] = [None] * n

    # First step: comment lives on the sequence itself
    seq_ca = getattr(steps_raw, "ca", None)
    if seq_ca is not None and seq_ca.comment and len(seq_ca.comment) > 1:
        raw_tokens[0] = seq_ca.comment[1]  # list of CommentToken or None

    # Steps 1..n-1: comment lives on the previous item's last key slot [2]
    for i in range(1, n):
        prev_item = steps_raw[i - 1]
        prev_ca = getattr(prev_item, "ca", None)
        if prev_ca is None:
            continue
        items_dict = getattr(prev_ca, "items", {}) or {}
        if not items_dict:
            continue
        # The comment before step i is stored on the last key of prev_item at slot [2]
        # (ruamel uses slots: [0]=before-key, [1]=after-key-inline, [2]=after-value-newline, [3]=...)
        last_key = list(items_dict.keys())[-1]
        entry = items_dict[last_key]
        if entry and len(entry) > 2 and entry[2] is not None:
            raw_tokens[i] = entry[2] if isinstance(entry[2], list) else [entry[2]]

    labels: list[Optional[str]] = []
    for tok_entry in raw_tokens:
        comment_lines = _tokens_to_comment_lines(tok_entry)
        labels.append(_parse_comment_label(comment_lines if comment_lines else None))
    return labels


def _tokens_to_comment_lines(tok_entry) -> list[str]:
    """Extract non-empty comment lines from a ruamel CommentToken or list thereof."""
    if tok_entry is None:
        return []
    if not isinstance(tok_entry, list):
        tok_entry = [tok_entry]
    lines = []
    for tok in tok_entry:
        if tok is None:
            continue
        val = tok.value if hasattr(tok, "value") else str(tok)
        for line in val.splitlines():
            line = line.strip()
            if line.startswith("#"):
                lines.append(line)
    return lines


def _parse_comment_label(comment_lines: Optional[list[str]]) -> Optional[str]:
    """Derive a label string from comment lines immediately above a step.

    Priority:
    1. First line matching box pattern (# ── text ──).
    2. All plain comment lines joined (space), capped at 80 chars.
    Returns None if no comment lines.
    """
    if not comment_lines:
        return None

    # Try box pattern first
    for line in comment_lines:
        m = _BOX_COMMENT_RE.match(line)
        if m:
            return m.group("label")

    # Fallback: strip # prefix from all lines, join, cap at 80
    parts = []
    for line in comment_lines:
        stripped = line.lstrip("#").strip()
        if stripped:
            parts.append(stripped)
    if not parts:
        return None
    joined = " ".join(parts)
    return joined[:80] if len(joined) > 80 else joined


# ---------------------------------------------------------------------------
# I11: Fragment import resolution
# ---------------------------------------------------------------------------

def _imports_dir(scenario_path: Path) -> Path:
    """Locate the shared/scenarios/imports/ directory relative to project root.

    Walks up from the scenario file until it finds the directory. Falls back
    to a path relative to this module's location.
    """
    # Walk up from the scenario file looking for shared/scenarios/imports
    candidate = scenario_path.parent
    for _ in range(8):
        imports_dir = candidate / _IMPORTS_DIR_NAME
        if imports_dir.is_dir():
            return imports_dir
        candidate = candidate.parent
    # Fallback: relative to this module
    return Path(__file__).parent.parent.parent / _IMPORTS_DIR_NAME


def _load_fragment_steps(
    name: str,
    imports_dir: Path,
    scenario_path: Path,
    _resolving: frozenset,
) -> list[dict]:
    """Load and expand a single fragment, returning its raw step dicts.

    :param name: fragment name (e.g. ``cleanup-rocketchat``)
    :param imports_dir: resolved path to ``shared/scenarios/imports/``
    :param scenario_path: parent scenario path (for error messages)
    :param _resolving: set of fragment names currently being resolved (cycle detection)
    :raises FileNotFoundError: if the fragment file does not exist
    :raises RuntimeError: if a cycle is detected
    """
    if name in _resolving:
        chain = " -> ".join(sorted(_resolving)) + " -> " + name
        raise RuntimeError(
            f"[I11] Cycle detected in fragment imports: {chain} "
            f"(while loading scenario {scenario_path})"
        )

    frag_path = imports_dir / f"{name}.yaml"
    if not frag_path.exists():
        raise FileNotFoundError(
            f"[I11] Fragment '{name}' not found at {frag_path} "
            f"(referenced from {scenario_path})"
        )

    import yaml as _yaml
    with open(frag_path) as _f:
        frag_doc = _yaml.safe_load(_f)

    # Fragment file shape: top-level dict with optional metadata + a "steps:" list
    if isinstance(frag_doc, list):
        # Bare list — treat as steps directly
        raw_steps = frag_doc
    elif isinstance(frag_doc, dict):
        raw_steps = frag_doc.get("steps", []) or []
    else:
        raw_steps = []

    # Extract comment-derived labels from the fragment file (I10 compat)
    frag_comment_labels = _extract_step_labels(frag_path)
    prefix = f"[import:{name}] "

    # Apply label prefix to each fragment step
    prefixed_steps = []
    for idx, step_dict in enumerate(raw_steps):
        step_dict = dict(step_dict)
        # Build label: comment-derived (if any) with prefix; explicit label: field also prefixed
        existing_label = step_dict.get("label")
        if existing_label:
            step_dict["label"] = prefix + existing_label
        elif frag_comment_labels and idx < len(frag_comment_labels) and frag_comment_labels[idx]:
            step_dict["label"] = prefix + frag_comment_labels[idx]
        prefixed_steps.append(step_dict)

    # Recursively expand any !import steps inside the fragment
    new_resolving = _resolving | {name}
    return _expand_imports(prefixed_steps, imports_dir, scenario_path, new_resolving)


def _is_import_step(step_dict) -> Optional[str]:
    """Return the fragment name if step_dict is an import reference, else None.

    Accepts both shapes:
    - ``{"import": "name"}`` (dict form, no other keys allowed to avoid ambiguity)
    - Steps with ``import`` key that may come from YAML tag (registered constructor
      returns same dict shape).
    """
    if not isinstance(step_dict, dict):
        return None
    # Recognise an import step by the presence of an "import" key. Any
    # other keys are treated as metadata (e.g. a "label" applied by the
    # I10 comment-extractor) and dropped — fragments supply their own
    # steps, not metadata-overrides for the splice site.
    val = step_dict.get(_IMPORT_KEY)
    if not isinstance(val, str) or not val.strip():
        return None
    # Allow only "import" plus optional metadata keys (label). Reject if
    # any "real" step keys are mixed in (shell, verify, click, etc.) —
    # that would be an ambiguous mixed step.
    _METADATA_KEYS = {_IMPORT_KEY, "label"}
    if not set(step_dict.keys()).issubset(_METADATA_KEYS):
        return None
    return val.strip()


def _expand_imports(
    steps: list,
    imports_dir: Path,
    scenario_path: Path,
    _resolving: frozenset = frozenset(),
) -> list[dict]:
    """Walk a list of raw step dicts and splice in fragment steps for any !import."""
    result = []
    for step in steps:
        frag_name = _is_import_step(step)
        if frag_name is not None:
            expanded = _load_fragment_steps(frag_name, imports_dir, scenario_path, _resolving)
            result.extend(expanded)
        else:
            result.append(step)
    return result


def _register_import_constructor(loader_class):
    """Register ``!import`` YAML tag on the given yaml.Loader class.

    The constructor maps ``!import name`` → ``{"import": "name"}``.
    """
    def _import_constructor(loader, node):
        value = loader.construct_scalar(node)
        return {_IMPORT_KEY: value.strip()}

    loader_class.add_constructor("!import", _import_constructor)


def _warn_manifest_mismatch(steps: list, declared_imports: list, scenario_path: Path):
    """I11: lint — warn if !import X is used but X not listed in top-level imports:."""
    if not declared_imports:
        return
    declared_set = set(declared_imports)
    for step in steps:
        name = _is_import_step(step)
        if name is not None and name not in declared_set:
            warnings.warn(
                f"[I11] Scenario {scenario_path.name}: step uses '!import {name}' "
                f"but '{name}' is not listed in the top-level imports: manifest. "
                f"Add it for documentation purposes.",
                stacklevel=5,
            )


def load_test_yaml(path, _unused=None, cli_vars: dict | None = None) -> tuple[str, list[FunctionalStep], dict, dict]:
    """Load a YAML functional test file.

    I8: ``cli_vars`` (from ``--var KEY=VALUE``) is merged over the scenario's
    ``vars:`` block before substitution. CLI values win on conflict.
    ``{{ key }}`` placeholders are rendered in all string step fields
    **before** ``ScenarioModel.model_validate`` so type checks run on
    rendered values.

    I11: ``!import <name>`` and ``- import: <name>`` step references are
    expanded inline before var substitution, so vars from the parent scenario
    flow into fragment steps.
    """
    import sys as _sys
    import yaml

    path = Path(path)
    # I10: extract comment-derived labels BEFORE safe_load (ruamel preserves comments)
    comment_labels = _extract_step_labels(path)

    # I11: register !import tag constructor on SafeLoader before loading
    _register_import_constructor(yaml.SafeLoader)

    with open(path) as f:
        data = yaml.safe_load(f)

    name = data.get("name", path.stem)

    # I11: expand !import references only when steps key exists
    if "steps" in data and data["steps"]:
        raw_steps_pre_expand = list(data["steps"])
        # I10 + I11: apply comment labels to parent steps BEFORE expansion (indices are pre-expansion)
        for idx, raw in enumerate(raw_steps_pre_expand):
            if comment_labels and idx < len(comment_labels) and comment_labels[idx] is not None:
                if isinstance(raw, dict) and ("label" not in raw or raw["label"] is None):
                    raw_steps_pre_expand[idx] = dict(raw)
                    raw_steps_pre_expand[idx]["label"] = comment_labels[idx]

        # I11: lint — warn if !import X used without listing in imports: manifest
        declared_imports = data.get("imports", []) or []
        _warn_manifest_mismatch(raw_steps_pre_expand, declared_imports, path)

        # I11: expand !import references (splice fragment steps into parent list)
        imports_dir = _imports_dir(path)
        expanded_steps = _expand_imports(raw_steps_pre_expand, imports_dir, path)
        data = dict(data)
        data["steps"] = expanded_steps

    # I8: merge yaml vars + CLI overrides (CLI wins)
    yaml_vars: dict = data.get("vars", {}) or {}
    merged_vars: dict = {**yaml_vars, **(cli_vars or {})}

    # I8: render {{ key }} substitutions on raw step dicts BEFORE schema validation
    if merged_vars:
        from automation.runners.var_subst import render_steps, MissingVarsError
        try:
            if "steps" in data and data["steps"]:
                data = dict(data)
                data["steps"] = render_steps(data["steps"], merged_vars)
            if "cleanup" in data and data["cleanup"]:
                data = dict(data)
                data["cleanup"] = render_steps(data["cleanup"], merged_vars)
        except MissingVarsError as e:
            _sys.stderr.write(f"[scenario] Variable substitution error in {path}:\n{e}\n")
            _sys.exit(4)

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

    checkpoints_config = data.get("checkpoints", {}) or {}
    steps = []
    for idx, raw in enumerate(data.get("steps", [])):
        steps.append(parse_step(raw))
    return name, steps, merged_vars, checkpoints_config


def parse_step(raw: dict) -> FunctionalStep:
    """Parse a single raw YAML step dict into a FunctionalStep."""
    if "checkpoint" in raw and len(raw) == 1:
        return FunctionalStep(checkpoint=str(raw["checkpoint"]))

    then_steps = None
    if "then" in raw:
        then_steps = [parse_step(step) for step in raw["then"]]
    return FunctionalStep(
        localize=raw.get("localize"),
        click=raw.get("click"),
        hover=bool(raw.get("hover", False)),
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
        accept_any=raw.get("accept_any"),
        label=raw.get("label"),
    )
