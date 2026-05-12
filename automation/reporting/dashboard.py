#!/usr/bin/env python3
"""H2.2: Trend dashboard — aggregate events.jsonl across functional runs.

Usage (CLI):
    mosdat dashboard --root results/ --output dashboard.html

Or as a library:
    from automation.reporting.dashboard import aggregate_runs, render_dashboard, detect_regressions
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_runs(results_root: Path) -> dict[str, Any]:
    """Walk results/functional/<run>/<vm>/events.jsonl and build aggregates.

    Returns a dict with keys:
        "runs":     list of run metadata dicts (sorted chronologically)
        "per_vm":   {vm_name: {run_id: {"pass_rate": float, "run_ts": str}}}
        "per_step": {step_label: {"attempts": [int, ...], "passed": int,
                                   "failed": int, "durations_s": [float, ...],
                                   "run_ids": [str, ...]}}
        "results_root": str path used
    """
    functional_dir = results_root / "functional"
    if not functional_dir.exists():
        return {
            "runs": [],
            "per_vm": {},
            "per_step": {},
            "results_root": str(results_root),
        }

    # Collect run dirs: YYYY-MM-DD* pattern
    run_dirs = sorted(
        (d for d in functional_dir.iterdir()
         if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}", d.name)),
        key=lambda d: d.name,
    )

    runs_meta: list[dict] = []
    per_vm: dict[str, dict[str, dict]] = defaultdict(dict)
    per_step: dict[str, dict] = defaultdict(lambda: {
        "attempts": [],
        "passed": 0,
        "failed": 0,
        "durations_s": [],
        "run_ids": [],
    })

    for run_dir in run_dirs:
        run_id = run_dir.name
        # Parse timestamp from run dir name: YYYY-MM-DD_HHMMSS or YYYY-MM-DD
        ts_match = re.match(r"(\d{4}-\d{2}-\d{2})(?:[_T](\d{6}|\d{2}:\d{2}:\d{2}))?", run_id)
        run_ts = ts_match.group(0).replace("_", "T") if ts_match else run_id

        for vm_dir in sorted(run_dir.iterdir()):
            if not vm_dir.is_dir():
                continue
            vm_name = vm_dir.name
            events_file = vm_dir / "events.jsonl"
            if not events_file.exists():
                continue

            # Per-step state within this file
            open_steps: dict[int, dict] = {}  # step_num -> {label, ts_start}
            vm_step_pass = 0
            vm_step_total = 0

            try:
                with events_file.open("r", encoding="utf-8", errors="replace") as fh:
                    for raw in fh:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            ev = json.loads(raw)
                        except json.JSONDecodeError:
                            warnings.warn(f"[dashboard] Malformed JSON in {events_file}: {raw[:80]}")
                            continue

                        etype = ev.get("event", "")
                        step_num = ev.get("step_num")
                        label = ev.get("label", "")
                        label_key = (label[:60] if label else f"step_{step_num}")

                        if etype == "step_start":
                            ts_raw = ev.get("ts") or ev.get("timestamp")
                            open_steps[step_num] = {
                                "label_key": label_key,
                                "ts_start": _parse_ts(ts_raw),
                            }

                        elif etype == "step_end":
                            status = ev.get("status", "")
                            attempts = ev.get("attempts", 1) or 1
                            ts_raw = ev.get("ts") or ev.get("timestamp")
                            ts_end = _parse_ts(ts_raw)

                            # Duration
                            duration_s: float | None = None
                            if step_num in open_steps and ts_end is not None:
                                ts_start = open_steps[step_num]["ts_start"]
                                if ts_start is not None:
                                    duration_s = ts_end - ts_start

                            lk = open_steps.get(step_num, {}).get("label_key", label_key)
                            per_step[lk]["attempts"].append(attempts)
                            per_step[lk]["run_ids"].append(run_id)
                            if status == "passed":
                                per_step[lk]["passed"] += 1
                                vm_step_pass += 1
                            elif status == "failed":
                                per_step[lk]["failed"] += 1
                            if duration_s is not None:
                                per_step[lk]["durations_s"].append(duration_s)
                            vm_step_total += 1

                            if step_num in open_steps:
                                del open_steps[step_num]

            except OSError as exc:
                warnings.warn(f"[dashboard] Could not read {events_file}: {exc}")
                continue

            pass_rate = (vm_step_pass / vm_step_total) if vm_step_total else None
            recording = _collect_recording(vm_dir)
            per_vm[vm_name][run_id] = {
                "pass_rate": pass_rate,
                "run_ts": run_ts,
                "passed": vm_step_pass,
                "total": vm_step_total,
                "recording": recording,
            }

        runs_meta.append({"run_id": run_id, "run_ts": run_ts})

    return {
        "runs": runs_meta,
        "per_vm": dict(per_vm),
        "per_step": dict(per_step),
        "results_root": str(results_root),
    }


def _collect_recording(vm_dir: Path) -> dict[str, str]:
    rec = {}
    mp4 = vm_dir / "recording" / "session.mp4"
    gif = vm_dir / "recording" / "session.gif"
    if mp4.exists():
        rec["mp4"] = "recording/session.mp4"
    if gif.exists():
        rec["gif"] = "recording/session.gif"
    return rec


def _parse_ts(ts_raw: Any) -> float | None:
    """Parse a timestamp to a float (unix seconds). Returns None on failure."""
    if ts_raw is None:
        return None
    if isinstance(ts_raw, (int, float)):
        return float(ts_raw)
    if isinstance(ts_raw, str):
        # Try ISO 8601
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(ts_raw, fmt)
                return dt.replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------

def detect_regressions(
    aggregates: dict[str, Any],
    recent_days: int = 7,
    baseline_days: int = 30,
    threshold_multiplier: float = 1.5,
    min_pass_rate: float = 0.85,
) -> list[dict[str, Any]]:
    """Flag performance regressions across three dimensions:

    1. Step pass-rate drop: pass rate dropped >10 pp in last recent_days vs
       prior baseline_days.
    2. Step duration regression: mean step duration over recent_days exceeds
       baseline mean * threshold_multiplier.
    3. VM pass-rate below floor: any VM's recent mean pass rate is below
       min_pass_rate.

    Returns list of dicts with keys:
        kind            — "pass_rate_drop" | "duration_regression" | "vm_pass_rate"
        step            — step label (empty string for vm_pass_rate kind)
        vm              — VM name (empty string for step-level kinds)
        recent_rate     — recent pass rate (pass_rate_drop / vm_pass_rate kinds)
        baseline_rate   — baseline pass rate (pass_rate_drop kind)
        drop_pp         — drop in percentage points (pass_rate_drop kind)
        recent_mean_s   — recent mean duration in seconds (duration_regression kind)
        baseline_mean_s — baseline mean duration in seconds (duration_regression kind)
        duration_ratio  — recent_mean_s / baseline_mean_s (duration_regression kind)
        recent_runs     — number of runs in recent window
        baseline_runs   — number of runs in baseline window
    """
    per_step = aggregates.get("per_step", {})
    per_vm = aggregates.get("per_vm", {})
    now_ts = datetime.now(tz=timezone.utc).timestamp()
    recent_cutoff = now_ts - recent_days * 86400
    baseline_cutoff = now_ts - baseline_days * 86400

    # Build run_id -> run_ts lookup from per_vm data
    run_ts_map: dict[str, float] = {}
    for vm_data in per_vm.values():
        for run_id, info in vm_data.items():
            if run_id not in run_ts_map:
                ts = _parse_run_ts(info.get("run_ts", run_id))
                if ts is not None:
                    run_ts_map[run_id] = ts

    regressions: list[dict[str, Any]] = []

    # --- 1. Step pass-rate drops (aggregate over all VMs) ---
    for step, stats in per_step.items():
        run_ids = stats.get("run_ids", [])
        total_occ = len(run_ids)
        if total_occ == 0:
            continue

        recent_rates: list[float] = []
        baseline_rates: list[float] = []

        for vm_data in per_vm.values():
            for run_id, info in vm_data.items():
                ts = run_ts_map.get(run_id)
                if ts is None:
                    continue
                rate = info.get("pass_rate")
                if rate is None:
                    continue
                if ts >= recent_cutoff:
                    recent_rates.append(rate)
                elif ts >= baseline_cutoff:
                    baseline_rates.append(rate)

        if not recent_rates or not baseline_rates:
            continue

        recent_avg = mean(recent_rates)
        baseline_avg = mean(baseline_rates)
        drop = baseline_avg - recent_avg

        if drop > 0.10:
            regressions.append({
                "kind": "pass_rate_drop",
                "step": step,
                "vm": "",
                "recent_rate": recent_avg,
                "baseline_rate": baseline_avg,
                "drop_pp": drop,
                "recent_mean_s": 0.0,
                "baseline_mean_s": 0.0,
                "duration_ratio": 0.0,
                "recent_runs": len(recent_rates),
                "baseline_runs": len(baseline_rates),
            })

    # --- 2. Step duration regressions ---
    for step, stats in per_step.items():
        run_ids = stats.get("run_ids", [])
        durations = stats.get("durations_s", [])
        if not durations or not run_ids:
            continue

        # Pair each duration with its run_id (parallel lists)
        recent_durs: list[float] = []
        baseline_durs: list[float] = []

        for run_id, dur in zip(run_ids, durations):
            ts = run_ts_map.get(run_id)
            if ts is None:
                continue
            if ts >= recent_cutoff:
                recent_durs.append(dur)
            elif ts >= baseline_cutoff:
                baseline_durs.append(dur)

        if not recent_durs or not baseline_durs:
            continue

        recent_mean = mean(recent_durs)
        baseline_mean = mean(baseline_durs)
        if baseline_mean <= 0:
            continue

        ratio = recent_mean / baseline_mean
        if ratio >= threshold_multiplier:
            regressions.append({
                "kind": "duration_regression",
                "step": step,
                "vm": "",
                "recent_rate": 0.0,
                "baseline_rate": 0.0,
                "drop_pp": 0.0,
                "recent_mean_s": recent_mean,
                "baseline_mean_s": baseline_mean,
                "duration_ratio": ratio,
                "recent_runs": len(recent_durs),
                "baseline_runs": len(baseline_durs),
            })

    # --- 3. Per-VM pass-rate below floor ---
    for vm_name, vm_data in per_vm.items():
        recent_rates_vm: list[float] = []
        for run_id, info in vm_data.items():
            ts = run_ts_map.get(run_id)
            if ts is None:
                continue
            rate = info.get("pass_rate")
            if rate is None:
                continue
            if ts >= recent_cutoff:
                recent_rates_vm.append(rate)

        if not recent_rates_vm:
            continue

        vm_recent_avg = mean(recent_rates_vm)
        if vm_recent_avg < min_pass_rate:
            regressions.append({
                "kind": "vm_pass_rate",
                "step": "",
                "vm": vm_name,
                "recent_rate": vm_recent_avg,
                "baseline_rate": 0.0,
                "drop_pp": 0.0,
                "recent_mean_s": 0.0,
                "baseline_mean_s": 0.0,
                "duration_ratio": 0.0,
                "recent_runs": len(recent_rates_vm),
                "baseline_runs": 0,
            })

    regressions.sort(key=lambda x: x.get("drop_pp", 0.0), reverse=True)
    return regressions


def build_regression_summary(regressions: list[dict[str, Any]]) -> str:
    """Return a human-readable plain-text summary of regression items."""
    if not regressions:
        return "No regressions detected."

    lines: list[str] = [f"{len(regressions)} regression(s) detected:"]
    for r in regressions:
        kind = r.get("kind", "")
        if kind == "pass_rate_drop":
            lines.append(
                f"  [pass-rate drop] step={r['step']!r}  "
                f"baseline={r['baseline_rate']*100:.1f}%  "
                f"recent={r['recent_rate']*100:.1f}%  "
                f"drop={r['drop_pp']*100:.1f}pp"
            )
        elif kind == "duration_regression":
            lines.append(
                f"  [duration spike] step={r['step']!r}  "
                f"baseline={r['baseline_mean_s']:.2f}s  "
                f"recent={r['recent_mean_s']:.2f}s  "
                f"ratio={r['duration_ratio']:.2f}x"
            )
        elif kind == "vm_pass_rate":
            lines.append(
                f"  [vm pass-rate]   vm={r['vm']!r}  "
                f"recent={r['recent_rate']*100:.1f}%  (below floor)"
            )
    return "\n".join(lines)


def _parse_run_ts(run_ts_str: str) -> float | None:
    """Parse a run_ts string like '2026-04-14T182233' or '2026-04-14' to unix ts."""
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H%M%S",
        "%Y-%m-%d_%H%M%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(run_ts_str[:len(fmt) + 2], fmt)
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    # Try just stripping to date
    m = re.match(r"(\d{4}-\d{2}-\d{2})", run_ts_str)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

_CHARTJS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"


def render_dashboard(aggregates: dict[str, Any], output: Path) -> None:
    """Produce a single self-contained static HTML dashboard file.

    Sections:
      1. Pass-rate trend over time (line chart per VM)
      2. Retry-rate per step — top-10 flakiest (bar chart)
      3. Mean step duration — table + bar chart
      4. Regression flags
    """
    regressions = detect_regressions(aggregates)
    per_vm = aggregates.get("per_vm", {})
    per_step = aggregates.get("per_step", {})
    runs_meta = aggregates.get("runs", [])
    results_root = aggregates.get("results_root", "")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- Section 1: pass-rate trend data ----
    # Collect all run_ids in chronological order
    all_run_ids = [r["run_id"] for r in runs_meta]
    vm_colors = [
        "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
        "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
        "#9c755f", "#bab0ac",
    ]

    trend_datasets = []
    for idx, (vm_name, run_map) in enumerate(sorted(per_vm.items())):
        color = vm_colors[idx % len(vm_colors)]
        data_points = []
        for run_id in all_run_ids:
            info = run_map.get(run_id)
            rate = info["pass_rate"] if info and info["pass_rate"] is not None else None
            if rate is not None:
                data_points.append({"x": run_id, "y": round(rate * 100, 1)})
        if data_points:
            trend_datasets.append({
                "label": vm_name,
                "data": data_points,
                "borderColor": color,
                "backgroundColor": color,
                "tension": 0.2,
                "fill": False,
            })

    trend_labels = _js(all_run_ids)
    trend_datasets_js = _js(trend_datasets)

    # ---- Section 2: retry-rate per step (top 10) ----
    retry_items = []
    for step, stats in per_step.items():
        attempts = stats.get("attempts", [])
        if not attempts:
            continue
        # retry count = sum(attempts - 1)
        retries = sum(max(0, a - 1) for a in attempts)
        retry_rate = retries / len(attempts)
        retry_items.append((step, retry_rate, retries, len(attempts)))

    retry_items.sort(key=lambda x: x[1], reverse=True)
    top10_retry = retry_items[:10]

    retry_labels = _js([_truncate(s, 40) for s, *_ in top10_retry])
    retry_values = _js([round(r * 100, 1) for _, r, *_ in top10_retry])

    # ---- Section 3: mean step duration ----
    duration_items = []
    for step, stats in per_step.items():
        durs = stats.get("durations_s", [])
        if not durs:
            continue
        avg_s = mean(durs)
        duration_items.append((step, avg_s, len(durs)))

    duration_items.sort(key=lambda x: x[1], reverse=True)

    # Table rows (all steps with duration data)
    duration_table_rows = "".join(
        f"<tr><td>{_esc(s)}</td><td>{avg_s:.2f}s</td><td>{n}</td></tr>"
        for s, avg_s, n in duration_items
    )

    # ---- Section 4: session recording artifacts ----
    output_base = output.parent
    results_root_path = Path(results_root) if results_root else Path(".")
    recording_rows = []
    for vm_name, run_map in sorted(per_vm.items()):
        for run_id, info in sorted(run_map.items()):
            recording = info.get("recording") or {}
            if not recording:
                continue
            rec_cells = []
            for kind in ("mp4", "gif"):
                rel = recording.get(kind)
                if not rel:
                    continue
                target = results_root_path / "functional" / run_id / vm_name / rel
                href = os.path.relpath(target, start=output_base).replace(os.sep, "/")
                rec_cells.append(f"<a href=\"{_esc(href)}\">{kind}</a>")
            if rec_cells:
                recording_rows.append(
                    "<tr>"
                    f"<td>{_esc(run_id)}</td>"
                    f"<td>{_esc(vm_name)}</td>"
                    f"<td>{' · '.join(rec_cells)}</td>"
                    "</tr>"
                )
    recordings_section = f"""
