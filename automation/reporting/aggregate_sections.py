"""Markdown section renderers and flakiness leaderboard for aggregate.py.

Extracted from aggregate.py to keep each file ≤500 LOC.
Internal use only — public API lives in aggregate.py.
"""

from __future__ import annotations

import json
import re
import warnings
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .aggregate import RunData


# ---------------------------------------------------------------------------
# Markdown table helper (duplicated here to avoid circular import)
# ---------------------------------------------------------------------------

def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a simple ASCII markdown table."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        parts = []
        for i, cell in enumerate(cells):
            w = widths[i] if i < len(widths) else len(cell)
            parts.append(cell.ljust(w))
        return "| " + " | ".join(parts) + " |"

    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    lines = [fmt_row(headers), sep]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


def _status_char(status: str) -> str:
    mapping = {
        "passed": "PASS",
        "failed": "FAIL",
        "skipped": "SKIP",
        "pending": "-",
        "running": "RUN",
    }
    return mapping.get(status, status.upper()[:4] if status else "?")


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

def section_overview(runs: list) -> str:
    lines = ["## Overview\n"]
    rows = []
    for r in runs:
        rate = f"{r.pass_rate:.0f}%" if r.total_count else "N/A"
        rows.append([
            r.run_id,
            r.version,
            str(r.pass_count),
            str(r.fail_count),
            str(r.total_count),
            rate,
            r.source,
            r.last_updated[:19] if r.last_updated else "",
        ])
    table = _md_table(
        ["Run", "Version", "Pass", "Fail", "Total", "Pass%", "Source", "Last Updated"],
        rows,
    )
    lines.append(table)
    lines.append("")
    return "\n".join(lines)


def section_matrix(runs: list) -> str:
    """OS x Package x GPU x Scenario matrix."""
    all_keys: list[str] = []
    seen: set[str] = set()
    for r in runs:
        for k in r.cells:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)
    all_keys.sort()

    if not all_keys:
        return "## Matrix\n\n_No test cells found._\n"

    lines = ["## Matrix\n"]
    lines.append("Rows = runs. Columns = OS-Package-GPU combos. Values = PASS/FAIL/SKIP/-.\n")

    col_headers = ["Run"] + [k for k in all_keys]
    rows = []
    for r in runs:
        row = [r.run_id]
        for k in all_keys:
            cell = r.cells.get(k)
            row.append(_status_char(cell.status) if cell else "-")
        rows.append(row)

    table = _md_table(col_headers, rows)
    lines.append(table)
    lines.append("")
    return "\n".join(lines)


def section_flaky(runs: list) -> str:
    """Cells that passed in some runs and failed in others."""
    all_keys: set[str] = set()
    for r in runs:
        all_keys.update(r.cells.keys())

    flaky: list[tuple[str, list[str], list[str]]] = []
    for key in sorted(all_keys):
        passed_in = []
        failed_in = []
        for r in runs:
            cell = r.cells.get(key)
            if cell is None:
                continue
            if cell.status == "passed":
                passed_in.append(r.run_id)
            elif cell.status == "failed":
                failed_in.append(r.run_id)
        if passed_in and failed_in:
            flaky.append((key, passed_in, failed_in))

    if not flaky:
        return "## Flaky Tests\n\n_No flaky tests detected._\n"

    lines = ["## Flaky Tests\n"]
    lines.append("Tests that passed in some runs and failed in others.\n")
    rows = []
    for key, passed_in, failed_in in flaky:
        rows.append([key, str(len(passed_in)), str(len(failed_in)),
                     ", ".join(passed_in[:3]) + ("..." if len(passed_in) > 3 else ""),
                     ", ".join(failed_in[:3]) + ("..." if len(failed_in) > 3 else "")])
    table = _md_table(
        ["Cell", "Pass Count", "Fail Count", "Passed In", "Failed In"],
        rows,
    )
    lines.append(table)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Flakiness leaderboard (B5)
# ---------------------------------------------------------------------------

