"""mosdat replay — rerun a single VLM verify against a cached screenshot.

Usage:
    mosdat replay <result-dir> [--step N] [--verify "<prompt>"]
                  [--model MODEL] [--all-attempts]

Exit codes:
    0  verdict yes
    1  verdict no
    2  internal error
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional


# ── Event parsing ──────────────────────────────────────────────────────────────

def _load_events(result_dir: Path) -> list[dict]:
    """Read all JSON lines from events.jsonl in result_dir."""
    events_path = result_dir / "events.jsonl"
    if not events_path.exists():
        raise FileNotFoundError(f"events.jsonl not found in {result_dir}")
    events = []
    with events_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _available_steps(events: list[dict]) -> list[dict]:
    """Return list of {step_num, question} for every step that has a vlm_verify event."""
    seen: dict[int, str] = {}
    for ev in events:
        if ev.get("event") == "vlm_verify":
            step_num = ev.get("step_num")
            if step_num is not None and step_num not in seen:
                seen[step_num] = ev.get("question", "")
    return [{"step_num": k, "question": seen[k]} for k in sorted(seen)]


def _find_screenshots(result_dir: Path, step_num: int, all_attempts: bool) -> list[Path]:
    """Find verify_poll screenshots for the given step.

    Naming convention: <HHMMSS>_step<N>_verify_poll.png
    Returns sorted list; if not all_attempts, returns only the last one.
    """
    pattern = re.compile(rf"^\d+_step{step_num}_verify_poll\.png$")
    matches = sorted(
        p for p in result_dir.iterdir()
        if p.is_file() and pattern.match(p.name)
    )
    if not matches:
        return []
    if all_attempts:
        return matches
    return [matches[-1]]


# ── VLM call ──────────────────────────────────────────────────────────────────

def _build_vlm(model: str, base_url: str, api_key: str = "", max_tokens_floor: int = 0) -> "VLMClient":  # type: ignore[name-defined]
    from automation.vlm.client import VLMClient
    return VLMClient(base_url=base_url, model=model, verify_model=model, api_key=api_key, max_tokens_floor=max_tokens_floor)


def _call_verify(vlm, screenshot_path: Path, prompt: str) -> tuple[bool, str]:
    """Call vlm.verify with the screenshot at path, return (verdict, raw_response)."""
    from PIL import Image
    from automation.vlm.prompts import _VERIFY_PROMPT, _encode_image

    img = Image.open(screenshot_path)

    # Use the same call path as functional_verify: vlm.verify()
    # We also capture raw_response by calling the underlying API directly.
    b64 = _encode_image(img)
    resp = vlm._call_with_failover(
        "chat.completions.create",
        model=vlm.verify_model,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": f"{_VERIFY_PROMPT}{prompt}"},
        ]}],
        max_tokens=1024,
        temperature=0.0,
        timeout=180,
    )
    from automation.vlm.parsing import _extract_content
    raw = _extract_content(resp)

    # Same yes/no logic as VLMClient.verify
    parse_raw = raw
    if "</think>" in parse_raw:
        parse_raw = parse_raw[parse_raw.rfind("</think>") + len("</think>"):]
    low = parse_raw.lower().strip()
    last_yes = low.rfind("yes")
    last_no = low.rfind("no")
    if last_yes == -1 and last_no == -1:
        verdict = False
    else:
        verdict = last_yes > last_no

    return verdict, raw


# ── Main entry point ──────────────────────────────────────────────────────────

def run_replay(args) -> int:
    result_dir = Path(args.result_dir).expanduser().resolve()
    if not result_dir.is_dir():
        print(f"[replay] ERROR: result dir not found: {result_dir}", file=sys.stderr)
        return 2

    try:
        events = _load_events(result_dir)
    except FileNotFoundError as e:
        print(f"[replay] ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[replay] ERROR reading events.jsonl: {e}", file=sys.stderr)
        return 2

    # If --step omitted, list available steps and exit
    if args.step is None:
        steps = _available_steps(events)
        if not steps:
            print("[replay] No verify steps found in events.jsonl")
            return 2
        print(f"{'Step':>5}  Question (first verify)")
        print(f"{'─'*5}  {'─'*60}")
        for s in steps:
            q = s["question"].replace("\n", " ")[:80]
            print(f"{s['step_num']:>5}  {q}")
        return 0

    step_num: int = args.step
    prompt: Optional[str] = getattr(args, "verify", None)
    all_attempts: bool = getattr(args, "all_attempts", False)

    # Find screenshots
    screenshots = _find_screenshots(result_dir, step_num, all_attempts)
    if not screenshots:
        print(f"[replay] ERROR: no verify_poll screenshots found for step {step_num} in {result_dir}",
              file=sys.stderr)
        return 2

    # If no prompt given, fall back to the original question from events
    if not prompt:
        for ev in events:
            if ev.get("event") == "vlm_verify" and ev.get("step_num") == step_num:
                prompt = ev.get("question", "")
                break
    if not prompt:
        print(f"[replay] ERROR: --verify prompt required (no prior question found for step {step_num})",
              file=sys.stderr)
        return 2

    # Resolve model and endpoint
    model = getattr(args, "model", None) or "qwen3.6-35b-a3b-apex-vl"
    base_url = os.environ.get("VLM_BASE_URL", "")
    if not base_url:
        print("[replay] ERROR: VLM_BASE_URL env var not set", file=sys.stderr)
        return 2

    try:
        api_key = os.environ.get("VLM_API_KEY", "")
        vlm = _build_vlm(model, base_url, api_key)
    except Exception as e:
        print(f"[replay] ERROR building VLM client: {e}", file=sys.stderr)
        return 2

    last_verdict: Optional[bool] = None
    for screenshot_path in screenshots:
        try:
            verdict, raw = _call_verify(vlm, screenshot_path, prompt)
        except Exception as e:
            print(f"[replay] ERROR calling VLM: {e}", file=sys.stderr)
            return 2

        last_verdict = verdict
        raw_truncated = raw[:500].replace("\n", " ")
        verdict_str = "yes" if verdict else "no"
        print(f"screenshot={screenshot_path}")
        print(f"prompt={prompt}")
        print(f"verdict={verdict_str}")
        print(f"raw_response={raw_truncated}")
        if all_attempts and len(screenshots) > 1:
            print()

    if last_verdict is None:
        return 2
    return 0 if last_verdict else 1
