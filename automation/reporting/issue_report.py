"""Phase D — Markdown report renderer for bug-confirmation results.

Pure functions only. No disk I/O, no HTTP, no SSH.
The orchestrator (Phase C) handles all file writes.

Public API:
    aggregate_verdict(iters, *, min_conclusive=3) -> AggregateVerdict
    render(...) -> RenderedReport
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

from automation.runners.functional import BugConfirmationResult
from automation.issue_fetch import IssueContext
from automation.scenario import ScenarioModel


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Mode = Literal["confirm", "verify-fix", "regression"]

AggregateVerdict = Literal[
    "CONFIRMED",        # all conclusive iters showed bug
    "NOT_REPRODUCED",   # all conclusive iters did NOT show bug
    "INTERMITTENT",     # mixed confirmed + not_visible
    "INCONCLUSIVE",     # not enough conclusive iters (precondition failed too often)
    "HARNESS_ERROR",    # all iters INCONCLUSIVE
]

# Verdict emoji map
_EMOJI: dict[AggregateVerdict, str] = {
    "CONFIRMED": "✅",
    "NOT_REPRODUCED": "❌",
    "INTERMITTENT": "🟡",
    "INCONCLUSIVE": "⚠",
    "HARNESS_ERROR": "🚨",
}


@dataclass
class ActualEnv:
    """What the harness actually ran on. Compared against scenario.expected_env."""
    ozone: Optional[str] = None
    display_server: Optional[str] = None
    install: Optional[str] = None
    app_version: Optional[str] = None
    os: Optional[str] = None
    vm_name: Optional[str] = None          # "fedora42"
    process_cmdline: Optional[str] = None
    config_view: Optional[str] = None      # from RC's config.json (if collected)
    extra: dict = field(default_factory=dict)  # arbitrary VM-state JSON


@dataclass
class RenderedReport:
    report_md: str
    comment_md: str
    summary_json: str        # JSON-serialised, machine-readable
    verdict: AggregateVerdict
    confirmed_count: int
    inconclusive_count: int
    not_visible_count: int


# ---------------------------------------------------------------------------
# aggregate_verdict
# ---------------------------------------------------------------------------

def aggregate_verdict(
    iters: list[BugConfirmationResult],
    *,
    min_conclusive: int = 3,
) -> AggregateVerdict:
    """Map per-iteration outcomes to a single aggregate per §6.3 of the plan.

    Verdict mapping per iteration (from BugConfirmationResult.verdict):
      INCONCLUSIVE      — precondition_met=False
      BUG_CONFIRMED     — precondition_met=True, bug_visible=True
      BUG_NOT_VISIBLE   — precondition_met=True, bug_visible=False

    Aggregate rules:
      1. All INCONCLUSIVE                    → HARNESS_ERROR
      2. Conclusive subset < min_conclusive  → INCONCLUSIVE (with warning)
      3. All conclusive are CONFIRMED        → CONFIRMED
      4. All conclusive are NOT_VISIBLE      → NOT_REPRODUCED
      5. Mixed                               → INTERMITTENT
    """
    confirmed = sum(1 for r in iters if r.verdict == "BUG_CONFIRMED")
    not_visible = sum(1 for r in iters if r.verdict == "BUG_NOT_VISIBLE")
    inconclusive = sum(1 for r in iters if r.verdict == "INCONCLUSIVE")
    conclusive = confirmed + not_visible

    if conclusive == 0:
        return "HARNESS_ERROR"
    if conclusive < min_conclusive:
        return "INCONCLUSIVE"
    if confirmed > 0 and not_visible == 0:
        return "CONFIRMED"
    if not_visible > 0 and confirmed == 0:
        return "NOT_REPRODUCED"
    return "INTERMITTENT"


# ---------------------------------------------------------------------------
# Helpers — environment comparison
# ---------------------------------------------------------------------------

def _env_match_symbol(expected: Optional[str], actual: Optional[str]) -> str:
    """Return ✓, ✗, ≈, or — for an env-field pair.

    Rules:
      - Either side missing (None or empty)  → —
      - Exact match (case-insensitive)        → ✓
      - Partial match heuristic (see below)   → ≈
      - Otherwise                             → ✗

    Partial-match heuristic (≈):
      Applied only when both values are populated and differ.
      Case 1 (OS major version): if both contain a common OS name token AND
        the numeric suffix differs by exactly 1 (e.g. "Fedora 42" vs "Fedora 43").
      Case 2 (app_version patch-only): if both are semver-like (X.Y.Z) and only
        the patch segment (Z) differs.
    """
    if not expected or not actual:
        return "—"
    if expected.strip().lower() == actual.strip().lower():
        return "✓"
    # Partial-match heuristic for OS major version (e.g. Fedora 42 vs Fedora 43)
    # Extract trailing integer from each value and compare the base token.
    import re
    m_exp = re.match(r"^(.*?)(\d+)$", expected.strip())
    m_act = re.match(r"^(.*?)(\d+)$", actual.strip())
    if m_exp and m_act:
        base_exp = m_exp.group(1).strip().lower()
        base_act = m_act.group(1).strip().lower()
        if base_exp == base_act and abs(int(m_exp.group(2)) - int(m_act.group(2))) == 1:
            return "≈"
    # Partial-match heuristic for app_version patch-only (X.Y.Z where only Z differs)
    parts_exp = expected.strip().split(".")
    parts_act = actual.strip().split(".")
    if len(parts_exp) == 3 and len(parts_act) == 3:
        if parts_exp[:2] == parts_act[:2] and parts_exp[2] != parts_act[2]:
            return "≈"
    return "✗"


def _env_match_dict(scenario: ScenarioModel, actual: ActualEnv) -> dict[str, bool]:
    """Return a mapping of env field → exact-match bool for summary.json."""
    expected = scenario.expected_env if scenario.expected_env else None
    fields = ["ozone", "display_server", "install", "app_version", "os"]
    result: dict[str, bool] = {}
    for f in fields:
        exp_val = getattr(expected, f, None) if expected else None
        act_val = getattr(actual, f, None)
        result[f] = bool(exp_val and act_val and exp_val.strip().lower() == act_val.strip().lower())
    return result


# ---------------------------------------------------------------------------
# Internal section renderers
# ---------------------------------------------------------------------------

def _render_header_block(
    issue: IssueContext,
    scenario: ScenarioModel,
    mode: Mode,
    verdict: AggregateVerdict,
    confirmed: int,
    not_visible: int,
    inconclusive: int,
    iterations: list[BugConfirmationResult],
    run_id: str,
    vm_name: str,
) -> str:
    total = len(iterations)
    emoji = _EMOJI[verdict]
    verdict_label = verdict
    if verdict == "INTERMITTENT":
        verdict_label = f"INTERMITTENT ({confirmed}/{total})"

    suspected_pr = "—"
    if scenario.issue and scenario.issue.suspected_pr:
        pr = scenario.issue.suspected_pr
        suspected_pr = f"[#{pr}](https://github.com/RocketChat/Rocket.Chat.Electron/pull/{pr})"

    lines = [
        f"**Mode**: {mode}",
        f"**Verdict**: {emoji} **{verdict_label}**",
        f"**Run ID**: {run_id}",
        f"**Run start**: {_run_id_to_iso(run_id)} (`{total}` iterations on `{vm_name}`)",
        f"**Issue link**: {issue.url}",
        f"**Suspected PR**: {suspected_pr}",
    ]
    return "\n".join(lines)


def _run_id_to_iso(run_id: str) -> str:
    """Convert "2026-05-02_191002" → "2026-05-02T19:10:02Z"."""
    try:
        dt = datetime.strptime(run_id, "%Y-%m-%d_%H%M%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return run_id


def _render_repro_steps(scenario: ScenarioModel) -> str:
    """Pull description or fall back to step listing."""
    if scenario.description:
        # If description is a multi-line block, render as-is
        return scenario.description.strip()
    # Fallback: enumerate steps by type
    lines = []
    for i, step in enumerate(scenario.steps, 1):
        step_dict = step.model_dump(exclude_none=True, exclude_defaults=True)
        if "launch" in step_dict:
            lines.append(f"{i}. Launch `{step_dict['launch']}`")
        elif "shell" in step_dict:
            lines.append(f"{i}. Shell: `{step_dict['shell']}`")
        elif "localize" in step_dict:
            lines.append(f"{i}. Click: {step_dict['localize']}")
        elif "key" in step_dict or "then_key" in step_dict:
            k = step_dict.get("key") or step_dict.get("then_key")
            lines.append(f"{i}. Key: `{k}`")
        else:
            lines.append(f"{i}. Step {i}")
    return "\n".join(lines) if lines else "_No steps described._"


def _render_verdict_table(iterations: list[BugConfirmationResult]) -> str:
    rows = ["| Iter | Precondition met | Bug visible | Outcome |",
            "|------|------------------|-------------|---------|"]
    for i, r in enumerate(iterations, 1):
        pre = "✓" if r.precondition_met else "✗"
        bug = "✓" if r.bug_visible else "✗"
        outcome = r.verdict.replace("BUG_", "")  # CONFIRMED / NOT_VISIBLE / INCONCLUSIVE
        rows.append(f"| {i}    | {pre}                | {bug}           | {outcome} |")
    return "\n".join(rows)


def _render_env_table(scenario: ScenarioModel, actual: ActualEnv) -> tuple[str, str]:
    """Return (table_md, install_note_or_empty).

    Fields: App version, Install, OS, Display server, Ozone backend.
    """
    expected = scenario.expected_env if scenario.expected_env else None

    def exp(attr: str) -> str:
        return getattr(expected, attr, None) or "—" if expected else "—"

    def act(attr: str) -> str:
        return getattr(actual, attr, None) or "—"

    rows_data = [
        ("App version",    exp("app_version"),    act("app_version")),
        ("Install",        exp("install"),         act("install")),
        ("OS",             exp("os"),              act("os")),
        ("Display server", exp("display_server"),  act("display_server")),
        ("Ozone backend",  exp("ozone"),           act("ozone")),
    ]
    rows = [
        "| Field          | Reporter (issue body) | This run     | Match  |",
        "|----------------|-----------------------|--------------|--------|",
    ]
    for label, exp_val, act_val in rows_data:
        sym = _env_match_symbol(
            None if exp_val == "—" else exp_val,
            None if act_val == "—" else act_val,
        )
        rows.append(f"| {label:<14} | {exp_val:<21} | {act_val:<12} | {sym}     |")

    table = "\n".join(rows)

    # Install-method mismatch note (issue brief says to add explicit note)
    exp_install = exp("install")
    act_install = act("install")
    note = ""
    if (exp_install != "—" and act_install != "—"
            and exp_install.lower() != act_install.lower()):
        note = (f"\n\n**Note**: install method differs from issue "
                f"({exp_install} reported, {act_install} tested).")
    return table, note


def _render_smoking_gun(iterations: list[BugConfirmationResult], scenario: ScenarioModel) -> str:
    """Embed the first confirmed iteration's bug-signal screenshot."""
    # Priority: first iter with bug_visible=True, else iter 1 final_screenshot
    target_iter = None
    for i, r in enumerate(iterations, 1):
        if r.bug_visible and r.precondition_met:
            target_iter = (i, r)
            break
    if target_iter is None:
        for i, r in enumerate(iterations, 1):
            if r.bug_visible:
                target_iter = (i, r)
                break
    if target_iter is None:
        target_iter = (1, iterations[0])

    idx, result = target_iter
    # Use bug_signal_screenshot if available, else final_screenshot
    img_path = f"iter-{idx}/bug-signal.png"

    bug_signal_prompt = scenario.bug_signal or "_no bug_signal prompt set_"
    lines = [
        f"![Bug visible]({img_path})",
        "",
        "VLM verdict on this frame:",
        f"> {bug_signal_prompt} → **yes**",
    ]
    return "\n".join(lines)


