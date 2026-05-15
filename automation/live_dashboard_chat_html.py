"""Chat-timeline HTML for the live event stream view."""

from __future__ import annotations

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
