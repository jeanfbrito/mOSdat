"""Internal section renderers for issue_report.py.

Extracted from issue_report.py to keep each file ≤500 LOC.
Not part of the public API — import from issue_report.py instead.

Deliberately uses no relative imports so it works when issue_report.py is
loaded via importlib.util.spec_from_file_location (no package context).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

# All imports from automation.* are TYPE_CHECKING-only to avoid polluting
# sys.modules at import time.  The test suite stubs automation.runners.functional
# and automation.scenario; real imports here would shadow those stubs.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from automation.runners.functional import BugConfirmationResult
    from automation.scenario import ScenarioModel


# ---------------------------------------------------------------------------
# Type stubs (mirrors issue_report.py — kept in sync manually)
# ---------------------------------------------------------------------------
# We avoid importing from issue_report.py to prevent circular imports.
# These types are used only for type hints; no runtime enforcement needed.


class _ActualEnvProto:
    """Protocol-style stub; real type is issue_report.ActualEnv."""
    ozone: Optional[str]
    display_server: Optional[str]
    install: Optional[str]
    app_version: Optional[str]
    os: Optional[str]
    vm_name: Optional[str]
    process_cmdline: Optional[str]
    config_view: Optional[str]


# _EMOJI values are passed in from issue_report.py or reconstructed here.
_EMOJI: dict[str, str] = {
    "CONFIRMED": "✅",
    "NOT_REPRODUCED": "❌",
    "INTERMITTENT": "🟡",
    "INCONCLUSIVE": "⚠",
    "HARNESS_ERROR": "🚨",
}


# ---------------------------------------------------------------------------
# Environment comparison helpers
# ---------------------------------------------------------------------------

def env_match_symbol(expected: Optional[str], actual: Optional[str]) -> str:
    """Return ✓, ✗, ≈, or — for an env-field pair."""
    if not expected or not actual:
        return "—"
    if expected.strip().lower() == actual.strip().lower():
        return "✓"
    m_exp = re.match(r"^(.*?)(\d+)$", expected.strip())
    m_act = re.match(r"^(.*?)(\d+)$", actual.strip())
    if m_exp and m_act:
        base_exp = m_exp.group(1).strip().lower()
        base_act = m_act.group(1).strip().lower()
        if base_exp == base_act and abs(int(m_exp.group(2)) - int(m_act.group(2))) == 1:
            return "≈"
    parts_exp = expected.strip().split(".")
    parts_act = actual.strip().split(".")
    if len(parts_exp) == 3 and len(parts_act) == 3:
        if parts_exp[:2] == parts_act[:2] and parts_exp[2] != parts_act[2]:
            return "≈"
    return "✗"


def env_match_dict(scenario: ScenarioModel, actual: object) -> dict[str, bool]:
    """Return a mapping of env field → exact-match bool for summary.json."""
    expected = getattr(scenario, "expected_env", None)
    fields = ["ozone", "display_server", "install", "app_version", "os"]
    result: dict[str, bool] = {}
    for f in fields:
        exp_val = getattr(expected, f, None) if expected else None
        act_val = getattr(actual, f, None)
        result[f] = bool(exp_val and act_val and exp_val.strip().lower() == act_val.strip().lower())
    return result


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def render_header_block(
    issue: object,
    scenario: ScenarioModel,
    mode: str,
    verdict: str,
    confirmed: int,
    not_visible: int,
    inconclusive: int,
    iterations: list[BugConfirmationResult],
    run_id: str,
    vm_name: str,
) -> str:
    total = len(iterations)
    emoji = _EMOJI.get(verdict, "")
    verdict_label = verdict
    if verdict == "INTERMITTENT":
        verdict_label = f"INTERMITTENT ({confirmed}/{total})"

    suspected_pr = "—"
    scenario_issue = getattr(scenario, "issue", None)
    if scenario_issue and getattr(scenario_issue, "suspected_pr", None):
        pr = scenario_issue.suspected_pr
        suspected_pr = f"[#{pr}](https://github.com/RocketChat/Rocket.Chat.Electron/pull/{pr})"

    issue_url = getattr(issue, "url", "")
    lines = [
        f"**Mode**: {mode}",
        f"**Verdict**: {emoji} **{verdict_label}**",
        f"**Run ID**: {run_id}",
        f"**Run start**: {run_id_to_iso(run_id)} (`{total}` iterations on `{vm_name}`)",
        f"**Issue link**: {issue_url}",
        f"**Suspected PR**: {suspected_pr}",
    ]
    return "\n".join(lines)


def run_id_to_iso(run_id: str) -> str:
    """Convert "2026-05-02_191002" → "2026-05-02T19:10:02Z"."""
    try:
        dt = datetime.strptime(run_id, "%Y-%m-%d_%H%M%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return run_id


def render_repro_steps(scenario: ScenarioModel) -> str:
    """Pull description or fall back to step listing."""
    if scenario.description:
        return scenario.description.strip()
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


def render_verdict_table(iterations: list[BugConfirmationResult]) -> str:
    rows = ["| Iter | Precondition met | Bug visible | Outcome |",
            "|------|------------------|-------------|---------|"]
    for i, r in enumerate(iterations, 1):
        pre = "✓" if r.precondition_met else "✗"
        bug = "✓" if r.bug_visible else "✗"
        outcome = r.verdict.replace("BUG_", "")
        rows.append(f"| {i}    | {pre}                | {bug}           | {outcome} |")
    return "\n".join(rows)


def render_env_table(scenario: ScenarioModel, actual: object) -> tuple[str, str]:
    """Return (table_md, install_note_or_empty)."""
    expected = getattr(scenario, "expected_env", None)

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
        sym = env_match_symbol(
            None if exp_val == "—" else exp_val,
            None if act_val == "—" else act_val,
        )
        rows.append(f"| {label:<14} | {exp_val:<21} | {act_val:<12} | {sym}     |")

    table = "\n".join(rows)

    exp_install = exp("install")
    act_install = act("install")
    note = ""
    if (exp_install != "—" and act_install != "—"
            and exp_install.lower() != act_install.lower()):
        note = (f"\n\n**Note**: install method differs from issue "
                f"({exp_install} reported, {act_install} tested).")
    return table, note


def render_smoking_gun(iterations: list[BugConfirmationResult], scenario: ScenarioModel) -> str:
    """Embed the first confirmed iteration's bug-signal screenshot."""
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
    img_path = f"iter-{idx}/bug-signal.png"

    bug_signal_prompt = getattr(scenario, "bug_signal", None) or "_no bug_signal prompt set_"
    lines = [
        f"![Bug visible]({img_path})",
        "",
        "VLM verdict on this frame:",
        f"> {bug_signal_prompt} → **yes**",
    ]
    return "\n".join(lines)