<section id="recordings">
  <h2>Session Recordings</h2>
  <p>Change-filtered replay artifacts exported by the recorder.</p>
  <table>
    <thead><tr><th>Run</th><th>VM</th><th>Artifacts</th></tr></thead>
    <tbody>{''.join(recording_rows) if recording_rows else '<tr><td colspan="3">No recording artifacts found.</td></tr>'}</tbody>
  </table>
</section>"""

    # Bar chart: top 15 by mean duration
    top15_dur = duration_items[:15]
    dur_labels = _js([_truncate(s, 40) for s, *_ in top15_dur])
    dur_values = _js([round(avg_s, 2) for _, avg_s, _ in top15_dur])

    # ---- Section 5: regression table ----
    if regressions:
        reg_rows = "".join(
            "<tr>"
            f"<td>{_esc(r['step'])}</td>"
            f"<td>{r['baseline_rate']*100:.1f}%</td>"
            f"<td>{r['recent_rate']*100:.1f}%</td>"
            f"<td class='reg-drop'>-{r['drop_pp']*100:.1f}pp</td>"
            f"<td>{r['baseline_runs']}</td>"
            f"<td>{r['recent_runs']}</td>"
            "</tr>"
            for r in regressions
        )
        reg_section = f"""
<section id="regressions">
  <h2>Regression Flags <span class="badge badge-warn">{len(regressions)}</span></h2>
  <p>Steps whose pass rate dropped &gt;10 pp in the last 7 days vs the prior 30-day baseline.</p>
  <table>
    <thead><tr>
      <th>Step</th><th>Baseline rate</th><th>Recent rate</th>
      <th>Drop</th><th>Baseline runs</th><th>Recent runs</th>
    </tr></thead>
    <tbody>{reg_rows}</tbody>
  </table>
