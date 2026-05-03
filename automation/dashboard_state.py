"""Build live dashboard state from functional run artifacts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


def build_dashboard_state(
    results_root: Path,
    *,
    warn_after: int = 90,
    stale_after: int = 180,
    now: Optional[datetime] = None,
) -> dict:
    """Return triage state for all functional runs under results_root."""
    now = now or datetime.now()
    functional_root = results_root / "functional"
    runs = []
    totals = {"runs": 0, "vms": 0, "running": 0, "pass": 0, "fail": 0, "stale": 0, "partial": 0}
    failures = []

    if not functional_root.exists():
        return {"generated_at": now.isoformat(), "warn_after": warn_after, "stale_after": stale_after,
                "totals": totals, "runs": [], "failures": []}

    for run_dir in sorted((p for p in functional_root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        vms = []
        latest_ts = None
        for vm_dir in sorted((p for p in run_dir.iterdir() if p.is_dir()), key=lambda p: p.name):
            vm_state = _build_vm_state(run_dir.name, vm_dir, now, warn_after, stale_after)
            vms.append(vm_state)
            vm_latest_ts = _parse_ts(vm_state.get("latest_ts"))
            if vm_latest_ts and (latest_ts is None or vm_latest_ts > latest_ts):
                latest_ts = vm_latest_ts
            totals["vms"] += 1
            totals[vm_state["status"]] = totals.get(vm_state["status"], 0) + 1
            failures.extend(vm_state["failures"])
        run_status = _rollup_status([vm["status"] for vm in vms])
        age = max(0, int((now - latest_ts).total_seconds())) if latest_ts else None
        runs.append({
            "name": run_dir.name,
            "status": run_status,
            "latest_ts": latest_ts.isoformat() if latest_ts else None,
            "age_seconds": age,
            "vms": vms,
        })
        totals["runs"] += 1

    runs.sort(key=lambda run: (run.get("latest_ts") or run["name"]), reverse=True)
    failures.sort(key=lambda failure: failure.get("ts", ""), reverse=True)
    return {
        "generated_at": now.isoformat(),
        "warn_after": warn_after,
        "stale_after": stale_after,
        "totals": totals,
        "runs": runs,
        "failures": failures,
    }


def _build_vm_state(run: str, vm_dir: Path, now: datetime, warn_after: int, stale_after: int) -> dict:
    events = _read_events(vm_dir / "events.jsonl")
    screenshots = _collect_screenshots(run, vm_dir)
    steps = _group_steps(events, screenshots)
    latest_event = events[-1] if events else None
    first_event = events[0] if events else None
    first_ts = _parse_ts(first_event.get("ts")) if first_event else None
    latest_ts = _parse_ts(latest_event.get("ts")) if latest_event else None
    age = max(0, int((now - latest_ts).total_seconds())) if latest_ts else None
    failures = _extract_failures(run, vm_dir.name, events, screenshots)
    status = _classify_status(steps, latest_event, failures, age, warn_after, stale_after)
    duration = _duration_seconds(first_ts, latest_ts, now, status)
    current_step = _current_step(steps)

    return {
        "run": run,
        "vm": vm_dir.name,
        "status": status,
        "current_step": current_step,
        "latest_event": latest_event.get("event") if latest_event else None,
        "latest_ts": latest_event.get("ts") if latest_event else None,
        "age_seconds": age,
        "duration_seconds": duration,
        "steps": steps,
        "failures": failures,
        "latest_screenshot": _latest_screenshot(screenshots),
    }


def _read_events(path: Path) -> list[dict]:
    events = []
    if not path.exists():
        return events
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _collect_screenshots(run: str, vm_dir: Path) -> dict[str, list[dict]]:
    screenshots: dict[str, list[dict]] = {}
    for path in sorted(vm_dir.glob("*.png")):
        step = _step_from_filename(path.name)
        if step is None:
            continue
        screenshots.setdefault(str(step), []).append({
            "filename": path.name,
            "url": f"/png/{run}/{vm_dir.name}/{path.name}",
            "kind": _screenshot_kind(path.name),
        })
    return screenshots


def _group_steps(events: list[dict], screenshots: dict[str, list[dict]]) -> list[dict]:
    by_step: dict[str, dict] = {}
    for event in events:
        step_num = event.get("step_num")
        if step_num is None:
            continue
        key = str(step_num)
        step = by_step.setdefault(key, {
            "step_num": step_num,
            "label": "",
            "kind": "",
            "status": "running",
            "duration_ms": None,
            "attempts": None,
            "events": [],
            "screenshots": [],
        })
        step["events"].append(_event_summary(event))
        if event.get("event") == "step_start":
            step["label"] = event.get("label", "") or step["label"]
            step["kind"] = event.get("kind", "") or step["kind"]
        if event.get("event") == "step_end":
            step["status"] = "pass" if event.get("status") == "ok" else "fail"
            step["duration_ms"] = event.get("duration_ms")
            step["attempts"] = event.get("attempts")
    for key, shots in screenshots.items():
        step = by_step.setdefault(key, {
            "step_num": int(key) if key.isdigit() else key,
            "label": "",
            "kind": "",
            "status": "running",
            "duration_ms": None,
            "attempts": None,
            "events": [],
            "screenshots": [],
        })
        step["screenshots"] = shots[-8:]
        if step["status"] == "running" and any(shot.get("kind") == "fail" for shot in shots):
            step["status"] = "fail"
    return [by_step[key] for key in sorted(by_step.keys(), key=_sort_step_key)]


def _extract_failures(run: str, vm: str, events: list[dict], screenshots: dict[str, list[dict]]) -> list[dict]:
    failures = []
    last_vlm_by_step: dict[str, dict] = {}
    for event in events:
        step_key = str(event.get("step_num")) if event.get("step_num") is not None else None
        if step_key and event.get("event") in {"vlm_verify", "launch_verify", "vlm_localize"}:
            last_vlm_by_step[step_key] = event
        if event.get("event") == "step_end" and event.get("status") != "ok" and step_key:
            shot = screenshots.get(step_key, [])[-1:] or []
            vlm = last_vlm_by_step.get(step_key, {})
            failures.append({
                "run": run,
                "vm": vm,
                "step_num": event.get("step_num"),
                "ts": event.get("ts", ""),
                "status": event.get("status", "failed"),
                "duration_ms": event.get("duration_ms"),
                "attempts": event.get("attempts"),
                "cause": _failure_cause(event, vlm, shot[0] if shot else None),
                "question": vlm.get("question", ""),
                "answer": vlm.get("answer", ""),
                "screenshot": shot[0] if shot else None,
            })
    failed_steps = {str(failure["step_num"]) for failure in failures}
    for step_key, shots in screenshots.items():
        if step_key in failed_steps or not any(shot.get("kind") == "fail" for shot in shots):
            continue
        failures.append({
            "run": run,
            "vm": vm,
            "step_num": int(step_key) if step_key.isdigit() else step_key,
            "ts": "",
            "status": "failed",
            "duration_ms": None,
            "attempts": None,
            "cause": "screenshot-only failure",
            "question": "",
            "answer": "",
            "screenshot": shots[-1] if shots else None,
        })
    return failures


def _classify_status(steps: list[dict], latest_event: Optional[dict], failures: list[dict],
                     age: Optional[int], warn_after: int, stale_after: int) -> str:
    if failures:
        return "fail"
    if latest_event is None and steps:
        return "partial"
    if age is not None and age >= stale_after:
        return "stale"
    if not steps:
        return "pending"
    if steps and all(step["status"] == "pass" for step in steps):
        return "pass"
    if latest_event and latest_event.get("event") == "step_end":
        return "partial"
    if age is not None and age >= warn_after:
        return "stale"
    return "running"


def _current_step(steps: list[dict]) -> Optional[dict]:
    for step in reversed(steps):
        if step["status"] == "running":
            return {"step_num": step["step_num"], "label": step.get("label", ""), "kind": step.get("kind", "")}
    return None


def _rollup_status(statuses: list[str]) -> str:
    if not statuses:
        return "pending"
    for status in ("fail", "stale", "running", "partial"):
        if status in statuses:
            return status
    if all(status == "pass" for status in statuses):
        return "pass"
    return "partial"


def _event_summary(event: dict) -> dict:
    keys = ("ts", "event", "step_num", "kind", "label", "status", "duration_ms", "attempts",
            "question", "answer", "latency_ms", "process", "window")
    return {key: event[key] for key in keys if key in event}


def _failure_cause(event: dict, vlm: dict, screenshot: Optional[dict]) -> str:
    if event.get("event") == "step_end" and event.get("status") != "ok":
        if vlm.get("event") == "launch_verify":
            return "launch verify failed"
        if vlm.get("event") == "vlm_verify" and str(vlm.get("answer", "")).lower() in {"no", "false"}:
            return "VLM said no"
        if screenshot and screenshot.get("kind") == "fail":
            return "step failed with screenshot"
    return "step failed"


def _latest_screenshot(screenshots: dict[str, list[dict]]) -> Optional[dict]:
    latest = None
    for shots in screenshots.values():
        for shot in shots:
            if latest is None or shot["filename"] > latest["filename"]:
                latest = shot
    return latest


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _duration_seconds(
    first_ts: Optional[datetime],
    latest_ts: Optional[datetime],
    now: datetime,
    status: str,
) -> Optional[int]:
    if first_ts is None:
        return None
    end_ts = now if status in {"running", "stale"} else latest_ts
    if end_ts is None:
        return None
    return max(0, int((end_ts - first_ts).total_seconds()))


def _step_from_filename(filename: str) -> Optional[int]:
    marker = "_step"
    if marker not in filename:
        return None
    rest = filename.split(marker, 1)[1]
    digits = []
    for char in rest:
        if char.isdigit():
            digits.append(char)
        else:
            break
    return int("".join(digits)) if digits else None


def _screenshot_kind(filename: str) -> str:
    for kind in ("verify_poll", "localize", "verify_input", "verify_click", "verify_click_diff", "canary_verify", "fail"):
        if kind in filename:
            return kind
    return "screenshot"


def _sort_step_key(key: str):
    parts = str(key).split(".")
    return tuple(int(part) if part.isdigit() else part for part in parts)
