import json
import re
from datetime import datetime
from pathlib import Path
from typing import TextIO

from ..config import ProjectConfig
from ..state import State, TestStatus


def generate_report(state: State, config: ProjectConfig) -> None:
    report_path = config.results_dir / "REPORT.md"

    with open(report_path, "w") as f:
        _write_header(f, state, config)
        _write_summary(f, state)
        _write_no_gpu_results(f, state, config)
        _write_gpu_results(f, state, config)
        if config.report.critical_tests:
            _write_critical_tests(f, state, config)
        _write_gpu_failure_analysis(f, state, config)
        _write_footer(f, state)

    print(f"[mOSdat] Report generated: {report_path}")


def _write_header(f: TextIO, state: State, config: ProjectConfig) -> None:
    f.write(f"# {config.report.title}\n\n")
    f.write(f"**App**: {config.app.name}\n")
    f.write(f"**Version**: {state.version}\n")
    f.write(f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"**Results Directory**: `{config.results_dir}`\n\n")


def _write_summary(f: TextIO, state: State) -> None:
    passed = sum(1 for r in state.results.values() if r.status == TestStatus.PASSED)
    failed = sum(1 for r in state.results.values() if r.status == TestStatus.FAILED)
    skipped = sum(1 for r in state.results.values() if r.status == TestStatus.SKIPPED)
    total = len(state.results)

    f.write("## Summary\n\n")
    f.write("| Metric | Count |\n")
    f.write("|--------|-------|\n")
    f.write(f"| Total Tests | {total} |\n")
    f.write(f"| Passed | {passed} |\n")
    f.write(f"| Failed | {failed} |\n")
    f.write(f"| Skipped | {skipped} |\n\n")

    if failed == 0:
        f.write("**Status: ALL TESTS PASSED**\n\n")
    else:
        f.write(f"**Status: {failed} TESTS FAILED**\n\n")


def _write_no_gpu_results(f: TextIO, state: State, config: ProjectConfig) -> None:
    f.write("## Without GPU Results\n\n")
    f.write("| OS | Package | Status | Exit Code |\n")
    f.write("|----|---------|--------|-----------|\n")

    for vm in config.vms:
        for pkg in vm.packages:
            key = f"{vm.name}-{pkg.format}-no-gpu"
            result = state.results.get(key)
            if result:
                status_emoji = _status_emoji(result.status)
                exit_code = result.exit_code if result.exit_code is not None else "N/A"
                f.write(f"| {vm.name} | {pkg.format} | {status_emoji} {result.status.value} | {exit_code} |\n")
            else:
                f.write(f"| {vm.name} | {pkg.format} | - not run | - |\n")

    f.write("\n")


def _write_gpu_results(f: TextIO, state: State, config: ProjectConfig) -> None:
    f.write("## GPU Results\n\n")
    f.write("| OS | Package | Status | Exit Code |\n")
    f.write("|----|---------|--------|-----------|\n")

    for vm in config.vms:
        for pkg in vm.packages:
            key = f"{vm.name}-{pkg.format}-gpu"
            result = state.results.get(key)
            if result:
                status_emoji = _status_emoji(result.status)
                exit_code = result.exit_code if result.exit_code is not None else "N/A"
                f.write(f"| {vm.name} | {pkg.format} | {status_emoji} {result.status.value} | {exit_code} |\n")
            else:
                f.write(f"| {vm.name} | {pkg.format} | - not run | - |\n")

    f.write("\n")


def _parse_test_from_log(log_file: str, test_name: str) -> str:
    log_path = Path(log_file)
    if not log_path.exists():
        return "NOT_RUN"

    content = log_path.read_text()
    pattern = rf"RESULT:{re.escape(test_name)}:(PASS|FAIL|SKIP)(?::([A-Z_]+))?(?::(\d+))?"
    match = re.search(pattern, content)
    if match:
        result = match.group(1)
        detail = match.group(2) or ""
        if result == "FAIL" and detail:
            return f"FAIL ({detail})"
        return result
    return "NOT_FOUND"