</section>"""
    else:
        reg_section = """
<section id="regressions">
  <h2>Regression Flags <span class="badge badge-ok">0</span></h2>
  <p>No regressions detected (no step dropped &gt;10 pp in the last 7 days vs prior 30 days).</p>
</section>"""

    # ---- Assemble HTML ----
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mOSdat Trend Dashboard</title>
<script src="{_CHARTJS_CDN}"></script>
<style>
  :root {{
    --bg: #1e1e2e; --surface: #2a2a3d; --surface2: #313147;
    --text: #cdd6f4; --sub: #a6adc8; --accent: #89b4fa;
    --warn: #f38ba8; --ok: #a6e3a1; --border: #45475a;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font: 14px/1.5 system-ui, sans-serif; padding: 1.5rem 2rem; }}
  h1 {{ font-size: 1.6rem; color: var(--accent); margin-bottom: .25rem; }}
  h2 {{ font-size: 1.15rem; color: var(--accent); margin: 1.75rem 0 .6rem; }}
  .meta {{ color: var(--sub); font-size: .85rem; margin-bottom: 1.5rem; }}
  section {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; margin-bottom: 1.5rem; }}
  .chart-wrap {{ position: relative; height: 280px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .85rem; margin-top: .5rem; }}
  th {{ background: var(--surface2); color: var(--sub); text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--border); }}
  td {{ padding: .35rem .6rem; border-bottom: 1px solid var(--border); }}
  tr:hover td {{ background: var(--surface2); }}
  .reg-drop {{ color: var(--warn); font-weight: 600; }}
  .badge {{ display: inline-block; padding: .1rem .45rem; border-radius: 4px; font-size: .8rem; font-weight: 600; margin-left: .4rem; }}
  .badge-warn {{ background: var(--warn); color: #1e1e2e; }}
  .badge-ok {{ background: var(--ok); color: #1e1e2e; }}
  p {{ color: var(--sub); font-size: .9rem; margin-top: .3rem; }}
</style>
</head>
<body>
<h1>mOSdat Trend Dashboard</h1>
<div class="meta">Generated: {generated_at} &nbsp;|&nbsp; Results root: {_esc(results_root)} &nbsp;|&nbsp; Runs: {len(runs_meta)}</div>

<section id="pass-rate-trend">
  <h2>Pass-Rate Trend by VM</h2>
  <p>Pass rate (%) per functional run, one line per VM.</p>
  <div class="chart-wrap"><canvas id="trendChart"></canvas></div>
</section>

<section id="retry-rate">
  <h2>Top-10 Flakiest Steps — Retry Rate</h2>
  <p>Steps ranked by retry rate (retries / total executions). Higher = flakier.</p>
  <div class="chart-wrap"><canvas id="retryChart"></canvas></div>
</section>

<section id="duration">
  <h2>Mean Step Duration</h2>
  <p>Average wall-clock duration per step (seconds). Bar shows top 15 by mean duration.</p>
  <div class="chart-wrap"><canvas id="durChart"></canvas></div>
  <h3 style="margin-top:1rem;font-size:.95rem;color:var(--sub)">All steps with timing data</h3>
  <table>
    <thead><tr><th>Step</th><th>Mean duration</th><th>Samples</th></tr></thead>
    <tbody>{duration_table_rows if duration_table_rows else '<tr><td colspan="3">No timing data recorded.</td></tr>'}</tbody>
  </table>
</section>

{recordings_section}

{reg_section}

<script>
const trendLabels = {trend_labels};
const trendDatasets = {trend_datasets_js};

const retryLabels = {retry_labels};
const retryValues = {retry_values};

const durLabels = {dur_labels};
const durValues = {dur_values};

const gridColor = "rgba(69,71,90,0.5)";
const textColor = "#a6adc8";

function baseScales(yLabel) {{
  return {{
    x: {{ ticks: {{ color: textColor, maxRotation: 45 }}, grid: {{ color: gridColor }} }},
    y: {{ title: {{ display: true, text: yLabel, color: textColor }}, ticks: {{ color: textColor }}, grid: {{ color: gridColor }} }},
  }};
}}

// Trend chart
new Chart(document.getElementById("trendChart"), {{
  type: "line",
  data: {{ labels: trendLabels, datasets: trendDatasets }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color: textColor }} }} }},
    scales: baseScales("Pass rate (%)"),
  }},
}});

// Retry chart
new Chart(document.getElementById("retryChart"), {{
  type: "bar",
  data: {{
    labels: retryLabels,
    datasets: [{{ label: "Retry rate (%)", data: retryValues, backgroundColor: "#f28e2b" }}],
  }},
  options: {{
    responsive: true, maintainAspectRatio: false, indexAxis: "y",
    plugins: {{ legend: {{ display: false }} }},
    scales: baseScales("Retry rate (%)"),
  }},
}});

// Duration chart
new Chart(document.getElementById("durChart"), {{
  type: "bar",
  data: {{
    labels: durLabels,
    datasets: [{{ label: "Mean duration (s)", data: durValues, backgroundColor: "#4e79a7" }}],
  }},
  options: {{
    responsive: true, maintainAspectRatio: false, indexAxis: "y",
    plugins: {{ legend: {{ display: false }} }},
    scales: baseScales("Mean duration (s)"),
  }},
}});
</script>
</body>
</html>
"""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(f"[mOSdat] Dashboard written: {output.absolute()}")


