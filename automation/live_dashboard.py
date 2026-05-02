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
import os
import posixpath
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

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
# Inline HTML
# ---------------------------------------------------------------------------
_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>mOSdat live</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f1117; color: #e0e0e0; }
  #topbar {
    display: flex; align-items: center; gap: 16px;
    background: #1a1d2e; padding: 10px 16px; border-bottom: 1px solid #2d3045;
    position: sticky; top: 0; z-index: 100;
  }
  #topbar h1 { font-size: 1rem; font-weight: 600; color: #7f9cf5; }
  .badge {
    font-size: 0.75rem; padding: 2px 8px; border-radius: 99px; font-weight: 600;
  }
  .badge-inflight { background: #2d3045; color: #93c5fd; }
  .badge-pass { background: #14532d; color: #86efac; }
  .badge-fail { background: #7f1d1d; color: #fca5a5; }
  #stale-indicator {
    margin-left: auto; font-size: 0.7rem; padding: 2px 8px; border-radius: 4px;
    background: #14532d; color: #86efac;
  }
  #stale-indicator.stale { background: #7f1d1d; color: #fca5a5; }
  #filter-bar { padding: 8px 16px; background: #12151f; border-bottom: 1px solid #2d3045; }
  #filter-bar select { background: #1a1d2e; color: #e0e0e0; border: 1px solid #2d3045; padding: 4px 8px; border-radius: 4px; }
  #lanes { padding: 12px 16px; display: flex; flex-direction: column; gap: 16px; }
  .lane {
    background: #1a1d2e; border: 1px solid #2d3045; border-radius: 8px; overflow: hidden;
  }
  .lane-header {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 14px; background: #12151f; font-size: 0.85rem; font-weight: 600;
  }
  .lane-body { padding: 10px 14px; display: flex; flex-direction: column; gap: 8px; }
  .timeline { display: flex; flex-wrap: wrap; gap: 6px; min-height: 28px; }
  .chip {
    font-size: 0.7rem; padding: 3px 8px; border-radius: 4px; cursor: default;
    white-space: nowrap; max-width: 220px; overflow: hidden; text-overflow: ellipsis;
  }
  .chip-shell    { background: #1e3a5f; color: #93c5fd; }
  .chip-key      { background: #1c3a2c; color: #86efac; }
  .chip-type     { background: #2d2a1e; color: #fbbf24; }
  .chip-localize { background: #2a1f3d; color: #c4b5fd; }
  .chip-launch   { background: #1a2e1a; color: #6ee7b7; }
  .chip-verify   { background: #1e2a3a; color: #60a5fa; }
  .chip-ok       { border-left: 3px solid #22c55e; }
  .chip-fail     { border-left: 3px solid #ef4444; background: #3b1818; color: #fca5a5; }
  .chip-running  { border-left: 3px solid #facc15; animation: pulse 1s infinite; }
  @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.5 } }
  .thumbs { display: flex; flex-wrap: wrap; gap: 6px; }
  .thumb { cursor: pointer; border: 1px solid #2d3045; border-radius: 4px; overflow: hidden; }
  .thumb img { display: block; height: 72px; width: auto; }
  #lightbox {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.85);
    align-items: center; justify-content: center; z-index: 999;
  }
  #lightbox.open { display: flex; }
  #lightbox img { max-width: 95vw; max-height: 90vh; border-radius: 6px; }
  .empty-msg { color: #4b5563; font-size: 0.8rem; font-style: italic; }
</style>
</head>
<body>
<div id="topbar">
  <h1>mOSdat live</h1>
  <span class="badge badge-inflight" id="cnt-inflight">0 in-flight</span>
  <span class="badge badge-pass" id="cnt-pass">0 pass</span>
  <span class="badge badge-fail" id="cnt-fail">0 fail</span>
  <span id="stale-indicator">live</span>
</div>
<div id="filter-bar">
  <select id="filter-vm" onchange="applyFilter()">
    <option value="">All VMs</option>
  </select>
</div>
<div id="lanes"></div>
<div id="lightbox" onclick="closeLightbox()">
  <img id="lb-img" src="" alt="">
</div>
<script>
const state = {};        // key: "run/vm" => {steps, thumbs, inflight, pass, fail}
let lastEventTs = Date.now();

function laneKey(run, vm) { return run + '/' + vm; }

function ensureLane(run, vm) {
  const k = laneKey(run, vm);
  if (!state[k]) {
    state[k] = { run, vm, steps: [], thumbs: [], inflight: 0, pass: 0, fail: 0 };
    renderLane(k);
    updateFilter();
  }
  return k;
}

function chipClass(kind, status) {
  const kindMap = { shell:'chip-shell', key:'chip-key', type:'chip-type',
    localize:'chip-localize', launch:'chip-launch', verify:'chip-verify' };
  const base = kindMap[kind] || 'chip-shell';
  const statusMap = { ok:'chip-ok', fail:'chip-fail', running:'chip-running' };
  return 'chip ' + base + ' ' + (statusMap[status] || '');
}

function renderLane(k) {
  const lanes = document.getElementById('lanes');
  const filter = document.getElementById('filter-vm').value;
  const d = state[k];
  if (filter && laneKey(d.run, d.vm) !== filter && d.vm !== filter) return;

  let el = document.getElementById('lane-' + k.replace(/\//g, '-'));
  if (!el) {
    el = document.createElement('div');
    el.className = 'lane';
    el.id = 'lane-' + k.replace(/\//g, '-');
    lanes.appendChild(el);
  }
  const stepsHtml = d.steps.length
    ? d.steps.map(s =>
        `<span class="${chipClass(s.kind, s.status)}" title="${esc(s.label || s.kind)}">${esc(s.label || s.kind)}</span>`
      ).join('')
    : '<span class="empty-msg">waiting for steps…</span>';

  const thumbsHtml = d.thumbs.map(t =>
    `<div class="thumb" onclick="openLightbox('${t}')"><img src="${t}" loading="lazy"></div>`
  ).join('');

  el.innerHTML = `
    <div class="lane-header">
      <span>${esc(d.vm)}</span>
      <span style="color:#6b7280;font-size:0.75rem">${esc(d.run)}</span>
      <span class="badge badge-pass" style="margin-left:auto">${d.pass}✓</span>
      <span class="badge badge-fail">${d.fail}✗</span>
    </div>
    <div class="lane-body">
      <div class="timeline">${stepsHtml}</div>
      ${thumbsHtml ? '<div class="thumbs">' + thumbsHtml + '</div>' : ''}
    </div>`;
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function applyFilter() {
  document.getElementById('lanes').innerHTML = '';
  for (const k of Object.keys(state)) renderLane(k);
}

function updateFilter() {
  const sel = document.getElementById('filter-vm');
  const existing = new Set([...sel.options].map(o => o.value));
  for (const k of Object.keys(state)) {
    const d = state[k];
    if (!existing.has(k)) {
      const opt = document.createElement('option');
      opt.value = k; opt.textContent = d.vm + ' (' + d.run + ')';
      sel.appendChild(opt);
    }
  }
}

function updateTopbar() {
  let inflight = 0, pass = 0, fail = 0;
  for (const d of Object.values(state)) { inflight += d.inflight; pass += d.pass; fail += d.fail; }
  document.getElementById('cnt-inflight').textContent = inflight + ' in-flight';
  document.getElementById('cnt-pass').textContent = pass + ' pass';
  document.getElementById('cnt-fail').textContent = fail + ' fail';
}

function checkStale() {
  const el = document.getElementById('stale-indicator');
  const age = (Date.now() - lastEventTs) / 1000;
  if (age > 5) {
    el.className = 'stale';
    el.textContent = 'stale ' + Math.round(age) + 's';
  } else {
    el.className = '';
    el.textContent = 'live';
  }
}
setInterval(checkStale, 1000);

function openLightbox(src) {
  document.getElementById('lb-img').src = src;
  document.getElementById('lightbox').className = 'open';
}
function closeLightbox() {
  document.getElementById('lightbox').className = '';
}

// SSE connection
const es = new EventSource('/stream');
es.onmessage = function(e) {
  lastEventTs = Date.now();
  let msg;
  try { msg = JSON.parse(e.data); } catch { return; }

  const { run, vm, event } = msg;
  if (!run || !vm) return;
  const k = ensureLane(run, vm);
  const d = state[k];

  if (event === 'step_start') {
    d.inflight++;
    d.steps.push({ kind: msg.kind || 'shell', label: msg.label || '', status: 'running', num: msg.step_num });
  } else if (event === 'step_end') {
    d.inflight = Math.max(0, d.inflight - 1);
    const step = d.steps.find(s => s.num === msg.step_num);
    if (step) step.status = msg.status === 'ok' ? 'ok' : 'fail';
    if (msg.status === 'ok') d.pass++; else d.fail++;
  } else if (event === 'screenshot') {
    d.thumbs.push(msg.url);
  }

  renderLane(k);
  updateTopbar();
};
es.onerror = function() {
  document.getElementById('stale-indicator').className = 'stale';
  document.getElementById('stale-indicator').textContent = 'disconnected';
};
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# SSEBroadcaster
# ---------------------------------------------------------------------------
class SSEBroadcaster:
    """Thread-safe fan-out of SSE messages to all connected clients."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: list[list[str]] = []

    def subscribe(self) -> list[str]:
        """Register a new subscriber; returns its shared queue."""
        q: list[str] = []
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: list[str]) -> None:
        with self._lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    def push(self, payload: str) -> None:
        """Push raw SSE payload string to all subscribers."""
        with self._lock:
            for q in self._queues:
                q.append(payload)

    def broadcast_event(self, data: dict) -> None:
        """Serialize dict to SSE event and fan out."""
        payload = f"data: {json.dumps(data)}\n\n"
        self.push(payload)

    def heartbeat(self) -> None:
        self.push(": heartbeat\n\n")


# ---------------------------------------------------------------------------
# EventWatcher
# ---------------------------------------------------------------------------
class EventWatcher:
    """Polls results/functional/<run>/<vm>/events.jsonl for new lines.

    Also scans for new PNG files in the same directory.
    Emits discovered events to an SSEBroadcaster.
    """

    def __init__(
        self,
        results_root: Path,
        broadcaster: SSEBroadcaster,
        refresh_ms: int = 500,
    ) -> None:
        self._root = results_root
        self._bc = broadcaster
        self._refresh = refresh_ms / 1000.0
        # per-(run,vm) state
        self._positions: dict[tuple[str, str], int] = {}   # byte offset in events.jsonl
        self._mtimes: dict[tuple[str, str], float] = {}    # last seen mtime
        self._known_pngs: dict[tuple[str, str], set[str]] = {}  # seen PNG basenames
        self._stop = threading.Event()

    def _functional_dir(self) -> Path:
        return self._root / "functional"

    def _iter_vms(self):
        """Yield (run_name, vm_name, vm_path) for all active run/vm dirs."""
        fn_dir = self._functional_dir()
        if not fn_dir.exists():
            return
        try:
            for run_entry in os.scandir(fn_dir):
                if not run_entry.is_dir():
                    continue
                try:
                    for vm_entry in os.scandir(run_entry.path):
                        if not vm_entry.is_dir():
                            continue
                        yield run_entry.name, vm_entry.name, Path(vm_entry.path)
                except OSError:
                    pass
        except OSError:
            pass

    def _poll_once(self) -> None:
        for run, vm, vm_path in self._iter_vms():
            key = (run, vm)
            events_file = vm_path / "events.jsonl"

            # --- tail events.jsonl ---
            if events_file.exists():
                try:
                    mtime = events_file.stat().st_mtime
                except OSError:
                    mtime = 0.0
                if mtime != self._mtimes.get(key, -1.0):
                    self._mtimes[key] = mtime
                    offset = self._positions.get(key, 0)
                    try:
                        with events_file.open("rb") as fh:
                            fh.seek(offset)
                            chunk = fh.read()
                            new_offset = offset + len(chunk)
                        self._positions[key] = new_offset
                        for raw_line in chunk.split(b"\n"):
                            raw_line = raw_line.strip()
                            if not raw_line:
                                continue
                            try:
                                evt = json.loads(raw_line)
                            except json.JSONDecodeError:
                                continue  # skip malformed mid-line
                            evt["run"] = run
                            evt["vm"] = vm
                            self._bc.broadcast_event(evt)
                    except OSError:
                        pass

            # --- detect new PNGs ---
            known = self._known_pngs.setdefault(key, set())
            try:
                for entry in os.scandir(vm_path):
                    if entry.name.endswith(".png") and entry.name not in known:
                        known.add(entry.name)
                        url = f"/png/{run}/{vm}/{entry.name}"
                        self._bc.broadcast_event(
                            {"event": "screenshot", "run": run, "vm": vm, "url": url}
                        )
            except OSError:
                pass

    def run_forever(self) -> None:
        _hb("watcher_start")
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(self._refresh)
        _hb("watcher_stop")

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------
class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the live dashboard HTML, SSE stream, and PNG screenshots."""

    # Injected by factory
    broadcaster: SSEBroadcaster
    results_root: Path

    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        # Suppress noisy request log; uncomment for debugging
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "":
            self._serve_html()
        elif path == "/stream":
            self._serve_sse()
        elif path.startswith("/png/"):
            self._serve_png(path)
        else:
            self.send_error(404, "Not Found")

    def _serve_html(self) -> None:
        body = _HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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


def _make_handler(broadcaster: SSEBroadcaster, results_root: Path):
    """Return a DashboardHandler subclass with broadcaster + results_root bound."""

    class BoundHandler(DashboardHandler):
        pass

    BoundHandler.broadcaster = broadcaster
    BoundHandler.results_root = results_root
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
    parsed = parser.parse_args(args)

    results_root = parsed.results.resolve()
    _hb("cli_start")

    broadcaster = SSEBroadcaster()
    watcher = EventWatcher(results_root, broadcaster, refresh_ms=parsed.refresh_ms)

    watcher_thread = threading.Thread(target=watcher.run_forever, daemon=True, name="event-watcher")
    watcher_thread.start()
    _hb("watcher_thread_started")

    handler_cls = _make_handler(broadcaster, results_root)
    server = HTTPServer(("", parsed.port), handler_cls)

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