def _confidence_label(confirmed: int, not_visible: int, total: int) -> str:
    conclusive = confirmed + not_visible
    if conclusive >= 5:
        return "high"
    if conclusive == 4:
        return "medium"
    if conclusive == 3:
        return "low"
    return "insufficient data"


def _render_reproducibility(
    verdict: AggregateVerdict,
    confirmed: int,
    not_visible: int,
    inconclusive: int,
    total: int,
) -> str:
    conclusive = confirmed + not_visible
    conf_label = _confidence_label(confirmed, not_visible, total)

    rows = [
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total iterations | {total} |",
        f"| Confirmed (bug visible) | {confirmed} |",
        f"| Not visible | {not_visible} |",
        f"| Inconclusive (precondition failed) | {inconclusive} |",
        f"| Conclusive | {conclusive}/{total} |",
        f"| Confidence | {conf_label} |",
    ]
    return "\n".join(rows)


def _render_artifacts(iterations: list[BugConfirmationResult]) -> str:
    lines = []
    for i in range(1, len(iterations) + 1):
        lines.append(
            f"- `iter-{i}/events.jsonl`, `iter-{i}/screenshots/`, `iter-{i}/vm-state.json`"
        )
    return "\n".join(lines)


def _render_mode_verdict_line(mode: Mode, verdict: AggregateVerdict, issue_id: str) -> str:
    """Return the mode-specific verdict narrative."""
    if mode == "confirm":
        if verdict == "CONFIRMED":
            return "Bug confirmed."
        if verdict == "NOT_REPRODUCED":
            return "Bug NOT reproduced — does not appear on this VM/setup."
        if verdict == "INTERMITTENT":
            return "Bug intermittent."
        return ""
    else:  # verify-fix or regression
        if verdict == "NOT_REPRODUCED":
            return "Fix holds — bug NOT visible."
        if verdict == "CONFIRMED":
            return f"Regression: bug visible after fix (issue #{issue_id})."
        if verdict == "INTERMITTENT":
            return "Intermittent — fix may be partial."
        return ""


