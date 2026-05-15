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
    """Map per-iteration outcomes to a single aggregate per §6.3 of the plan."""
    confirmed = sum(1 for r in iters if r.verdict == "BUG_CONFIRMED")
    not_visible = sum(1 for r in iters if r.verdict == "BUG_NOT_VISIBLE")
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

    # Lazy import using importlib to work both when loaded normally and when
    # loaded via importlib.util.spec_from_file_location (no package context).
    import importlib as _importlib
    _sec = _importlib.import_module("automation.reporting.issue_report_sections")
    env_match_dict = _sec.env_match_dict
    render_artifacts = _sec.render_artifacts
    render_env_table = _sec.render_env_table
    render_header_block = _sec.render_header_block
    render_mode_footer = _sec.render_mode_footer
    render_mode_verdict_line = _sec.render_mode_verdict_line
    render_repro_steps = _sec.render_repro_steps
    render_reproducibility = _sec.render_reproducibility
    render_smoking_gun = _sec.render_smoking_gun
    render_verdict_table = _sec.render_verdict_table

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
    header_block = render_header_block(
        issue, scenario, mode, verdict,
        confirmed, not_visible, inconclusive, iterations, run_id, vm_name,
    )
    repro_steps = render_repro_steps(scenario)
    verdict_table = render_verdict_table(iterations)
    env_table, env_note = render_env_table(scenario, actual_env)
    smoking_gun = render_smoking_gun(iterations, scenario)
    reproducibility = render_reproducibility(verdict, confirmed, not_visible, inconclusive, total)
    artifacts = render_artifacts(iterations)
    mode_verdict_line = render_mode_verdict_line(mode, verdict, issue_id)
    mode_footer = render_mode_footer(mode, issue_id)

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

    reproducibility_line = (
        f"{confirmed}/{total} iterations confirmed"
        if confirmed
        else f"0/{total} iterations confirmed"
    )

    git_footer = ""
    if git_rev:
        git_footer = (
            f"\nReproduced with [mosdat](https://github.com/jeanfbrito/mOSdat) "
            f"at commit {git_rev}."
        )

    comment_env_table = env_table

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
    env_match = env_match_dict(scenario, actual_env)

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
