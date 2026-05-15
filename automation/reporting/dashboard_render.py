"""HTML rendering helpers for the mOSdat trend dashboard.

Extracted from dashboard.py to keep each file ≤500 LOC.
Internal use only — public API lives in dashboard.py.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any


# ---------------------------------------------------------------------------
# HTML / JS utilities
# ---------------------------------------------------------------------------

_CHARTJS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"


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
# Section builders
# ---------------------------------------------------------------------------

def _build_trend_datasets(per_vm: dict, all_run_ids: list[str]) -> tuple[str, str]:
    """Return (trend_labels_js, trend_datasets_js)."""
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
    return _js(all_run_ids), _js(trend_datasets)


def _build_retry_chart_data(per_step: dict) -> tuple[str, str]:
    """Return (retry_labels_js, retry_values_js) for top-10 flaky steps."""
    retry_items = []
    for step, stats in per_step.items():
        attempts = stats.get("attempts", [])
        if not attempts:
            continue
        retries = sum(max(0, a - 1) for a in attempts)
        retry_rate = retries / len(attempts)
        retry_items.append((step, retry_rate, retries, len(attempts)))
    retry_items.sort(key=lambda x: x[1], reverse=True)
    top10 = retry_items[:10]
    return (
        _js([_truncate(s, 40) for s, *_ in top10]),
        _js([round(r * 100, 1) for _, r, *_ in top10]),
    )


def _build_duration_data(per_step: dict) -> tuple[list, str]:
    """Return (duration_items sorted desc, duration_table_rows_html)."""
    duration_items = []
    for step, stats in per_step.items():
        durs = stats.get("durations_s", [])
        if not durs:
            continue
        avg_s = mean(durs)
        duration_items.append((step, avg_s, len(durs)))
    duration_items.sort(key=lambda x: x[1], reverse=True)
    table_rows = "".join(
        f"<tr><td>{_esc(s)}</td><td>{avg_s:.2f}s</td><td>{n}</td></tr>"
        for s, avg_s, n in duration_items
    )
    return duration_items, table_rows


def _build_recordings_section(per_vm: dict, results_root: str, output_base: Path) -> str:
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
                rec_cells.append(f'<a href="{_esc(href)}">{kind}</a>')
            if rec_cells:
                recording_rows.append(
                    "<tr>"
                    f"<td>{_esc(run_id)}</td>"
                    f"<td>{_esc(vm_name)}</td>"
                    f"<td>{' · '.join(rec_cells)}</td>"
                    "</tr>"
                )
    body = (
        "".join(recording_rows)
        if recording_rows
        else '<tr><td colspan="3">No recording artifacts found.</td></tr>'
    )
    return f"""
<section id="recordings">
  <h2>Session Recordings</h2>
  <p>Change-filtered replay artifacts exported by the recorder.</p>
  <table>
    <thead><tr><th>Run</th><th>VM</th><th>Artifacts</th></tr></thead>
    <tbody>{body}</tbody>
  </table>
</section>"""


def _build_regression_section(regressions: list[dict]) -> str:
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
        return f"""
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
    return """
<section id="regressions">
  <h2>Regression Flags <span class="badge badge-ok">0</span></h2>
  <p>No regressions detected (no step dropped &gt;10 pp in the last 7 days vs prior 30 days).</p>
</section>"""


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_dashboard(aggregates: dict[str, Any], output: Path) -> None:
    """Produce a single self-contained static HTML dashboard file."""
    from .dashboard import detect_regressions  # avoid circular at import time

    regressions = detect_regressions(aggregates)
    per_vm = aggregates.get("per_vm", {})
    per_step = aggregates.get("per_step", {})
    runs_meta = aggregates.get("runs", [])
    results_root = aggregates.get("results_root", "")
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    all_run_ids = [r["run_id"] for r in runs_meta]
    trend_labels, trend_datasets_js = _build_trend_datasets(per_vm, all_run_ids)
    retry_labels, retry_values = _build_retry_chart_data(per_step)
    duration_items, duration_table_rows = _build_duration_data(per_step)

    recordings_section = _build_recordings_section(per_vm, results_root, output.parent)
    reg_section = _build_regression_section(regressions)

    top15_dur = duration_items[:15]
    dur_labels = _js([_truncate(s, 40) for s, *_ in top15_dur])
    dur_values = _js([round(avg_s, 2) for _, avg_s, _ in top15_dur])

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