def _write_critical_tests(f: TextIO, state: State, config: ProjectConfig) -> None:
    for test_name in config.report.critical_tests:
        f.write(f"## Critical Test: {test_name}\n\n")
        if config.report.critical_description:
            f.write(f"{config.report.critical_description}\n\n")

        all_passed = True

        f.write("| OS | Package | GPU Config | Result |\n")
        f.write("|----|---------|------------|--------|\n")

        for vm in config.vms:
            for pkg in vm.packages:
                for gpu in [False, True]:
                    gpu_label = "gpu" if gpu else "no-gpu"
                    key = f"{vm.name}-{pkg.format}-{gpu_label}"
                    result = state.results.get(key)

                    if result and result.log_file:
                        test_result = _parse_test_from_log(result.log_file, test_name)
                        if test_result == "PASS":
                            f.write(f"| {vm.name} | {pkg.format} | {gpu_label} | PASS |\n")
                        elif "FAIL" in test_result:
                            f.write(f"| {vm.name} | {pkg.format} | {gpu_label} | **{test_result}** |\n")
                            all_passed = False
                        else:
                            f.write(f"| {vm.name} | {pkg.format} | {gpu_label} | {test_result} |\n")
                    else:
                        f.write(f"| {vm.name} | {pkg.format} | {gpu_label} | - |\n")

        f.write("\n")

        if all_passed:
            f.write(f"**{test_name} validated across all tested configurations.**\n\n")
        else:
            f.write(f"**WARNING: {test_name} FAILED on some configurations. Review logs.**\n\n")


def _write_gpu_failure_analysis(f: TextIO, state: State, config: ProjectConfig) -> None:
    # Find GPU test scenarios from config
    gpu_tests = [t for t in config.tests if t.gpu]
    if not gpu_tests:
        return

    gpu_test_names = [t.name for t in gpu_tests]

    f.write("## GPU Test Failure Analysis\n\n")
    f.write("GPU tests may fail for reasons unrelated to the application:\n\n")

    header = "| OS | Package | " + " | ".join(gpu_test_names) + " | Notes |\n"
    separator = "|----|---------|" + "|".join("-" * (len(n) + 2) for n in gpu_test_names) + "|-------|\n"
    f.write(header)
    f.write(separator)

    for vm in config.vms:
        for pkg in vm.packages:
            key = f"{vm.name}-{pkg.format}-gpu"
            result = state.results.get(key)

            if result and result.log_file:
                test_results = []
                notes = []
                for t_name in gpu_test_names:
                    t_result = _parse_test_from_log(result.log_file, t_name)
                    test_results.append(t_result)

                    # Check known issues
                    for issue_key, issue_desc in config.report.known_issues.items():
                        parts = issue_key.split("+")
                        if len(parts) == 2:
                            pkg_match, test_match = parts
                            if pkg.format == pkg_match and test_match in t_name and "FAIL" in t_result:
                                notes.append(issue_desc)

                notes_str = ", ".join(notes) if notes else "-"
                results_str = " | ".join(test_results)
                f.write(f"| {vm.name} | {pkg.format} | {results_str} | {notes_str} |\n")
            else:
                dashes = " | ".join("-" for _ in gpu_test_names)
                f.write(f"| {vm.name} | {pkg.format} | {dashes} | not run |\n")

    f.write("\n")


def _write_footer(f: TextIO, state: State) -> None:
    f.write("---\n\n")
    f.write(f"*Report generated by mOSdat at {datetime.now().isoformat()}*\n")


def _status_emoji(status: TestStatus) -> str:
    return {
        TestStatus.PASSED: "",
        TestStatus.FAILED: "",
        TestStatus.SKIPPED: "",
        TestStatus.PENDING: "",
        TestStatus.RUNNING: "",
    }.get(status, "")