def flake_leaderboard(results_root: Path) -> str:
    """Aggregate retry rates across ALL past functional runs.

    Walks results_root/functional for all dated run dirs, groups events by step label,
    counts retries and failures, aggregates across runs, and returns a markdown table
    ranked by retry_rate.

    Args:
        results_root: Path to results directory (contains "functional" subdir)

    Returns:
        Markdown string with flakiness leaderboard table. Empty report if no runs found.
    """
    functional_dir = results_root / "functional"

    if not functional_dir.exists():
        return f"# Flakiness Leaderboard\n\nNo functional runs found under {results_root}\n"

    run_dirs = []
    for item in sorted(functional_dir.iterdir()):
        if item.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", item.name):
            run_dirs.append(item)

    if not run_dirs:
        return f"# Flakiness Leaderboard\n\nNo functional runs found under {results_root}\n"

    step_stats: dict[str, dict] = {}

    for run_dir in run_dirs:
        for vm_dir in run_dir.iterdir():
            if not vm_dir.is_dir():
                continue
            events_file = vm_dir / "events.jsonl"
            if not events_file.exists():
                continue

            try:
                with events_file.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            warnings.warn(f"[flake_leaderboard] Malformed JSON in {events_file}: {line[:100]}")
                            continue

                        if event.get("event") == "step_start":
                            step_num = event.get("step_num")
                            label = event.get("label", "")
                            label_key = label[:60] if label else f"step_{step_num}"

                            if label_key not in step_stats:
                                step_stats[label_key] = {
                                    "runs": set(),
                                    "retries": 0,
                                    "failures": 0,
                                    "attempts": [],
                                }
                            step_stats[label_key]["runs"].add(run_dir.name)

                        elif event.get("event") == "retry":
                            step_num = event.get("step_num")
                            label = event.get("label", "")
                            label_key = label[:60] if label else f"step_{step_num}"
                            if label_key in step_stats:
                                step_stats[label_key]["retries"] += 1

                        elif event.get("event") == "step_end":
                            step_num = event.get("step_num")
                            label = event.get("label", "")
                            label_key = label[:60] if label else f"step_{step_num}"
                            status = event.get("status", "")
                            attempts = event.get("attempts", 1)

                            if label_key in step_stats:
                                if status == "failed":
                                    step_stats[label_key]["failures"] += 1
                                if attempts:
                                    step_stats[label_key]["attempts"].append(attempts)

            except OSError as exc:
                warnings.warn(f"[flake_leaderboard] Could not read {events_file}: {exc}")
                continue

    if not step_stats:
        return f"# Flakiness Leaderboard\n\nNo events found in {results_root}\n"

    leaderboard: list[tuple[str, int, int, int, float, float, int]] = []
    for label, stats in step_stats.items():
        runs_total = len(stats["runs"])
        if runs_total < 2:
            continue

        retries_total = stats["retries"]
        failures_total = stats["failures"]
        attempts_list = stats["attempts"]

        retry_rate = retries_total / runs_total if runs_total > 0 else 0.0
        failure_rate = failures_total / runs_total if runs_total > 0 else 0.0
        median_attempts = int(median(attempts_list)) if attempts_list else 1

        leaderboard.append((
            label,
            runs_total,
            retries_total,
            failures_total,
            retry_rate,
            failure_rate,
            median_attempts,
        ))

    leaderboard.sort(key=lambda x: x[4], reverse=True)
    leaderboard = leaderboard[:30]

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    rows = []
    for label, runs, retries, failures, retry_rate, failure_rate, med_attempts in leaderboard:
        rows.append([
            label,
            str(runs),
            f"{retry_rate:.2f}",
            f"{failure_rate:.2f}",
            str(med_attempts),
        ])

    table = _md_table(
        ["Step", "Runs", "Retry rate", "Failure rate", "Median attempts"],
        rows,
    ) if rows else "_No steps with sufficient history (minimum 2 runs)._"

    lines = [
        "# Flakiness Leaderboard\n",
        f"Generated: {now}\n"
        f"Runs analyzed: {len(run_dirs)}\n",
        table,
        "",
    ]

    return "\n".join(lines)
