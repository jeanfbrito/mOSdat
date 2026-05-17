"""HTML functional-run report renderer.

Extracted from report.py to keep each file ≤500 LOC.
Internal use only — public API lives in report.py.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# HTML utilities
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """HTML-escape a string."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _find_screenshot(pattern: str, screenshots: set) -> str | None:
    """Find the first screenshot filename matching a step pattern."""
    for name in sorted(screenshots):
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


# ---------------------------------------------------------------------------
# Step event renderers
# ---------------------------------------------------------------------------

def _render_step_events(step_events: list, screenshots: set) -> str:
    """Render the expandable body for a step: VLM calls, clicks, screenshots."""
    parts = []
    for ev in step_events:
        etype = ev.get("event", "")
        if etype in ("step_start", "step_end", "popup_sweep"):
            continue

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

        elif etype == "verify_step":
            # I14: rich verify event with full prompt, raw VLM response, cache indicator
            prompt = _esc(ev.get("prompt_text", ""))
            raw = _esc(ev.get("raw_vlm_response", ""))
            verdict = ev.get("verdict", "no")
            cache_hit = ev.get("cache_hit", False)
            latency = ev.get("latency_ms", "?")
            attempt = ev.get("attempt", 1)
            verdict_class = "answer-yes" if verdict == "yes" else "answer-no"
            cache_badge = ' <span class="cache-badge">cache hit</span>' if cache_hit else ""
            parts.append(f"""
<div class="event-row">
  <div class="event-detail">
    <div class="event-type verify-tag">verify (I14){cache_badge}</div>
    <div class="event-field">verdict: <span class="mono {verdict_class}">{_esc(verdict)}</span> &nbsp; latency: {_esc(str(latency))}ms &nbsp; attempt: {_esc(str(attempt))}</div>
    <details class="io-details">
      <summary class="mono">prompt_text</summary>
      <pre class="io-pre">{prompt}</pre>
    </details>
    {"" if not raw else f'<details class="io-details"><summary class="mono">raw_vlm_response</summary><pre class="io-pre">{raw}</pre></details>'}
  </div>
</div>""")

        elif etype == "accept_any_step":
            # I14: rich accept_any event with per-prompt verdicts and raw responses
            verdict = ev.get("verdict", "no")
            verdict_class = "answer-yes" if verdict == "yes" else "answer-no"
            latency = ev.get("latency_ms", "?")
            attempt = ev.get("attempt", 1)
            per_prompt = ev.get("per_prompt_verdicts", [])
            prompt_rows = ""
            for pi, pv in enumerate(per_prompt):
                p_text = _esc(pv.get("prompt", ""))
                p_verdict = pv.get("verdict", "no")
                p_raw = _esc(pv.get("raw_vlm_response", ""))
                p_class = "answer-yes" if p_verdict == "yes" else "answer-no"
                prompt_rows += f"""<div class="accept-any-row">
  <span class="mono {p_class}">[{pi}] {_esc(p_verdict)}</span>: <span class="mono">{p_text}</span>
  {"" if not p_raw else f'<details class="io-details"><summary class="mono">raw</summary><pre class="io-pre">{p_raw}</pre></details>'}
</div>"""
            parts.append(f"""
<div class="event-row">
  <div class="event-detail">
    <div class="event-type verify-tag">accept_any (I14)</div>
    <div class="event-field">verdict: <span class="mono {verdict_class}">{_esc(verdict)}</span> &nbsp; latency: {_esc(str(latency))}ms &nbsp; attempt: {_esc(str(attempt))}</div>
    <div class="accept-any-prompts">{prompt_rows}</div>
  </div>
</div>""")

        elif etype == "config_snapshot":
            # I14: config.json snapshot captured after a shell step
            content = _esc(ev.get("content", ""))
            sn = ev.get("step_num", "")
            parts.append(f"""
<div class="event-row">
  <div class="event-detail">
    <div class="event-type config-tag">config_snapshot (I14)</div>
    <details class="io-details">
      <summary class="mono">config.json (step {_esc(str(sn))})</summary>
      <pre class="io-pre">{content}</pre>
    </details>
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

        elif etype == "shell_step":
            # I14: rich shell event with full command, stdout/stderr, exit code, duration
            cmd_sent = _esc(ev.get("command_sent", ""))
            stdout = _esc(ev.get("stdout_tail", ""))
            stderr = _esc(ev.get("stderr_tail", ""))
            exit_code = ev.get("exit_code", "?")
            duration = ev.get("duration_ms", "?")
            exit_class = "answer-yes" if exit_code == 0 else "answer-no"
            blk_id = f"shell-{ev.get('step_num', '')}-{id(ev)}"
            parts.append(f"""
<div class="event-row">
  <div class="event-detail">
    <div class="event-type shell-tag">shell (I14)</div>
    <div class="event-field">exit: <span class="mono {exit_class}">{_esc(str(exit_code))}</span> &nbsp; duration: {_esc(str(duration))}ms</div>
    <details class="io-details">
      <summary class="mono">command_sent</summary>
      <pre class="io-pre">{cmd_sent}</pre>
    </details>
    {"" if not stdout else f'<details class="io-details"><summary class="mono">stdout</summary><pre class="io-pre">{stdout}</pre></details>'}
    {"" if not stderr else f'<details class="io-details"><summary class="mono">stderr</summary><pre class="io-pre">{stderr}</pre></details>'}
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


def _render_step_block(step_key: str, step_events: list, screenshots: set) -> str:
    """Render the HTML block for one step."""
    start_ev: dict = next((e for e in step_events if e.get("event") == "step_start"), {})
    end_ev: dict = next((e for e in step_events if e.get("event") == "step_end"), {})

    label = _esc(start_ev.get("label", step_key))
    # I10: step_label is "N: human label" when label available, else bare step_key
    step_label_raw = start_ev.get("step_label")
    step_header_label = _esc(step_label_raw) if step_label_raw else ""
    kind = _esc(start_ev.get("kind", ""))
    status = end_ev.get("status", "unknown")
    attempts = end_ev.get("attempts", 1)
    duration_ms = end_ev.get("duration_ms")
    duration_str = f"{duration_ms / 1000:.1f}s" if duration_ms is not None else ""

    if status == "ok":
        border_class = "step-ok"
        icon = "&#x2713;"
        icon_class = "icon-ok"
        status_label = "ok"
    elif status == "failed":
        border_class = "step-fail"
        icon = "&#x2717;"
        icon_class = "icon-fail"
        status_label = "failed"
    elif status == "skipped":
        border_class = "step-skip"
        icon = "&#x21B7;"
        icon_class = "icon-skip"
        status_label = "skipped"
    else:
        border_class = "step-unknown"
        icon = "&#x25CC;"
        icon_class = "icon-unknown"
        status_label = status

    retry_badge = ""
    has_retries = any(e.get("event") == "retry" for e in step_events)
    if has_retries or (attempts and attempts > 1):
        retry_badge = f' <span class="retry-badge">&#x21B7; {attempts} attempt{"s" if attempts != 1 else ""}</span>'

    sub_html = _render_step_events(step_events, screenshots)

    popup_ev = next((e for e in step_events if e.get("event") == "popup_sweep"), None)
    popup_note = ""
    if popup_ev and popup_ev.get("dismissed", 0) > 0:
        n = popup_ev["dismissed"]
        popup_note = f'<div class="popup-note">&#x1F4AC; Popup sweep dismissed {n} dialog{"s" if n != 1 else ""}</div>'

    block_id = f"step-{step_key.replace('.', '-')}"

    # I10: if we have a human label, show it prominently; subscript the step number
    if step_header_label:
        step_num_display = f'<span class="step-num"><sub>#{_esc(step_key)}</sub></span>'
        step_label_display = f'<span class="step-label">{step_header_label}</span>'
    else:
        step_num_display = f'<span class="step-num">Step {_esc(step_key)}</span>'
        step_label_display = f'<span class="step-label">{label}</span>'

    return f"""
<div class="step-block {border_class}" id="{block_id}">
  <div class="step-header" onclick="toggleStep('{block_id}')">
    <span class="{icon_class}">{icon}</span>
    {step_num_display}
    <span class="step-kind">{kind}</span>
    {step_label_display}
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


# ---------------------------------------------------------------------------
# Full HTML page assembler
# ---------------------------------------------------------------------------

_CSS = """\
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #1a1a1a;
  color: #e0e0e0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 14px;
  line-height: 1.5;
}
a { color: #60a5fa; text-decoration: none; }
a:hover { text-decoration: underline; }
.mono { font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", Consolas, monospace; font-size: 0.88em; }
.run-header { background: #111; border-bottom: 1px solid #333; padding: 20px 24px 16px; }
.run-title { font-size: 1.3em; font-weight: 600; margin-bottom: 6px; }
.run-meta { color: #aaa; font-size: 0.9em; margin-bottom: 10px; }
.run-counts { font-size: 0.9em; color: #bbb; }
.status-badge { display: inline-block; padding: 2px 10px; border-radius: 4px; font-weight: 700; font-size: 1em; letter-spacing: 0.05em; margin-left: 8px; }
.status-pass { background: #166534; color: #4ade80; }
.status-fail { background: #7f1d1d; color: #f87171; }
.status-partial { background: #713f12; color: #fbbf24; }
.toolbar { background: #1f1f1f; border-bottom: 1px solid #2a2a2a; padding: 10px 24px; display: flex; gap: 16px; flex-wrap: wrap; align-items: center; }
.toolbar label { display: flex; align-items: center; gap: 6px; cursor: pointer; color: #ccc; font-size: 0.9em; }
.toolbar input[type=checkbox] { accent-color: #60a5fa; }
.toolbar button { background: #2d2d2d; border: 1px solid #444; color: #ddd; padding: 4px 14px; border-radius: 4px; cursor: pointer; font-size: 0.9em; }
.toolbar button:hover { background: #3a3a3a; }
.steps-container { padding: 16px 24px; max-width: 1200px; margin: 0 auto; }
.step-block { border-left: 4px solid #333; background: #222; border-radius: 6px; margin-bottom: 10px; overflow: hidden; }
.step-ok    { border-left-color: #4ade80; }
.step-fail  { border-left-color: #f87171; }
.step-skip  { border-left-color: #60a5fa; }
.step-unknown { border-left-color: #fbbf24; }
.step-header { display: flex; align-items: center; gap: 8px; padding: 10px 14px; cursor: pointer; user-select: none; flex-wrap: wrap; }
.step-header:hover { background: #2a2a2a; }
.icon-ok    { color: #4ade80; font-weight: 700; font-size: 1.1em; }
.icon-fail  { color: #f87171; font-weight: 700; font-size: 1.1em; }
.icon-skip  { color: #60a5fa; font-weight: 700; font-size: 1.1em; }
.icon-unknown { color: #fbbf24; font-weight: 700; font-size: 1.1em; }
.step-num  { font-weight: 600; min-width: 60px; }
.step-kind { background: #333; color: #aaa; font-size: 0.78em; padding: 1px 7px; border-radius: 3px; font-family: monospace; }
.step-label { flex: 1; color: #ddd; }
.step-meta  { color: #888; font-size: 0.88em; white-space: nowrap; }
.toggle-arrow { color: #555; font-size: 0.8em; }
.retry-badge { background: #422006; color: #fbbf24; font-size: 0.78em; padding: 1px 7px; border-radius: 3px; margin-left: 6px; }
.step-body { padding: 0 14px 12px; display: none; }
.step-body.open { display: block; }
.event-row { display: flex; gap: 12px; margin-top: 10px; align-items: flex-start; }
.event-thumb { flex-shrink: 0; }
.thumb { width: 200px; height: auto; border-radius: 4px; border: 1px solid #333; display: block; }
.img-missing { width: 200px; background: #2a2a2a; border: 1px dashed #444; border-radius: 4px; color: #666; font-size: 0.8em; text-align: center; padding: 16px 8px; }
.event-detail { flex: 1; min-width: 0; }
.event-type { font-size: 0.78em; font-weight: 600; padding: 2px 8px; border-radius: 3px; display: inline-block; margin-bottom: 5px; }
.vlm-tag     { background: #1e3a5f; color: #93c5fd; }
.verify-tag  { background: #1a3a2a; color: #6ee7b7; }
.click-tag   { background: #2d1f4a; color: #c4b5fd; }
.type-tag    { background: #1f2d1f; color: #86efac; }
.key-tag     { background: #2d2014; color: #fcd34d; }
.shell-tag   { background: #1a1a2e; color: #a5b4fc; }
.launch-tag  { background: #1a2a2d; color: #67e8f9; }
.retry-tag   { background: #3d2100; color: #fbbf24; }
.split-tag   { background: #3d1a1a; color: #f87171; }
.fail-tag    { background: #3d0000; color: #f87171; }
.config-tag  { background: #1a2a1a; color: #86efac; }
.cache-badge { background: #1e3a5f; color: #93c5fd; font-size: 0.78em; padding: 1px 7px; border-radius: 3px; margin-left: 6px; }
.io-details  { margin-top: 4px; }
.io-details summary { cursor: pointer; color: #aaa; font-size: 0.85em; padding: 2px 0; }
.io-details summary:hover { color: #ddd; }
.io-pre      { background: #111; border: 1px solid #2a2a2a; border-radius: 4px; padding: 8px 10px; margin-top: 4px; font-family: "JetBrains Mono", "Fira Code", Consolas, monospace; font-size: 0.82em; color: #c8d3f0; overflow-x: auto; white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow-y: auto; }
.accept-any-prompts { margin-top: 6px; }
.accept-any-row { font-size: 0.85em; color: #bbb; margin-top: 3px; }
.kind-badge { background: rgba(255,255,255,0.1); font-size: 0.85em; padding: 0 5px; border-radius: 2px; font-weight: 400; margin-left: 4px; }
.event-field { font-size: 0.88em; color: #bbb; margin-top: 3px; }
.answer-yes { color: #4ade80; }
.answer-no  { color: #f87171; }
.split-sample { margin-top: 4px; font-size: 0.85em; color: #aaa; }
.retry-row { opacity: 0.85; }
.popup-note { padding: 5px 14px; color: #60a5fa; font-size: 0.85em; border-top: 1px solid #2a2a2a; }
.no-events { color: #555; font-style: italic; padding: 8px 0; }
.verify-split-row { display: block; }
.run-footer { border-top: 1px solid #2a2a2a; padding: 12px 24px; color: #555; font-size: 0.82em; display: flex; gap: 20px; flex-wrap: wrap; }
.footer-item { display: flex; align-items: center; gap: 4px; }
body.filter-failed .step-block:not(.step-fail) { display: none; }"""

_JS = """\
function toggleStep(id) {
  var body = document.getElementById(id + '-body');
  if (body) body.classList.toggle('open');
  var arrow = document.querySelector('#' + id + ' .toggle-arrow');
  if (arrow) arrow.innerHTML = body.classList.contains('open') ? '&#x25B2;' : '&#x25BC;';
}
function expandAll() {
  document.querySelectorAll('.step-body').forEach(function(b) { b.classList.add('open'); });
  document.querySelectorAll('.toggle-arrow').forEach(function(a) { a.innerHTML = '&#x25B2;'; });
}
function collapseAll() {
  document.querySelectorAll('.step-body').forEach(function(b) { b.classList.remove('open'); });
  document.querySelectorAll('.toggle-arrow').forEach(function(a) { a.innerHTML = '&#x25BC;'; });
}
function toggleFailedFilter(on) { document.body.classList.toggle('filter-failed', on); }
window.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.step-fail').forEach(function(block) {
    var id = block.id;
    var body = document.getElementById(id + '-body');
    if (body) body.classList.add('open');
    var arrow = block.querySelector('.toggle-arrow');
    if (arrow) arrow.innerHTML = '&#x25B2;';
  });
});"""


def render_html(meta: dict, steps: dict, screenshots: set, run_dir: Path, events_path: Path) -> str:
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
{_CSS}
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
{_JS}
</script>

</body>
</html>"""
