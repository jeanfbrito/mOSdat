"""Data collection and aggregation for functional HTML reports."""

import json
from datetime import datetime
from pathlib import Path


def load_events(path: Path) -> list:
    if not path.exists():
        return []
    events = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def collect_screenshots(run_dir: Path) -> set:
    """Return set of screenshot filenames (name only, no dir) present on disk."""
    return {p.name for p in run_dir.glob("*.png")}


def group_by_step(events: list) -> dict:
    """Group events by step_num. Returns ordered dict step_num -> list of events."""
    steps: dict = {}
    for event in events:
        step_num = event.get("step_num")
        if step_num is None:
            continue
        key = str(step_num)
        if key not in steps:
            steps[key] = []
        steps[key].append(event)
    return dict(sorted(steps.items(), key=lambda item: _sort_key(item[0])))


def extract_meta(events: list, run_dir: Path) -> dict:
    """Pull top-level run metadata from events."""
    start_ts = None
    end_ts = None
    scenario_name = run_dir.name

    step_starts = {}
    step_ends = {}
    retried_steps = set()

    for event in events:
        ts = event.get("ts", "")
        event_type = event.get("event", "")
        step_num = event.get("step_num")

        if start_ts is None and ts:
            start_ts = ts
        if ts:
            end_ts = ts

        if event_type == "step_start" and step_num is not None:
            step_starts[str(step_num)] = event
        if event_type == "step_end" and step_num is not None:
            step_ends[str(step_num)] = event
        if event_type == "retry" and step_num is not None:
            retried_steps.add(str(step_num))

    total = len(step_starts)
    passed = sum(1 for event in step_ends.values() if event.get("status") == "ok")
    failed = sum(1 for event in step_ends.values() if event.get("status") == "failed")
    retried = len(retried_steps)

    if failed > 0:
        overall = "FAIL"
    elif total > 0 and passed == total:
        overall = "PASS"
    else:
        overall = "PARTIAL"

    duration_str = ""
    if start_ts and end_ts:
        try:
            start_time = datetime.fromisoformat(start_ts)
            end_time = datetime.fromisoformat(end_ts)
            secs = (end_time - start_time).total_seconds()
            duration_str = f"{secs:.1f}s"
        except Exception:
            pass

    return {
        "scenario_name": scenario_name,
        "start_ts": start_ts or "",
        "end_ts": end_ts or "",
        "duration": duration_str,
        "overall": overall,
        "total": total,
        "passed": passed,
        "failed": failed,
        "retried": retried,
        "commit": get_git_commit(run_dir),
    }


def get_git_commit(run_dir: Path) -> str:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
            cwd=run_dir,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _sort_key(key):
    parts = str(key).split(".")
    return tuple(int(part) if part.isdigit() else part for part in parts)
