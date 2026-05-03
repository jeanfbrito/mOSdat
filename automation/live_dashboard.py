"""Live event-stream dashboard for mosdat functional runs.

Usage:
    mosdat live --port 8080 [--results results/]

Architecture:
    - EventWatcher: polls events.jsonl mtimes every N ms, tails new lines, detects new PNGs
    - SSEBroadcaster: fan-out to all connected SSE clients
    - DashboardHandler: HTTP handler (stdlib only) serving HTML + /stream + /png/...
"""

from __future__ import annotations

import argparse
import json
import posixpath
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from automation.dashboard_state import build_dashboard_state
from automation.live_events import EventWatcher, SSEBroadcaster

# ---------------------------------------------------------------------------
# Heartbeat log (for phase signalling in tests)
# ---------------------------------------------------------------------------
_HB_LOG = Path("/tmp/agent-dashboard-hb.log")


def _hb(msg: str) -> None:
    try:
        with _HB_LOG.open("a") as fh:
            fh.write(f"{time.time():.3f} {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Inline HTML — chat-timeline UX
# ---------------------------------------------------------------------------
_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mOSdat live</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; background: #f3f4f6; color: #111827; }

  /* ── Sticky header ── */
  #topbar {
    position: sticky; top: 0; z-index: 100;
    background: #1e293b; color: #f1f5f9;
    padding: 10px 16px;
    display: flex; align-items: center; flex-wrap: wrap; gap: 10px;
    border-bottom: 2px solid #0f172a;
  }
  #topbar-title { font-size: 0.95rem; font-weight: 700; color: #7dd3fc; white-space: nowrap; }
  #topbar-vm    { font-size: 0.8rem; color: #94a3b8; white-space: nowrap; }
  #topbar-run   { font-size: 0.75rem; color: #64748b; white-space: nowrap; }
  #topbar-elapsed { font-size: 0.75rem; color: #94a3b8; white-space: nowrap; }
  #topbar-steps { font-size: 0.75rem; color: #cbd5e1; white-space: nowrap; }
  .tb-badge {
    font-size: 0.72rem; padding: 2px 8px; border-radius: 99px; font-weight: 600; white-space: nowrap;
  }
  .tb-pass { background: #166534; color: #bbf7d0; }
  .tb-fail { background: #7f1d1d; color: #fecaca; }
  #hb-pill {
    margin-left: auto; font-size: 0.7rem; padding: 2px 9px; border-radius: 99px;
    font-weight: 600; white-space: nowrap;
    background: #166534; color: #bbf7d0;
    transition: background 0.4s, color 0.4s;
  }
  #hb-pill.yellow { background: #854d0e; color: #fef08a; }
  #hb-pill.red    { background: #7f1d1d; color: #fecaca; }

  /* ── Filter bar ── */
  #filter-bar {
    padding: 7px 16px; background: #f8fafc; border-bottom: 1px solid #e2e8f0;
    display: flex; align-items: center; gap: 8px;
  }
  #filter-bar label { font-size: 0.78rem; color: #475569; }
  #filter-vm {
    font-size: 0.78rem; padding: 3px 8px; border-radius: 4px;
    border: 1px solid #cbd5e1; background: #fff; color: #1e293b;
  }

  /* ── Chat column ── */
  #chat-wrap {
    max-width: 960px; margin: 0 auto; padding: 16px 12px 80px;
    display: flex; flex-direction: column; gap: 8px;
  }

  /* ── Bubble ── */
  .bubble {
    background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px;
    padding: 9px 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.06);
    transition: background 0.3s;
  }
  .bubble.ok   { background: #f0fdf4; border-color: #bbf7d0; }
  .bubble.fail { background: #fff1f2; border-color: #fecaca; }

  /* ── Bubble header row ── */
  .bubble-head {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    margin-bottom: 4px;
  }
  .bh-ts   { font-size: 0.7rem; color: #94a3b8; font-variant-numeric: tabular-nums; }
  .bh-step {
    font-size: 0.68rem; padding: 1px 7px; border-radius: 99px;
    background: #e0e7ff; color: #3730a3; font-weight: 600;
  }
  .bh-icon { font-size: 0.9rem; }
  .bh-kind { font-size: 0.8rem; font-weight: 600; color: #334155; }
  .bh-status-ok   { font-size: 0.75rem; color: #16a34a; font-weight: 600; }
  .bh-status-fail { font-size: 0.75rem; color: #dc2626; font-weight: 600; }
  .bh-running { font-size: 0.75rem; color: #d97706; animation: pulse 1.2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.45} }

  /* ── Bubble body ── */
  .bubble-body {
    font-size: 0.78rem; color: #475569; margin-top: 2px;
    word-break: break-word; white-space: pre-wrap;
  }

  /* ── Screenshot thumbnail ── */
  .bubble-thumb {
    margin-top: 7px; display: inline-block; cursor: pointer;
    border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden;
    line-height: 0;
  }
  .bubble-thumb img { display: block; max-width: 200px; max-height: 120px; object-fit: cover; }

  /* ── Resume-scroll button ── */
  #resume-btn {
    display: none; position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: #1e293b; color: #f1f5f9; border: none; border-radius: 99px;
    padding: 7px 20px; font-size: 0.8rem; cursor: pointer; z-index: 200;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25);
  }
  #resume-btn.visible { display: block; }

  /* ── Lightbox ── */
  #lightbox {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.88);
    align-items: center; justify-content: center; z-index: 999;
  }
  #lightbox.open { display: flex; }
  #lightbox img  { max-width: 95vw; max-height: 92vh; border-radius: 8px; }

  /* ── Mobile ── */
  @media (max-width: 640px) {
    #chat-wrap { padding: 10px 6px 80px; }
    .bubble { padding: 8px 10px; }
    .bubble-thumb img { max-width: 160px; max-height: 96px; }
  }
</style>
</head>
<body>

<div id="topbar">
  <span id="topbar-title">mOSdat live</span>
  <span id="topbar-vm"></span>
  <span id="topbar-run"></span>
  <span id="topbar-elapsed"></span>
  <span id="topbar-steps"></span>
  <span class="tb-badge tb-pass" id="cnt-pass">0 pass</span>
  <span class="tb-badge tb-fail" id="cnt-fail">0 fail</span>
  <span id="hb-pill">live</span>
</div>

<div id="filter-bar">
  <label for="filter-vm">VM:</label>
  <select id="filter-vm" onchange="applyFilter()">
    <option value="">All</option>
  </select>
</div>

<div id="chat-wrap"></div>

<button id="resume-btn" onclick="resumeScroll()">&#8595; Resume</button>

<div id="lightbox" onclick="closeLightbox()">
  <img id="lb-img" src="" alt="">
</div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
// bubbles: array of {id, run, vm, ts, stepNum, kind, label, status, url}
// stepMap: key "run/vm/stepNum" → bubble id (for step_end lookups)
// vmMeta:  key "run/vm" → {latestVm, latestRun, runStart, maxStep, totalSteps, pass, fail}
const bubbles = [];
const stepMap = {};
const vmMeta = {};
let globalPass = 0, globalFail = 0;
let lastEventTs = Date.now();
let runStartTs = Date.now();
let autoScroll = true;
let nextId = 0;

// ── Helpers ────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtTs(ms) {
  const d = new Date(ms);
  return d.getHours().toString().padStart(2,'0') + ':' +
         d.getMinutes().toString().padStart(2,'0') + ':' +
         d.getSeconds().toString().padStart(2,'0');
}

function elapsed(ms) {
  const s = Math.round((Date.now() - ms) / 1000);
  if (s < 60) return s + 's';
  return Math.floor(s/60) + 'm ' + (s%60) + 's';
}

const KIND_ICON = {
  launch:          '▶',
  click:           '🖱',
  type:            '⌨',
  key:             '🔑',
  shell:           '🐚',
  verify:          '👁',
  vlm_verify:      '👁',
  verify_localize: '👁',
  localize:        '🔍',
  vlm_localize:    '🔍',
  screenshot:      '📷',
  step_end_ok:     '✅',
  step_end_fail:   '❌',
  wait:            '⏱',
  retry:           '🔁',
  launch_verify:   '🔎',
  if_visible:      '👀',
  popup_sweep:     '🧹',
  checkpoint:      '🚩',
  step_start:      '➡',
};

function kindIcon(kind) { return KIND_ICON[kind] || '▪'; }

function metaKey(run, vm) { return run + '/' + vm; }

function ensureMeta(run, vm) {
  const k = metaKey(run, vm);
  if (!vmMeta[k]) {
    vmMeta[k] = { run, vm, runStart: Date.now(), maxStep: 0, totalSteps: null, pass: 0, fail: 0 };
    updateFilterDropdown(run, vm, k);
  }
  return k;
}

// ── Filter ─────────────────────────────────────────────────────────────────
let activeFilter = '';

function updateFilterDropdown(run, vm, k) {
  const sel = document.getElementById('filter-vm');
  const existing = new Set([...sel.options].map(o => o.value));
  if (!existing.has(k)) {
    const opt = document.createElement('option');
    opt.value = k;
    opt.textContent = vm + ' (' + run + ')';
    sel.appendChild(opt);
  }
}

function applyFilter() {
  activeFilter = document.getElementById('filter-vm').value;
  const wrap = document.getElementById('chat-wrap');
  wrap.innerHTML = '';
  for (const b of bubbles) {
    if (passesFilter(b)) {
      wrap.insertAdjacentHTML('beforeend', renderBubble(b));
    }
  }
  if (autoScroll) scrollToBottom();
}

function passesFilter(b) {
  if (!activeFilter) return true;
  return metaKey(b.run, b.vm) === activeFilter;
}

// ── Bubble rendering ───────────────────────────────────────────────────────
function renderBubble(b) {
  let headClass = 'bubble';
  if (b.status === 'ok')   headClass += ' ok';
  if (b.status === 'fail') headClass += ' fail';

  let statusHtml = '';
  if (b.status === 'running') {
    statusHtml = `<span class="bh-running">⏳</span>`;
  } else if (b.status === 'ok') {
    statusHtml = `<span class="bh-status-ok">✓ ok</span>`;
  } else if (b.status === 'fail') {
    statusHtml = `<span class="bh-status-fail">✗ fail</span>`;
  }

  const stepBadge = b.stepNum != null
    ? `<span class="bh-step">step ${esc(b.stepNum)}</span>` : '';

  let bodyHtml = '';
  if (b.body) {
    bodyHtml = `<div class="bubble-body">${esc(b.body)}</div>`;
  }
  if (b.url) {
    bodyHtml += `<div class="bubble-thumb" onclick="openLightbox('${esc(b.url)}')">` +
                `<img src="${esc(b.url)}" loading="lazy" alt="screenshot"></div>`;
  }

  return `<div class="bubble" id="bubble-${b.id}" data-mk="${esc(metaKey(b.run, b.vm))}">` +
    `<div class="bubble-head">` +
    `<span class="bh-ts">${esc(fmtTs(b.ts))}</span>` +
    stepBadge +
    `<span class="bh-icon">${kindIcon(b.kind)}</span>` +
    `<span class="bh-kind">${esc(b.kind)}</span>` +
    statusHtml +
    `</div>` +
    bodyHtml +
    `</div>`;
}

function appendBubble(b) {
  bubbles.push(b);
  if (!passesFilter(b)) return;
  document.getElementById('chat-wrap').insertAdjacentHTML('beforeend', renderBubble(b));
  if (autoScroll) scrollToBottom();
}

function updateBubbleDom(b) {
  const el = document.getElementById('bubble-' + b.id);
  if (!el) return;
  // re-render only the bubble element in-place
  const tmp = document.createElement('div');
  tmp.innerHTML = renderBubble(b);
  const newEl = tmp.firstChild;
  el.replaceWith(newEl);
}

// ── Topbar refresh ─────────────────────────────────────────────────────────
let elapsedTimer = null;

function updateTopbar() {
  document.getElementById('cnt-pass').textContent = globalPass + ' pass';
  document.getElementById('cnt-fail').textContent = globalFail + ' fail';

  // Use the most-recently-active VM meta for the header display
  const metas = Object.values(vmMeta);
  if (metas.length > 0) {
    const m = metas[metas.length - 1];
    document.getElementById('topbar-vm').textContent = m.vm;
    document.getElementById('topbar-run').textContent = m.run;
    const stepTxt = m.totalSteps
      ? `step ${m.maxStep} / ${m.totalSteps}`
      : (m.maxStep > 0 ? `step ${m.maxStep}` : '');
    document.getElementById('topbar-steps').textContent = stepTxt;
    if (!elapsedTimer) {
      elapsedTimer = setInterval(function() {
        document.getElementById('topbar-elapsed').textContent = elapsed(runStartTs);
      }, 1000);
      document.getElementById('topbar-elapsed').textContent = elapsed(runStartTs);
    }
  }
}

// ── Heartbeat staleness pill ───────────────────────────────────────────────
function checkStale() {
  const pill = document.getElementById('hb-pill');
  const age = (Date.now() - lastEventTs) / 1000;
  if (age > 5) {
    pill.className = 'red';
    pill.textContent = 'stale ' + Math.round(age) + 's';
  } else if (age > 2) {
    pill.className = 'yellow';
    pill.textContent = Math.round(age) + 's ago';
  } else {
    pill.className = '';
    pill.textContent = 'live';
  }
}
setInterval(checkStale, 1000);

// ── Auto-scroll ────────────────────────────────────────────────────────────
function scrollToBottom() {
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
}

function resumeScroll() {
  autoScroll = true;
  document.getElementById('resume-btn').classList.remove('visible');
  scrollToBottom();
}

window.addEventListener('scroll', function() {
  const fromBottom = document.body.scrollHeight - window.innerHeight - window.scrollY;
  if (fromBottom > 100) {
    if (autoScroll) {
      autoScroll = false;
      document.getElementById('resume-btn').classList.add('visible');
    }
  } else {
    autoScroll = true;
    document.getElementById('resume-btn').classList.remove('visible');
  }
});

// ── Lightbox ───────────────────────────────────────────────────────────────
function openLightbox(src) {
  document.getElementById('lb-img').src = src;
  document.getElementById('lightbox').className = 'open';
}
function closeLightbox() {
  document.getElementById('lightbox').className = '';
}

// ── SSE event handler ──────────────────────────────────────────────────────
const es = new EventSource('/stream');
es.onmessage = function(e) {
  lastEventTs = Date.now();
  let msg;
  try { msg = JSON.parse(e.data); } catch { return; }

  const { run, vm, event } = msg;
  if (!run || !vm) return;

  const mk = ensureMeta(run, vm);
  const meta = vmMeta[mk];

  if (event === 'step_start') {
    const stepNum = msg.step_num;
    if (stepNum != null && stepNum > meta.maxStep) meta.maxStep = stepNum;
    if (msg.total_steps != null) meta.totalSteps = msg.total_steps;

    const id = nextId++;
    const b = {
      id, run, vm,
      ts: Date.now(),
      stepNum: stepNum,
      kind: msg.kind || 'shell',
      label: msg.label || '',
      status: 'running',
      body: msg.label || null,
      url: null,
    };
    stepMap[mk + '/' + stepNum] = id;
    appendBubble(b);

  } else if (event === 'step_end') {
    const stepNum = msg.step_num;
    const ok = msg.status === 'ok';
    if (ok) { meta.pass++; globalPass++; } else { meta.fail++; globalFail++; }

    // update existing running bubble if present
    const bid = stepMap[mk + '/' + stepNum];
    if (bid != null) {
      const b = bubbles.find(x => x.id === bid);
      if (b) {
        b.status = ok ? 'ok' : 'fail';
        b.kind   = ok ? 'step_end_ok' : 'step_end_fail';
        updateBubbleDom(b);
      }
    } else {
      // No matching step_start — create a standalone bubble
      const id = nextId++;
      appendBubble({
        id, run, vm, ts: Date.now(),
        stepNum, kind: ok ? 'step_end_ok' : 'step_end_fail',
        label: '', status: ok ? 'ok' : 'fail', body: null, url: null,
      });
    }

  } else if (event === 'screenshot') {
    const id = nextId++;
    appendBubble({
      id, run, vm, ts: Date.now(),
      stepNum: msg.step_num ?? null,
      kind: 'screenshot', label: '', status: null,
      body: null, url: msg.url,
    });

  } else {
    // Generic event bubble — covers vlm_localize, vlm_verify, click, type, key,
    // shell, launch, wait, if_visible, retry, launch_verify, etc.
    const id = nextId++;
    let body = '';
    if (event === 'click')      body = `(${msg.x}, ${msg.y})`;
    else if (event === 'type')  body = msg.text_redacted || msg.text || '';
    else if (event === 'key')   body = msg.key || '';
    else if (event === 'launch') body = msg.app || '';
    else if (event === 'shell') body = (msg.cmd || '').slice(0, 160);
    else if (event === 'vlm_localize') body = (msg.prompt || msg.label || msg.target || '').slice(0, 200);
    else if (event === 'vlm_verify')   body = (msg.prompt || msg.predicate || msg.text || '').slice(0, 200);
    else if (event === 'launch_verify') body = `process=${msg.process} window=${msg.window}`;
    else if (event === 'vlm_verify' && msg.kind === 'verify_click_diff') body = `diff-click: ${msg.answer || ''} (${msg.latency_ms || 0}ms)`;
    else if (event === 'vlm_verify' && msg.kind === 'canary_verify') body = `canary: ${msg.answer || ''} (${msg.latency_ms || 0}ms)`;
    else if (event === 'retry') body = `retry ${msg.attempt || ''}`;
    else if (event === 'wait')  body = `${msg.seconds || msg.duration_ms || ''}`;
    else                        body = JSON.stringify(msg).slice(0, 200);
    appendBubble({
      id, run, vm, ts: Date.now(),
      stepNum: msg.step_num ?? null,
      kind: event, label: '', status: null,
      body, url: null,
    });
  }

  updateTopbar();
};

es.onerror = function() {
  lastEventTs = 0;  // force red pill
  document.getElementById('hb-pill').className = 'red';
  document.getElementById('hb-pill').textContent = 'disconnected';
};
</script>
</body>
</html>
"""

_TRIAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>mOSdat Live Triage</title>
<style>
:root{--bg:#0b0f14;--panel:#121821;--panel2:#182130;--text:#e8eef6;--muted:#9aa7b8;--border:#2a3547;--ok:#3fb950;--fail:#ff6b6b;--run:#58a6ff;--stale:#f0b429}
*{box-sizing:border-box}body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--text)}
header{position:sticky;top:0;z-index:10;background:rgba(11,15,20,.96);border-bottom:1px solid var(--border);padding:12px 18px}
.top{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.title{font-weight:750;font-size:18px;margin-right:8px}
.pill{border:1px solid var(--border);border-radius:999px;padding:4px 10px;color:var(--muted);font-size:12px}.pill.pass{color:var(--ok);border-color:rgba(63,185,80,.45)}.pill.fail{color:var(--fail);border-color:rgba(255,107,107,.45)}.pill.running{color:var(--run);border-color:rgba(88,166,255,.45)}.pill.stale,.pill.partial{color:var(--stale);border-color:rgba(240,180,41,.45)}
main{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:16px;padding:16px}section{background:var(--panel);border:1px solid var(--border);border-radius:8px;min-width:0}.section-head{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid var(--border)}h2{font-size:14px;margin:0}.matrix{overflow:auto}
table{border-collapse:collapse;width:100%;min-width:720px}th,td{border-bottom:1px solid var(--border);padding:8px;text-align:left;font-size:12px;vertical-align:middle}th{color:var(--muted);font-weight:650;background:var(--panel2);position:sticky;top:0}.vm-name{font-weight:700}.small{color:var(--muted);font-size:12px}
.step-row{display:flex;gap:4px;flex-wrap:wrap}.cell{width:28px;height:28px;border-radius:6px;border:1px solid var(--border);display:inline-flex;align-items:center;justify-content:center;cursor:pointer;color:var(--text);font-size:11px;background:transparent}.cell.pass{background:rgba(63,185,80,.18);border-color:rgba(63,185,80,.55)}.cell.fail{background:rgba(255,107,107,.18);border-color:rgba(255,107,107,.65)}.cell.running{background:rgba(88,166,255,.18);border-color:rgba(88,166,255,.6)}.cell.stale,.cell.partial{background:rgba(240,180,41,.18);border-color:rgba(240,180,41,.6)}.cell.slow{box-shadow:inset 0 -3px 0 rgba(240,180,41,.9)}.cell.hot{box-shadow:inset 0 -3px 0 rgba(255,107,107,.95)}
.thumb{width:74px;height:44px;object-fit:contain;border:1px solid var(--border);border-radius:6px;background:#05070a;cursor:zoom-in}
.fail-list{padding:10px;display:flex;flex-direction:column;gap:10px;max-height:calc(100vh - 150px);overflow:auto}.fail-card{border:1px solid rgba(255,107,107,.45);background:rgba(255,107,107,.08);border-radius:8px;padding:10px;cursor:pointer}.fail-card img{width:100%;max-height:160px;object-fit:contain;border:1px solid var(--border);border-radius:6px;margin-top:8px;background:#05070a}
.drawer{position:fixed;right:0;top:0;bottom:0;width:min(720px,96vw);background:#0f151f;border-left:1px solid var(--border);z-index:30;transform:translateX(100%);transition:.18s transform;overflow:auto}.drawer.open{transform:translateX(0)}.drawer-head{position:sticky;top:0;background:#0f151f;border-bottom:1px solid var(--border);padding:14px;display:flex;justify-content:space-between;gap:12px}.drawer-body{padding:14px}button{background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 10px;cursor:pointer}.event{border:1px solid var(--border);border-radius:6px;padding:8px;margin:8px 0;background:rgba(255,255,255,.025)}.shots{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}.shots img{width:180px;height:110px;object-fit:contain;border:1px solid var(--border);border-radius:6px;background:#05070a;cursor:zoom-in}.empty{padding:28px;color:var(--muted);text-align:center}
#lightbox{display:none;position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.86);align-items:center;justify-content:center;padding:20px}#lightbox img{max-width:96vw;max-height:92vh;border:1px solid var(--border)}@media(max-width:980px){main{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><div class="top"><div class="title">mOSdat Live Triage</div><span id="conn" class="pill stale">connecting</span><span id="runs" class="pill">0 runs</span><span id="running" class="pill running">0 running</span><span id="pass" class="pill pass">0 pass</span><span id="fail" class="pill fail">0 fail</span><span id="stale" class="pill stale">0 stale</span><span id="updated" class="pill">updated never</span><label class="small">Run <select id="run-filter" onchange="setRunFilter(this.value)"><option value="latest">Latest</option><option value="all">All</option></select></label><button onclick="loadState()">Refresh</button></div><div id="freshness" class="small" style="margin-top:8px">No run loaded.</div></header>
<main><section><div class="section-head"><h2>Matrix Overview</h2><span class="small">click a step for timeline</span></div><div id="matrix" class="matrix"><div class="empty">Loading state…</div></div></section><section><div class="section-head"><h2>Failures</h2><span id="failure-count" class="small">0</span></div><div id="failures" class="fail-list"><div class="empty">No failures</div></div></section></main>
<aside id="drawer" class="drawer"><div class="drawer-head"><div><strong id="drawer-title">Timeline</strong><div id="drawer-sub" class="small"></div></div><button onclick="closeDrawer()">Close</button></div><div id="drawer-body" class="drawer-body"></div></aside>
<div id="lightbox" onclick="closeLightbox()"><img id="lightbox-img" src="" alt="screenshot"></div>
<script>
let state=null;let runFilter='latest';function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmtAge(sec){if(sec==null)return'-';if(sec<60)return`${sec}s`;return`${Math.floor(sec/60)}m ${sec%60}s`;}
function cls(status){return['pass','fail','running','stale','partial'].includes(status)?status:''}
async function loadState(){const res=await fetch('/api/state',{cache:'no-store'});state=await res.json();render();}
function visibleRuns(){const runs=state?.runs||[];if(runFilter==='all')return runs;if(runFilter==='latest')return runs.slice(0,1);return runs.filter(r=>r.name===runFilter)}
function setRunFilter(value){runFilter=value;render()}
function updateRunFilter(){const sel=document.getElementById('run-filter');const current=sel.value||runFilter;sel.innerHTML='<option value="latest">Latest</option><option value="all">All</option>'+((state?.runs||[]).slice(0,20).map(r=>`<option value="${esc(r.name)}">${esc(r.name)}</option>`).join(''));sel.value=[...sel.options].some(o=>o.value===current)?current:'latest';runFilter=sel.value}
function visibleTotals(){const runs=visibleRuns();let t={runs:runs.length,running:0,pass:0,fail:0,stale:0};for(const run of runs)for(const vm of run.vms||[])t[vm.status]=(t[vm.status]||0)+1;return t}
function heat(step){const ms=step.duration_ms||0;if(ms>90000)return'hot';if(ms>30000)return'slow';return''}
function render(){if(!state)return;updateRunFilter();const t=visibleTotals();for(const [id,val] of Object.entries({runs:`${t.runs||0} runs`,running:`${t.running||0} running`,pass:`${t.pass||0} pass`,fail:`${t.fail||0} fail`,stale:`${t.stale||0} stale`}))document.getElementById(id).textContent=val;document.getElementById('updated').textContent=`updated ${new Date(state.generated_at).toLocaleTimeString()}`;renderFreshness();renderMatrix();renderFailures();}
function renderFreshness(){const runs=visibleRuns();if(!runs.length){document.getElementById('freshness').textContent='No run selected.';return}const run=runs[0];document.getElementById('freshness').textContent=runFilter==='all'?`Browsing all runs · ${state.totals.vms||0} VMs total`:`Watching ${run.name} · ${run.status} · last event ${fmtAge(run.age_seconds)} ago · ${run.vms.length} VM(s)`}
function renderMatrix(){const rows=[];for(const run of visibleRuns())for(const vm of run.vms||[]){const shot=vm.latest_screenshot;rows.push(`<tr><td><div class="vm-name">${esc(vm.vm)}</div><div class="small">${esc(run.name)}</div></td><td><span class="pill ${cls(vm.status)}">${esc(vm.status)}</span></td><td>${shot?`<img class="thumb" src="${esc(shot.url)}" onclick="openLightbox('${esc(shot.url)}')" loading="lazy">`:'<span class="small">-</span>'}</td><td>${vm.current_step?`step ${esc(vm.current_step.step_num)} <span class="small">${esc(vm.current_step.kind)}</span>`:'<span class="small">-</span>'}</td><td>${fmtAge(vm.duration_seconds)}</td><td><div class="step-row">${(vm.steps||[]).map(step=>`<button class="cell ${cls(step.status)} ${heat(step)}" title="step ${esc(step.step_num)} ${esc(step.status)} ${esc(step.duration_ms??'-')}ms" onclick="openTimeline('${esc(run.name)}','${esc(vm.vm)}','${esc(step.step_num)}')">${esc(step.step_num)}</button>`).join('')}</div></td></tr>`)}document.getElementById('matrix').innerHTML=rows.length?`<table><thead><tr><th>VM</th><th>Status</th><th>Latest</th><th>Current</th><th>Total runtime</th><th>Steps</th></tr></thead><tbody>${rows.join('')}</tbody></table>`:'<div class="empty">No functional runs found.</div>'}
function renderFailures(){const names=new Set(visibleRuns().map(r=>r.name));const failures=(state.failures||[]).filter(f=>runFilter==='all'||names.has(f.run));document.getElementById('failure-count').textContent=String(failures.length);document.getElementById('failures').innerHTML=failures.length?failures.map(f=>`<div class="fail-card" onclick="openTimeline('${esc(f.run)}','${esc(f.vm)}','${esc(f.step_num)}')"><div><strong>${esc(f.vm)}</strong> step ${esc(f.step_num)}</div><div class="small">${esc(f.run)} · <span class="pill fail">${esc(f.cause||'step failed')}</span> · attempts ${esc(f.attempts??'-')} · ${esc(f.duration_ms??'-')}ms</div>${f.question?`<div class="small">Q: ${esc(f.question)}</div>`:''}${f.answer?`<div class="small">A: ${esc(f.answer)}</div>`:''}${f.screenshot?`<img src="${esc(f.screenshot.url)}" loading="lazy">`:''}</div>`).join(''):'<div class="empty">No failures</div>'}
function findVm(runName,vmName){for(const run of state.runs||[])if(run.name===runName)for(const vm of run.vms||[])if(vm.vm===vmName)return vm;return null}
function openTimeline(runName,vmName,stepNum){const vm=findVm(runName,vmName);if(!vm)return;const steps=stepNum===''?vm.steps:vm.steps.filter(s=>String(s.step_num)===String(stepNum));document.getElementById('drawer-title').textContent=`${vmName} timeline`;document.getElementById('drawer-sub').textContent=`${runName} · ${vm.status} · last ${fmtAge(vm.age_seconds)}`;document.getElementById('drawer-body').innerHTML=steps.map(step=>`<div class="event"><div><strong>Step ${esc(step.step_num)}</strong> <span class="pill ${cls(step.status)}">${esc(step.status)}</span></div><div class="small">${esc(step.kind||'')} ${esc(step.label||'')} · ${esc(step.duration_ms??'-')}ms · attempts ${esc(step.attempts??'-')}</div>${(step.events||[]).map(e=>`<div class="event"><div class="small">${esc(e.ts||'')} · ${esc(e.event||'')}</div>${eventBody(e)}</div>`).join('')}<div class="shots">${(step.screenshots||[]).map(s=>`<img src="${esc(s.url)}" title="${esc(s.filename)}" onclick="openLightbox('${esc(s.url)}')" loading="lazy">`).join('')}</div></div>`).join('')||'<div class="empty">No events for this step.</div>';document.getElementById('drawer').classList.add('open')}
function eventBody(e){const bits=[];if(e.question)bits.push(`Q: ${esc(e.question)}`);if(e.answer)bits.push(`A: ${esc(e.answer)}`);if(e.status)bits.push(`status: ${esc(e.status)}`);if(e.latency_ms)bits.push(`latency: ${esc(e.latency_ms)}ms`);return bits.length?`<div>${bits.join('<br>')}</div>`:''}
function closeDrawer(){document.getElementById('drawer').classList.remove('open')}function openLightbox(url){document.getElementById('lightbox-img').src=url;document.getElementById('lightbox').style.display='flex'}function closeLightbox(){document.getElementById('lightbox').style.display='none';document.getElementById('lightbox-img').src=''}
const es=new EventSource('/stream');es.onopen=()=>{const c=document.getElementById('conn');c.textContent='connected';c.className='pill pass'};es.onerror=()=>{const c=document.getElementById('conn');c.textContent='disconnected';c.className='pill fail'};es.onmessage=()=>loadState();loadState();setInterval(loadState,5000);
</script></body></html>
"""


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------
class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the live dashboard HTML, SSE stream, and PNG screenshots."""

    # Injected by factory
    broadcaster: SSEBroadcaster
    results_root: Path
    warn_after: int
    stale_after: int

    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        # Suppress noisy request log; uncomment for debugging
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "":
            self._serve_html()
        elif path == "/api/state":
            self._serve_state()
        elif path == "/stream":
            self._serve_sse()
        elif path.startswith("/png/"):
            self._serve_png(path)
        else:
            self.send_error(404, "Not Found")

    def _serve_html(self) -> None:
        body = _TRIAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_state(self) -> None:
        state = build_dashboard_state(
            self.results_root,
            warn_after=self.warn_after,
            stale_after=self.stale_after,
        )
        body = json.dumps(state).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = self.broadcaster.subscribe()
        last_hb = time.monotonic()
        try:
            while True:
                # Drain pending messages
                pending = list(q)
                del q[:]
                for chunk in pending:
                    self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()

                # Heartbeat every 15 s
                now = time.monotonic()
                if now - last_hb >= 15.0:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    last_hb = now

                time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.broadcaster.unsubscribe(q)

    def _serve_png(self, path: str) -> None:
        # path = /png/<run>/<vm>/<file>
        # Safety: resolve under results_root/functional, reject any .. traversal
        parts = path[len("/png/"):].split("/")
        if len(parts) < 3:
            self.send_error(400, "Bad path")
            return

        # Rebuild candidate path using posixpath.normpath then check prefix
        candidate = posixpath.normpath("/".join(parts))
        if ".." in candidate.split("/"):
            self.send_error(400, "Path traversal rejected")
            return

        run, vm, filename = parts[0], parts[1], "/".join(parts[2:])

        # Additional traversal check on each segment
        for seg in (run, vm, filename):
            if ".." in seg or seg.startswith("/"):
                self.send_error(400, "Path traversal rejected")
                return

        img_path = self.results_root / "functional" / run / vm / filename
        # Final canonical check: resolved path must be under results_root
        try:
            resolved = img_path.resolve()
            base = (self.results_root / "functional").resolve()
            resolved.relative_to(base)  # raises ValueError if outside
        except (ValueError, OSError):
            self.send_error(403, "Forbidden")
            return

        if not resolved.exists() or not resolved.is_file():
            self.send_error(404, "Not Found")
            return

        try:
            data = resolved.read_bytes()
        except OSError:
            self.send_error(500, "Read error")
            return

        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _make_handler(
    broadcaster: SSEBroadcaster,
    results_root: Path,
    warn_after: int = 90,
    stale_after: int = 180,
):
    """Return a DashboardHandler subclass with broadcaster + results_root bound."""

    class BoundHandler(DashboardHandler):
        pass

    BoundHandler.broadcaster = broadcaster
    BoundHandler.results_root = results_root
    BoundHandler.warn_after = warn_after
    BoundHandler.stale_after = stale_after
    return BoundHandler


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def cli(args: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="mosdat live", description="Live event-stream dashboard")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    parser.add_argument("--results", type=Path, default=Path("results"), metavar="DIR",
                        help="Results root directory (default: results/)")
    parser.add_argument("--refresh-ms", type=int, default=500, dest="refresh_ms",
                        help="Watcher poll interval ms (default: 500)")
    parser.add_argument("--warn-after", type=int, default=90, dest="warn_after",
                        help="Mark VM as warning/stale after N seconds without events (default: 90)")
    parser.add_argument("--stale-after", type=int, default=180, dest="stale_after",
                        help="Mark VM stale after N seconds without events (default: 180)")
    parsed = parser.parse_args(args)

    results_root = parsed.results.resolve()
    _hb("cli_start")

    broadcaster = SSEBroadcaster()
    watcher = EventWatcher(results_root, broadcaster, refresh_ms=parsed.refresh_ms, debug_hook=_hb)

    watcher_thread = threading.Thread(target=watcher.run_forever, daemon=True, name="event-watcher")
    watcher_thread.start()
    _hb("watcher_thread_started")

    handler_cls = _make_handler(
        broadcaster,
        results_root,
        warn_after=parsed.warn_after,
        stale_after=parsed.stale_after,
    )
    server = ThreadingHTTPServer(("", parsed.port), handler_cls)
    server.daemon_threads = True

    print(f"[mOSdat] Dashboard live at http://localhost:{parsed.port}")
    print(f"[mOSdat] Watching: {results_root}/functional/")
    print("[mOSdat] Press Ctrl-C to stop.")
    _hb("server_start")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mOSdat] Dashboard stopped.")
    finally:
        watcher.stop()
        server.server_close()
        _hb("server_stop")

    return 0
