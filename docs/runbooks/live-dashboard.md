# Live Dashboard Runbook

## Launch

```bash
mosdat live --port 8080 --results results/
```

Then open `http://localhost:8080` in a browser.

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | 8080 | HTTP port |
| `--results` | `results/` | Root results directory to watch |
| `--refresh-ms` | 500 | Poll interval for new events/screenshots |

## What it shows

- **Top bar**: in-flight step count, cumulative pass/fail counts, staleness indicator (turns red if no event received for >5 s).
- **Swimlanes**: one lane per `<run>/<vm>` pair, auto-discovered as directories appear under `results/functional/`.
- **Step chips**: coloured by kind (shell/key/type/localize/launch/verify) and status (running/ok/fail). Pulsing = in progress.
- **Screenshot thumbnails**: click any thumbnail to enlarge.
- **Filter dropdown**: select a single VM for a full-width view.

## How it works

```
results/functional/<run>/<vm>/events.jsonl   ← tailed every 500 ms
results/functional/<run>/<vm>/*.png          ← new files detected via scandir
        |
        v
EventWatcher (background thread)
        |
        v
SSEBroadcaster  ──── /stream (text/event-stream) ──── browser EventSource
                                                        (vanilla JS, no deps)
```

- `/` — serves embedded HTML (no external files needed).
- `/stream` — SSE endpoint; heartbeat every 15 s keeps the connection alive through proxies.
- `/png/<run>/<vm>/<file>` — serves screenshot PNGs; path traversal is rejected with HTTP 400/403.

## Running multiple VMs simultaneously

Start `mosdat functional` with multiple `--vms` in separate terminals (or in parallel). The dashboard discovers all `<run>/<vm>` directories automatically and shows a swimlane for each.

## Stopping

Press `Ctrl-C` in the terminal running `mosdat live`.