# ---------------------------------------------------------------------------
# B1: HTML functional run report
# ---------------------------------------------------------------------------

def generate_html_report(run_dir: Path) -> Path:
    """Generate a self-contained HTML report for a functional run.

    Walks run_dir for events.jsonl and screenshot files, groups events by
    step_num, and renders a single HTML file with per-step expandable blocks,
    VLM I/O, click coords, retry attempts, and filter controls.

    Args:
        run_dir: Directory containing events.jsonl and screenshot .png files.

    Returns:
        Path to the written report.html.
    """
    events_path = run_dir / "events.jsonl"
    events = _load_events(events_path)
    screenshots = _collect_screenshots(run_dir)
    steps = _group_by_step(events)
    meta = _extract_meta(events, run_dir)
    html = _render_html(meta, steps, screenshots, run_dir, events_path)
    out = run_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    return out


def _load_events(path: Path) -> list:
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


def _collect_screenshots(run_dir: Path) -> set:
    """Return set of screenshot filenames (name only, no dir) present on disk."""
    return {p.name for p in run_dir.glob("*.png")}


def _group_by_step(events: list) -> dict:
    """Group events by step_num. Returns ordered dict step_num -> list of events."""
    steps: dict = {}
    for ev in events:
        sn = ev.get("step_num")
        if sn is None:
            continue
        key = str(sn)
        if key not in steps:
            steps[key] = []
        steps[key].append(ev)
    # Sort numerically where possible (handles "1.1", "2", etc.)
    def _sort_key(k):
        parts = str(k).split(".")
        return tuple(int(p) if p.isdigit() else p for p in parts)
    return dict(sorted(steps.items(), key=lambda x: _sort_key(x[0])))


def _extract_meta(events: list, run_dir: Path) -> dict:
    """Pull top-level run metadata from events."""
    start_ts = None
    end_ts = None
    scenario_name = run_dir.name  # fallback: use dir name

    step_starts = {}
    step_ends = {}
    retried_steps = set()

    for ev in events:
        ts = ev.get("ts", "")
        etype = ev.get("event", "")
        sn = ev.get("step_num")

        if start_ts is None and ts:
            start_ts = ts
        if ts:
            end_ts = ts

        if etype == "step_start" and sn is not None:
            step_starts[str(sn)] = ev
        if etype == "step_end" and sn is not None:
            step_ends[str(sn)] = ev
        if etype == "retry" and sn is not None:
            retried_steps.add(str(sn))

    total = len(step_starts)
    passed = sum(1 for e in step_ends.values() if e.get("status") == "ok")
    failed = sum(1 for e in step_ends.values() if e.get("status") == "failed")
    retried = len(retried_steps)

    # Determine overall status
    if failed > 0:
        overall = "FAIL"
    elif total > 0 and passed == total:
        overall = "PASS"
    else:
        overall = "PARTIAL"

    # Duration
    duration_str = ""
    if start_ts and end_ts:
        try:
            t0 = datetime.fromisoformat(start_ts)
            t1 = datetime.fromisoformat(end_ts)
            secs = (t1 - t0).total_seconds()
            duration_str = f"{secs:.1f}s"
        except Exception:
            pass

    # Git commit
    commit = _get_git_commit(run_dir)

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
        "commit": commit,
    }


def _get_git_commit(run_dir: Path) -> str:
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