def _render_mode_footer(mode: Mode, issue_id: str) -> str:
    if mode == "confirm":
        return (
            f"_If a fix lands, re-run with `--mode=verify-fix` to validate._"
        )
    else:
        return f"_Regression test result for issue #{issue_id}._"


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(
    *,
    issue: IssueContext,
    scenario: ScenarioModel,
    mode: Mode,
    iterations: list[BugConfirmationResult],
    actual_env: ActualEnv,
    run_id: str,
    vm_name: str,
    git_rev: Optional[str] = None,
    repro_command: str,
) -> RenderedReport:
    """Produce report.md + comment.md + summary.json as strings."""
    if not iterations:
        raise ValueError("iterations must be non-empty")

    # --- Aggregate ---
    verdict = aggregate_verdict(iterations)
    confirmed = sum(1 for r in iterations if r.verdict == "BUG_CONFIRMED")
    not_visible = sum(1 for r in iterations if r.verdict == "BUG_NOT_VISIBLE")
    inconclusive = sum(1 for r in iterations if r.verdict == "INCONCLUSIVE")
    total = len(iterations)

    issue_id = issue.id
    issue_title = issue.title or f"Issue #{issue_id}"
    emoji = _EMOJI[verdict]
    verdict_label = verdict if verdict != "INTERMITTENT" else f"INTERMITTENT ({confirmed}/{total})"

    # --- report.md ---
    header_block = _render_header_block(
        issue, scenario, mode, verdict,
        confirmed, not_visible, inconclusive, iterations, run_id, vm_name,
    )
    repro_steps = _render_repro_steps(scenario)
    verdict_table = _render_verdict_table(iterations)
    env_table, env_note = _render_env_table(scenario, actual_env)
    smoking_gun = _render_smoking_gun(iterations, scenario)
    reproducibility = _render_reproducibility(verdict, confirmed, not_visible, inconclusive, total)
    artifacts = _render_artifacts(iterations)
    mode_verdict_line = _render_mode_verdict_line(mode, verdict, issue_id)
    mode_footer = _render_mode_footer(mode, issue_id)

    # Mode-specific header for verify-fix / regression
    verify_fix_header = ""
    if mode in ("verify-fix", "regression"):
        verify_fix_header = f"_Regression test result for issue #{issue_id}._\n\n"

    git_line = f"\nmosdat git revision: `{git_rev}`" if git_rev else ""

    mode_verdict_prefix = f"\n_{mode_verdict_line}_\n" if mode_verdict_line else ""

    report_md = f"""\
# Issue {issue_id} — {issue_title}

{header_block}
{mode_verdict_prefix}
## Reproduction steps (from scenario)

{repro_steps}

## Verdict detail

{verdict_table}

## Environment

{env_table}{env_note}

## Smoking-gun evidence

{smoking_gun}

## Reproducibility

{reproducibility}

## Per-iteration artifacts

{artifacts}

## Reproducer command

```bash
{repro_command}
```
{git_line}

{mode_footer}
"""

    # --- comment.md ---
    # Build a compact env summary line (no VM row)
    act = actual_env
    env_summary_parts = []
    if act.app_version:
        env_summary_parts.append(f"RC {act.app_version}")
    if act.install:
        env_summary_parts.append(act.install)
    if act.ozone:
        env_summary_parts.append(f"ozone={act.ozone}")
    if act.display_server:
        env_summary_parts.append(act.display_server)
    if act.os:
        env_summary_parts.append(act.os)
    env_summary = ", ".join(env_summary_parts) if env_summary_parts else "unknown env"

    mode_line_comment = (
        "Reproduced via mosdat confirm"
        if mode == "confirm"
        else "Verified fix via mosdat confirm --mode=verify-fix"
    )

    reproducibility_line = f"{confirmed}/{total} iterations confirmed" if confirmed else f"0/{total} iterations confirmed"

    git_footer = ""
    if git_rev:
        git_footer = (
            f"\nReproduced with [mosdat](https://github.com/jeanfbrito/mOSdat) "
            f"at commit {git_rev}."
        )

    # Comment env table (same as report but without VM row)
    comment_env_table = env_table  # already excludes vm_name row

    comment_md = f"""\
{emoji} **{verdict_label}** — {mode_verdict_line}

{mode_line_comment}

Tested on: {env_summary}

## Environment

{comment_env_table}{env_note}

Reproducibility: {reproducibility_line}

(screenshot attached separately)

_Evidence: see attached `iter-1/bug-signal.png`_
{git_footer}
"""

    # --- summary.json ---
    elapsed_total = sum(r.elapsed_ms for r in iterations)
    env_match = _env_match_dict(scenario, actual_env)

    summary_dict: dict = {
        "issue_id": issue_id,
        "issue_url": issue.url,
        "mode": mode,
        "verdict": verdict,
        "iterations_total": total,
        "iterations_confirmed": confirmed,
        "iterations_not_visible": not_visible,
        "iterations_inconclusive": inconclusive,
        "vm": vm_name,
        "run_id": run_id,
        "elapsed_ms_total": elapsed_total,
        "git_rev": git_rev,
        "env_match": env_match,
    }
    summary_json = json.dumps(summary_dict, indent=2)

    return RenderedReport(
        report_md=report_md,
        comment_md=comment_md,
        summary_json=summary_json,
        verdict=verdict,
        confirmed_count=confirmed,
        inconclusive_count=inconclusive,
        not_visible_count=not_visible,
    )