def confidence_label(confirmed: int, not_visible: int, total: int) -> str:
    conclusive = confirmed + not_visible
    if conclusive >= 5:
        return "high"
    if conclusive == 4:
        return "medium"
    if conclusive == 3:
        return "low"
    return "insufficient data"


def render_reproducibility(
    verdict: str,
    confirmed: int,
    not_visible: int,
    inconclusive: int,
    total: int,
) -> str:
    conclusive = confirmed + not_visible
    conf_label = confidence_label(confirmed, not_visible, total)

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


def render_artifacts(iterations: list[BugConfirmationResult]) -> str:
    lines = []
    for i in range(1, len(iterations) + 1):
        lines.append(
            f"- `iter-{i}/events.jsonl`, `iter-{i}/screenshots/`, `iter-{i}/vm-state.json`"
        )
    return "\n".join(lines)


def render_mode_verdict_line(mode: str, verdict: str, issue_id: str) -> str:
    """Return the mode-specific verdict narrative."""
    if mode == "confirm":
        if verdict == "CONFIRMED":
            return "Bug confirmed."
        if verdict == "NOT_REPRODUCED":
            return "Bug NOT reproduced — does not appear on this VM/setup."
        if verdict == "INTERMITTENT":
            return "Bug intermittent."
        return ""
    else:
        if verdict == "NOT_REPRODUCED":
            return "Fix holds — bug NOT visible."
        if verdict == "CONFIRMED":
            return f"Regression: bug visible after fix (issue #{issue_id})."
        if verdict == "INTERMITTENT":
            return "Intermittent — fix may be partial."
        return ""


def render_mode_footer(mode: str, issue_id: str) -> str:
    if mode == "confirm":
        return "_If a fix lands, re-run with `--mode=verify-fix` to validate._"
    else:
        return f"_Regression test result for issue #{issue_id}._"