def _esc(text: str) -> str:
    """HTML-escape a string."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _render_step_block(step_key: str, step_events: list, screenshots: set) -> str:
    """Render the HTML block for one step."""
    start_ev: dict = next((e for e in step_events if e.get("event") == "step_start"), {})
    end_ev: dict = next((e for e in step_events if e.get("event") == "step_end"), {})

    label = _esc(start_ev.get("label", step_key))
    kind = _esc(start_ev.get("kind", ""))
    status = end_ev.get("status", "unknown")
    attempts = end_ev.get("attempts", 1)
    duration_ms = end_ev.get("duration_ms")
    duration_str = f"{duration_ms / 1000:.1f}s" if duration_ms is not None else ""

    # Status styling
    if status == "ok":
        border_class = "step-ok"
        icon = "&#x2713;"  # checkmark
        icon_class = "icon-ok"
        status_label = "ok"
    elif status == "failed":
        border_class = "step-fail"
        icon = "&#x2717;"  # cross
        icon_class = "icon-fail"
        status_label = "failed"
    elif status == "skipped":
        border_class = "step-skip"
        icon = "&#x21B7;"  # skip arrow
        icon_class = "icon-skip"
        status_label = "skipped"
    else:
        border_class = "step-unknown"
        icon = "&#x25CC;"  # dotted circle
        icon_class = "icon-unknown"
        status_label = status

    retry_badge = ""
    has_retries = any(e.get("event") == "retry" for e in step_events)
    if has_retries or (attempts and attempts > 1):
        retry_badge = f' <span class="retry-badge">&#x21B7; {attempts} attempt{"s" if attempts != 1 else ""}</span>'

    # Build sub-events HTML
    sub_html = _render_step_events(step_events, screenshots)

    # Popup sweep note
    popup_ev = next((e for e in step_events if e.get("event") == "popup_sweep"), None)
    popup_note = ""
    if popup_ev and popup_ev.get("dismissed", 0) > 0:
        n = popup_ev["dismissed"]
        popup_note = f'<div class="popup-note">&#x1F4AC; Popup sweep dismissed {n} dialog{"s" if n != 1 else ""}</div>'

    block_id = f"step-{step_key.replace('.', '-')}"

    return f"""
<div class="step-block {border_class}" id="{block_id}">
  <div class="step-header" onclick="toggleStep('{block_id}')">
    <span class="{icon_class}">{icon}</span>
    <span class="step-num">Step {_esc(step_key)}</span>
    <span class="step-kind">{kind}</span>
    <span class="step-label">{label}</span>
    <span class="step-meta">
      {_esc(status_label)}{" " + duration_str if duration_str else ""}{retry_badge}
    </span>
    <span class="toggle-arrow">&#x25BC;</span>
  </div>
  {popup_note}
  <div class="step-body" id="{block_id}-body">
    {sub_html}
  </div>
</div>"""


def _render_step_events(step_events: list, screenshots: set) -> str:
    """Render the expandable body for a step: VLM calls, clicks, screenshots."""
    parts = []
    for ev in step_events:
        etype = ev.get("event", "")
        if etype in ("step_start", "step_end", "popup_sweep"):
            continue  # rendered in header

        if etype == "vlm_localize":
            target = _esc(ev.get("target", ""))
            x = ev.get("x", "?")
            y = ev.get("y", "?")
            latency = ev.get("latency_ms", "?")
            attempt = ev.get("attempt", 1)
            sn = ev.get("step_num", "")
            thumb = _find_screenshot(f"step{sn}_localize", screenshots)
            thumb_html = _img_or_placeholder(thumb, "localize screenshot")
            parts.append(f"""
<div class="event-row">
  <div class="event-thumb">{thumb_html}</div>
  <div class="event-detail">
    <div class="event-type vlm-tag">VLM localize</div>
    <div class="event-field">target: <span class="mono">{target}</span></div>
    <div class="event-field">coords: <span class="mono">x={_esc(str(x))} y={_esc(str(y))}</span> &nbsp; latency: {_esc(str(latency))}ms &nbsp; attempt: {_esc(str(attempt))}</div>
  </div>
</div>""")

        elif etype == "click":
            x = ev.get("x", "?")
            y = ev.get("y", "?")
            sn = ev.get("step_num", "")
            thumb = _find_screenshot(f"step{sn}_click", screenshots)
            thumb_html = _img_or_placeholder(thumb, "click overlay")
            parts.append(f"""
<div class="event-row">
  <div class="event-thumb">{thumb_html}</div>
  <div class="event-detail">
    <div class="event-type click-tag">click</div>
    <div class="event-field">coords: <span class="mono">({_esc(str(x))}, {_esc(str(y))})</span></div>
  </div>