def _js(obj: Any) -> str:
    """Serialize obj to a JS-safe JSON literal."""
    return json.dumps(obj, ensure_ascii=False)


def _esc(s: str) -> str:
    """HTML-escape a string."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mosdat dashboard",
        description="H2.2: Generate a static HTML trend dashboard from functional run data.",
    )
    parser.add_argument(
        "--root", type=Path, default=Path("results"),
        help="Root results directory (must contain functional/ subdir). Default: results/",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output HTML path. Default: <root>/functional/dashboard.html",
    )
    parser.add_argument(
        "--alert", action="store_true", default=False,
        help=(
            "When set, send a notification via notify.py if regressions are detected "
            "and NOTIFY_WEBHOOK (or NOTIFY_EMAIL_SMTP) env var is present."
        ),
    )
    parser.add_argument(
        "--threshold-multiplier", type=float, default=1.5, dest="threshold_multiplier",
        metavar="N",
        help=(
            "Duration regression threshold: alert when recent mean step duration "
            "exceeds baseline mean * N. Default: 1.5"
        ),
    )
    parser.add_argument(
        "--min-pass-rate", type=float, default=0.85, dest="min_pass_rate",
        metavar="R",
        help=(
            "VM pass-rate floor (0.0–1.0): alert when a VM's recent mean pass rate "
            "drops below R. Default: 0.85"
        ),
    )
    parser.add_argument(
        "--report-url", type=str, default="", dest="report_url",
        help="URL to include in the alert notification body. Default: empty string.",
    )
    args = parser.parse_args(argv)

    results_root: Path = args.root
    if not results_root.exists():
        print(f"[mOSdat] ERROR: --root path does not exist: {results_root}", file=sys.stderr)
        return 1

    output_path: Path = args.output or (results_root / "functional" / "dashboard.html")

    agg = aggregate_runs(results_root)
    if not agg["runs"]:
        print(f"[mOSdat] WARNING: No functional run directories found under {results_root}/functional/")

    render_dashboard(agg, output_path)

    if args.alert:
        regressions = detect_regressions(
            agg,
            threshold_multiplier=args.threshold_multiplier,
            min_pass_rate=args.min_pass_rate,
        )
        if regressions:
            webhook = os.environ.get("NOTIFY_WEBHOOK", "")
            smtp = os.environ.get("NOTIFY_EMAIL_SMTP", "")
            if webhook or smtp:
                try:
                    from automation.notify import notify  # type: ignore[import]
                    channel = os.environ.get("NOTIFY_CHANNEL", "slack")
                    summary = build_regression_summary(regressions)
                    report_url = args.report_url or str(output_path.absolute())
                    notify(
                        channel,
                        run_label=summary,
                        status="fail",
                        report_url=report_url,
                    )
                    print(f"[mOSdat] Alert sent ({channel}): {len(regressions)} regression(s)")
                except Exception as exc:  # noqa: BLE001
                    print(f"[mOSdat] WARNING: Alert notification failed: {exc}", file=sys.stderr)
            else:
                print(
                    "[mOSdat] --alert set and regressions detected, but NOTIFY_WEBHOOK / "
                    "NOTIFY_EMAIL_SMTP not set — skipping notification.",
                    file=sys.stderr,
                )

    return 0


if __name__ == "__main__":
    sys.exit(cli())
