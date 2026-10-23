"""Card 3: offline resolution of deferred `verify_recorded` checks.

Testing transient UI (toasts) can't afford a VLM round-trip (20-40s) in the
hot path of a 6s toast lifetime. `run_step`'s `verify_recorded` branch (see
`automation/runners/functional_steps.py`) just queues
`{prompt, between, step_num}` onto `runner._pending_recorded_verifies` — no
VLM call, no blocking. This module performs the actual VLM verification
AFTER the scenario run completes, against frames already captured by the
`SessionRecorder` (see `automation/recording/session_recorder.py`).

Known, intentional limitation (per design): this runs strictly after
`FunctionalRunner.run_test`'s per-step retry/checkpoint-rewind loop has
already returned, so a FAIL discovered here cannot trigger a checkpoint
rewind — that loop has already exited by the time frames are analyzed.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union


class DeferredVerifyError(RuntimeError):
    """Raised when a `between` step label/number cannot be resolved.

    Ambiguous (2+ matches) or missing (0 matches) references are hard
    errors — never a silent best-guess. Also raised (as a plain assertion
    surfaced via this type) is not the case for an empty candidate-frame
    window; that is reported as a FAIL result instead (see
    `resolve_pending_verifies`), not an exception, since it is a valid
    (if unfortunate) outcome of a real scenario run.
    """


def _parse_events(events_path: Path) -> tuple[Dict[Any, Dict[str, float]], Dict[str, Set[Any]]]:
    """Parse events.jsonl into (by_step_num, label_to_steps) lookups.

    `by_step_num[step_num]` is a dict with up to `"step_start"`/`"step_end"`
    epoch-timestamp keys. `label_to_steps[label]` is the set of step_nums
    whose `step_start` event carried that `step_label` — used to detect
    ambiguous label references.
    """
    by_step: Dict[Any, Dict[str, float]] = {}
    label_to_steps: Dict[str, Set[Any]] = {}

    if not events_path.exists():
        return by_step, label_to_steps

    with events_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = rec.get("event")
            if event not in ("step_start", "step_end"):
                continue
            step_num = rec.get("step_num")
            ts_raw = rec.get("ts")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = datetime.fromisoformat(ts_raw).timestamp()
            except ValueError:
                continue
            entry = by_step.setdefault(step_num, {})
            entry[event] = ts
            if event == "step_start":
                label = rec.get("step_label")
                if label:
                    label_to_steps.setdefault(str(label), set()).add(step_num)
    return by_step, label_to_steps


def _resolve_endpoint(
    ref: Union[str, int],
    by_step: Dict[Any, Dict[str, float]],
    label_to_steps: Dict[str, Set[Any]],
) -> float:
    """Resolve one `between` entry (step label or step_num) to an epoch ts.

    `int` -> exact step_num match. `str` -> exact `step_label` text match.
    Raises `DeferredVerifyError` on zero or 2+ matches (hard error, per the
    planner's explicit design decision — never a silent best-effort guess).
    """
    if isinstance(ref, int) and not isinstance(ref, bool):
        entry = by_step.get(ref)
        if entry is None:
            raise DeferredVerifyError(
                f"verify_recorded.between: no step found with step_num={ref}"
            )
        ts = entry.get("step_start", entry.get("step_end"))
        if ts is None:
            raise DeferredVerifyError(
                f"verify_recorded.between: step_num={ref} has no timestamped "
                "step_start/step_end event"
            )
        return ts

    matches = label_to_steps.get(str(ref), set())
    if len(matches) == 0:
        raise DeferredVerifyError(
            f"verify_recorded.between: no step found with label={ref!r}"
        )
    if len(matches) > 1:
        raise DeferredVerifyError(
            f"verify_recorded.between: label={ref!r} is ambiguous — matches "
            f"{len(matches)} steps ({sorted(matches, key=str)})"
        )
    step_num = next(iter(matches))
    entry = by_step[step_num]
    ts = entry.get("step_start", entry.get("step_end"))
    if ts is None:
        raise DeferredVerifyError(
            f"verify_recorded.between: label={ref!r} (step_num={step_num}) "
            "has no timestamped step_start/step_end event"
        )
    return ts


def _load_filtered_index(filtered_index_path: Optional[Path]) -> List[Dict[str, Any]]:
    """Parse `filtered_index.jsonl` into a chronologically-sorted frame list.

    Uses the recently-fixed per-frame `filtered_index.jsonl` written by
    `SessionRecorder` — NOT the raw `index.jsonl`, which no longer
    corresponds 1:1 to `filtered/` content (see session_recorder.py's
    "Bug 8 fix" comment: `filtered/` renumbers kept frames independently
    of the raw sequence `index.jsonl` describes).
    """
    frames: List[Dict[str, Any]] = []
    if filtered_index_path is None or not Path(filtered_index_path).exists():
        return frames
    with Path(filtered_index_path).open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            frame_name = rec.get("frame")
            ts_raw = rec.get("ts")
            if not isinstance(frame_name, str) or not isinstance(ts_raw, str):
                continue
            try:
                ts = datetime.fromisoformat(ts_raw).timestamp()
            except ValueError:
                continue
            frames.append({"frame": frame_name, "ts": ts})
    frames.sort(key=lambda r: r["ts"])
    return frames


def resolve_pending_verifies(
    runner, events_path: Path, recording_artifacts
) -> List[Dict[str, Any]]:
    """Resolve all of `runner._pending_recorded_verifies` against captured frames.

    :param runner: the `FunctionalRunner` whose `run_test` just completed.
        Its `._pending_recorded_verifies` list supplies the queued checks,
        and `.vlm.verify(image, prompt)` is used for the actual VLM call
        (benefits automatically from the existing SQLite VLM cache — no new
        caching code needed here).
    :param events_path: the run's `events.jsonl` (see `FunctionalRunner._emit`).
    :param recording_artifacts: the `RecordingArtifacts` returned by
        `SessionRecorder.stop_and_export()` — supplies `filtered_dir` and
        `filtered_index_path`.
    :returns: a list of `{step_num, prompt, passed, frames_checked}` dicts,
        one per queued check, in queue order.
    :raises DeferredVerifyError: if a `between` label/step_num reference is
        ambiguous or unresolvable — a hard error, never a silent best-guess.

    Also emits a `verify_recorded_result` event per resolved check via
    `runner._emit`. This runs OUTSIDE any single step's own per-step
    context (the run has already finished) — `_emit` is called directly as
    a plain method on the runner rather than threaded through the
    step-dispatch call stack; the event's own `step_num` field identifies
    which queued check it reports on, keeping it consistent with every
    other event in this codebase's `_emit`-based convention.
    """
    pending = list(getattr(runner, "_pending_recorded_verifies", None) or [])
    if not pending:
        return []

    by_step, label_to_steps = _parse_events(Path(events_path))
    filtered_index_path = getattr(recording_artifacts, "filtered_index_path", None)
    filtered_dir = getattr(recording_artifacts, "filtered_dir", None)
    frames = _load_filtered_index(filtered_index_path)

    results: List[Dict[str, Any]] = []
    for pv in pending:
        prompt = pv["prompt"]
        between = pv["between"]
        step_num = pv["step_num"]

        ts_a = _resolve_endpoint(between[0], by_step, label_to_steps)
        ts_b = _resolve_endpoint(between[1], by_step, label_to_steps)
        lo, hi = (ts_a, ts_b) if ts_a <= ts_b else (ts_b, ts_a)

        candidates = [f for f in frames if lo <= f["ts"] <= hi]

        passed = False
        frames_checked = 0
        if candidates and filtered_dir is not None:
            from PIL import Image

            for frame_rec in candidates:
                frame_path = Path(filtered_dir) / frame_rec["frame"]
                frames_checked += 1
                try:
                    with Image.open(frame_path) as img:
                        img.load()
                        verdict = runner.vlm.verify(img, prompt)
                except Exception:
                    verdict = False
                if verdict:
                    # First "yes" short-circuits — benefits automatically
                    # from the VLM SQLite cache (automation/transport/vlm_cache.py).
                    passed = True
                    break
            # Empty window (no candidate frames at all) is handled below by
            # the `frames_checked == 0` fall-through — also a FAIL, not a
            # silent skip.

        runner._emit(
            "verify_recorded_result",
            step_num=step_num,
            prompt=prompt[:200] if isinstance(prompt, str) else prompt,
            passed=passed,
            frames_checked=frames_checked,
            window_start=lo,
            window_end=hi,
        )
        results.append({
            "step_num": step_num,
            "prompt": prompt,
            "passed": passed,
            "frames_checked": frames_checked,
        })

    return results