</div>""")

        elif etype == "vlm_verify":
            kind = ev.get("kind", "verify")
            question = _esc(ev.get("question", ""))
            answer = _esc(ev.get("answer", ""))
            latency = ev.get("latency_ms", "?")
            attempt = ev.get("attempt", 1)
            sn = ev.get("step_num", "")
            answer_class = "answer-yes" if ev.get("answer") == "yes" else "answer-no"
            # For verify kind, try to find verified screenshot
            thumb = None
            if kind == "verify":
                thumb = _find_screenshot(f"step{sn}_verified", screenshots)
                if not thumb:
                    thumb = _find_screenshot(f"step{sn}_verify_poll", screenshots)
            thumb_html = _img_or_placeholder(thumb, "verify screenshot") if thumb else ""
            parts.append(f"""
<div class="event-row">
  {"<div class='event-thumb'>" + thumb_html + "</div>" if thumb_html else ""}
  <div class="event-detail">
    <div class="event-type verify-tag">VLM verify <span class="kind-badge">{_esc(kind)}</span></div>
    <div class="event-field">Q: <span class="mono">{question}</span></div>
    <div class="event-field">A: <span class="mono {answer_class}">{answer}</span> &nbsp; latency: {_esc(str(latency))}ms &nbsp; attempt: {_esc(str(attempt))}</div>
  </div>
</div>""")

        elif etype == "verify_split":
            question = _esc(ev.get("question", ""))
            responses = ev.get("responses", [])
            resp_html = "".join(
                f'<div class="split-sample">sample {i+1}: <span class="mono">{_esc(r)}</span></div>'
                for i, r in enumerate(responses)
            )
            parts.append(f"""
<div class="event-row verify-split-row">
  <div class="event-detail">
    <div class="event-type split-tag">verify_split (quorum disagreement)</div>
    <div class="event-field">Q: <span class="mono">{question}</span></div>
    {resp_html}
  </div>
</div>""")

        elif etype == "type":
            text = _esc(ev.get("text_redacted", ""))
            parts.append(f"""
<div class="event-row">
  <div class="event-detail">
    <div class="event-type type-tag">type</div>
    <div class="event-field"><span class="mono">{text}</span></div>
  </div>
</div>""")

        elif etype == "key":
            key = _esc(ev.get("key", ""))
            parts.append(f"""
<div class="event-row">
  <div class="event-detail">
    <div class="event-type key-tag">key</div>
    <div class="event-field"><span class="mono">{key}</span></div>
  </div>
</div>""")

        elif etype == "shell":
            cmd = _esc(ev.get("cmd_truncated", ""))
            parts.append(f"""
<div class="event-row">
  <div class="event-detail">
    <div class="event-type shell-tag">shell</div>
    <div class="event-field"><span class="mono">{cmd}</span></div>
  </div>
</div>""")

        elif etype == "launch":
            app = _esc(ev.get("app", ""))
            parts.append(f"""
<div class="event-row">
  <div class="event-detail">
    <div class="event-type launch-tag">launch</div>
    <div class="event-field"><span class="mono">{app}</span></div>
  </div>
</div>""")

        elif etype == "launch_verify":
            process_ok = ev.get("process_present", False)
            window_ok = ev.get("window_present", False)
            elapsed = ev.get("elapsed_ms", "?")
            app = _esc(ev.get("app", ""))
            ok_class = "answer-yes" if (process_ok and window_ok) else "answer-no"
            parts.append(f"""
<div class="event-row">
  <div class="event-detail">
    <div class="event-type launch-tag">launch_verify</div>
    <div class="event-field">{app}: <span class="mono {ok_class}">process={process_ok} window={window_ok}</span> &nbsp; {_esc(str(elapsed))}ms</div>
  </div>
