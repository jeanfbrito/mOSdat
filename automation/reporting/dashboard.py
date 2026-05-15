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

# Re-export render_dashboard so callers importing it from this module still work.
from .dashboard_render import render_dashboard  # noqa: F401


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
    """Flag performance regressions across three dimensions.

    Returns list of dicts with keys:
        kind            — "pass_rate_drop" | "duration_regression" | "vm_pass_rate"
        step / vm / recent_rate / baseline_rate / drop_pp /
        recent_mean_s / baseline_mean_s / duration_ratio /
        recent_runs / baseline_runs
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

    # --- 1. Step pass-rate drops ---
    for step, stats in per_step.items():
        run_ids = stats.get("run_ids", [])
        if not run_ids:
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