</div>""")

        elif etype == "retry":
            reason = _esc(ev.get("reason", ""))
            attempt = ev.get("attempt", "?")
            parts.append(f"""
<div class="event-row retry-row">
  <div class="event-detail">
    <div class="event-type retry-tag">retry after attempt {_esc(str(attempt))}</div>
    <div class="event-field">reason: <span class="mono">{reason}</span></div>
  </div>
</div>""")

        elif etype == "vm_health_failed":
            parts.append("""
<div class="event-row">
  <div class="event-detail">
    <div class="event-type fail-tag">vm_health_failed</div>
    <div class="event-field">VM was frozen or unresponsive</div>
  </div>
</div>""")

    return "\n".join(parts) if parts else '<div class="no-events">No sub-events recorded</div>'


def _find_screenshot(pattern: str, screenshots: set) -> str | None:
    """Find the first screenshot filename matching a step pattern."""
    # Screenshots are named <HHMMSS>_<label>.png
    for name in sorted(screenshots):
        # Strip timestamp prefix (6 digits) and underscore
        stem = name[7:] if len(name) > 7 and name[:6].isdigit() and name[6] == "_" else name
        stem_no_ext = stem[:-4] if stem.endswith(".png") else stem
        if stem_no_ext == pattern or stem_no_ext.startswith(pattern):
            return name
    return None


def _img_or_placeholder(filename: str | None, alt: str) -> str:
    if not filename:
        return f'<div class="img-missing">{_esc(alt)}<br><small>screenshot missing</small></div>'
    safe = _esc(filename)
    return f'<a href="{safe}" target="_blank"><img src="{safe}" alt="{_esc(alt)}" class="thumb"></a>'


def _render_html(meta: dict, steps: dict, screenshots: set, run_dir: Path, events_path: Path) -> str:
    overall = meta["overall"]
    if overall == "PASS":
        overall_class = "status-pass"
        overall_text = "PASS"
    elif overall == "FAIL":
        overall_class = "status-fail"
        overall_text = "FAIL"
    else:
        overall_class = "status-partial"
        overall_text = "PARTIAL"

    steps_html = ""
    if not steps:
        steps_html = '<div class="no-events">No events captured in events.jsonl (file missing or empty).</div>'
    else:
        for skey, sevents in steps.items():
            steps_html += _render_step_block(skey, sevents, screenshots)

    commit_html = ""
    if meta.get("commit"):
        commit_html = f'<span class="footer-item">commit: <span class="mono">{_esc(meta["commit"])}</span></span>'

    events_rel = events_path.name
    scenario = _esc(meta["scenario_name"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mosdat run — {scenario}</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: #1a1a1a;
  color: #e0e0e0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 14px;
  line-height: 1.5;
}}
a {{ color: #60a5fa; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.mono {{ font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", Consolas, monospace; font-size: 0.88em; }}

/* ---- Header ---- */
.run-header {{
  background: #111;
  border-bottom: 1px solid #333;
  padding: 20px 24px 16px;
}}
.run-title {{
  font-size: 1.3em;
  font-weight: 600;
  margin-bottom: 6px;
}}
.run-meta {{
  color: #aaa;
  font-size: 0.9em;
  margin-bottom: 10px;
}}
.run-counts {{
  font-size: 0.9em;
  color: #bbb;
}}
.status-badge {{
  display: inline-block;
  padding: 2px 10px;
  border-radius: 4px;
  font-weight: 700;
  font-size: 1em;
  letter-spacing: 0.05em;
  margin-left: 8px;
}}
.status-pass {{ background: #166534; color: #4ade80; }}
.status-fail {{ background: #7f1d1d; color: #f87171; }}
.status-partial {{ background: #713f12; color: #fbbf24; }}

/* ---- Toolbar ---- */
.toolbar {{
  background: #1f1f1f;
  border-bottom: 1px solid #2a2a2a;
  padding: 10px 24px;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  align-items: center;
}}
.toolbar label {{
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  color: #ccc;
  font-size: 0.9em;
}}
.toolbar input[type=checkbox] {{ accent-color: #60a5fa; }}
.toolbar button {{
  background: #2d2d2d;
  border: 1px solid #444;
  color: #ddd;
  padding: 4px 14px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.9em;
}}
.toolbar button:hover {{ background: #3a3a3a; }}

/* ---- Steps ---- */
.steps-container {{
  padding: 16px 24px;
  max-width: 1200px;
  margin: 0 auto;
}}
.step-block {{
  border-left: 4px solid #333;
  background: #222;
  border-radius: 6px;
  margin-bottom: 10px;
  overflow: hidden;
}}
.step-ok    {{ border-left-color: #4ade80; }}
.step-fail  {{ border-left-color: #f87171; }}
.step-skip  {{ border-left-color: #60a5fa; }}
.step-unknown {{ border-left-color: #fbbf24; }}

.step-header {{
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  flex-wrap: wrap;
}}
.step-header:hover {{ background: #2a2a2a; }}
.icon-ok    {{ color: #4ade80; font-weight: 700; font-size: 1.1em; }}
.icon-fail  {{ color: #f87171; font-weight: 700; font-size: 1.1em; }}
.icon-skip  {{ color: #60a5fa; font-weight: 700; font-size: 1.1em; }}
.icon-unknown {{ color: #fbbf24; font-weight: 700; font-size: 1.1em; }}

.step-num  {{ font-weight: 600; min-width: 60px; }}
.step-kind {{
  background: #333;
  color: #aaa;
  font-size: 0.78em;
  padding: 1px 7px;
  border-radius: 3px;
  font-family: monospace;
}}
.step-label {{ flex: 1; color: #ddd; }}
.step-meta  {{ color: #888; font-size: 0.88em; white-space: nowrap; }}
.toggle-arrow {{ color: #555; font-size: 0.8em; }}
.retry-badge {{
  background: #422006;
  color: #fbbf24;
  font-size: 0.78em;
  padding: 1px 7px;
  border-radius: 3px;
  margin-left: 6px;
}}

/* ---- Step body ---- */
.step-body {{
  padding: 0 14px 12px;
  display: none;
}}
.step-body.open {{ display: block; }}

.event-row {{
  display: flex;
  gap: 12px;
  margin-top: 10px;
  align-items: flex-start;
}}
.event-thumb {{ flex-shrink: 0; }}
.thumb {{
  width: 200px;
  height: auto;
  border-radius: 4px;
  border: 1px solid #333;
  display: block;
}}
.img-missing {{
  width: 200px;
  background: #2a2a2a;
  border: 1px dashed #444;
  border-radius: 4px;
  color: #666;
  font-size: 0.8em;
  text-align: center;
  padding: 16px 8px;
}}
.event-detail {{ flex: 1; min-width: 0; }}
.event-type {{
  font-size: 0.78em;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 3px;
  display: inline-block;
  margin-bottom: 5px;
}}
.vlm-tag     {{ background: #1e3a5f; color: #93c5fd; }}
.verify-tag  {{ background: #1a3a2a; color: #6ee7b7; }}
.click-tag   {{ background: #2d1f4a; color: #c4b5fd; }}
.type-tag    {{ background: #1f2d1f; color: #86efac; }}
.key-tag     {{ background: #2d2014; color: #fcd34d; }}
.shell-tag   {{ background: #1a1a2e; color: #a5b4fc; }}
.launch-tag  {{ background: #1a2a2d; color: #67e8f9; }}
.retry-tag   {{ background: #3d2100; color: #fbbf24; }}
.split-tag   {{ background: #3d1a1a; color: #f87171; }}
.fail-tag    {{ background: #3d0000; color: #f87171; }}
.kind-badge {{
  background: rgba(255,255,255,0.1);
  font-size: 0.85em;
  padding: 0 5px;
  border-radius: 2px;
  font-weight: 400;
  margin-left: 4px;
}}
.event-field {{ font-size: 0.88em; color: #bbb; margin-top: 3px; }}
.answer-yes {{ color: #4ade80; }}
.answer-no  {{ color: #f87171; }}
.split-sample {{ margin-top: 4px; font-size: 0.85em; color: #aaa; }}
.retry-row {{ opacity: 0.85; }}
.popup-note {{
  padding: 5px 14px;
  color: #60a5fa;
  font-size: 0.85em;
  border-top: 1px solid #2a2a2a;
}}
.no-events {{ color: #555; font-style: italic; padding: 8px 0; }}
.verify-split-row {{ display: block; }}

/* ---- Footer ---- */
.run-footer {{
  border-top: 1px solid #2a2a2a;
  padding: 12px 24px;
  color: #555;
  font-size: 0.82em;
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
}}
.footer-item {{ display: flex; align-items: center; gap: 4px; }}

/* ---- Filter: hide passing steps ---- */
body.filter-failed .step-block:not(.step-fail) {{ display: none; }}
</style>
</head>
<body>

<div class="run-header">
  <div class="run-title">
    mosdat functional run &mdash; {scenario}
    <span class="status-badge {overall_class}">{_esc(overall_text)}</span>
  </div>
  <div class="run-meta">
    {_esc(meta.get("start_ts", ""))} &rarr; {_esc(meta.get("end_ts", ""))}
    {(" &middot; " + _esc(meta["duration"])) if meta.get("duration") else ""}
  </div>
  <div class="run-counts">
    Steps: {meta["total"]} &nbsp;&middot;&nbsp;
    Pass: <span style="color:#4ade80">{meta["passed"]}</span> &nbsp;&middot;&nbsp;
    Fail: <span style="color:#f87171">{meta["failed"]}</span> &nbsp;&middot;&nbsp;
    Retried: <span style="color:#fbbf24">{meta["retried"]}</span>
  </div>
</div>

<div class="toolbar">
  <label>
    <input type="checkbox" id="filter-failed" onchange="toggleFailedFilter(this.checked)">
    Show only failed steps
  </label>
  <button onclick="expandAll()">Expand all</button>
  <button onclick="collapseAll()">Collapse all</button>
</div>

<div class="steps-container">
{steps_html}
</div>

<div class="run-footer">
  <span class="footer-item"><a href="{_esc(events_rel)}">events.jsonl</a></span>
  {commit_html}
  <span class="footer-item">generated by mOSdat at {_esc(datetime.now().isoformat())}</span>
</div>

<script>
function toggleStep(id) {{
  var body = document.getElementById(id + '-body');
  if (body) body.classList.toggle('open');
  var arrow = document.querySelector('#' + id + ' .toggle-arrow');
  if (arrow) arrow.innerHTML = body.classList.contains('open') ? '&#x25B2;' : '&#x25BC;';
}}
function expandAll() {{
  document.querySelectorAll('.step-body').forEach(function(b) {{
    b.classList.add('open');
  }});
  document.querySelectorAll('.toggle-arrow').forEach(function(a) {{
    a.innerHTML = '&#x25B2;';
  }});
}}
function collapseAll() {{
  document.querySelectorAll('.step-body').forEach(function(b) {{
    b.classList.remove('open');
  }});
  document.querySelectorAll('.toggle-arrow').forEach(function(a) {{
    a.innerHTML = '&#x25BC;';
  }});
}}
function toggleFailedFilter(on) {{
  document.body.classList.toggle('filter-failed', on);
}}
// Auto-expand failed steps on load
window.addEventListener('DOMContentLoaded', function() {{
  document.querySelectorAll('.step-fail').forEach(function(block) {{
    var id = block.id;
    var body = document.getElementById(id + '-body');
    if (body) body.classList.add('open');
    var arrow = block.querySelector('.toggle-arrow');
    if (arrow) arrow.innerHTML = '&#x25B2;';
  }});
}});
</script>

</body>
</html>"""
