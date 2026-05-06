# Live Dashboard and Author Workbench

The live server serves two tools from the same process:

- `/` — functional-run triage dashboard
- `/author` — browser workbench for creating VLM-driven test flows

## Launch

```bash
mosdat live --port 8082 --results results --config examples/rocketchat.toml
```

Open:

- `http://localhost:8082/` for run triage
- `http://localhost:8082/author` for authoring

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8080` | HTTP port |
| `--results` | `results/` | Root results directory to watch |
| `--refresh-ms` | `500` | Poll interval for new events/screenshots |
| `--warn-after` | `90` | Mark VM warning/stale after N seconds without events |
| `--stale-after` | `180` | Mark VM stale after N seconds without events |
| `--config` | none | Enables authoring sessions and VM status lookup |

## Runs Dashboard

The root page is a triage view over `results/functional/`.

It shows:

- latest or selected historical runs
- VM status (`running`, `pass`, `fail`, `stale`, `partial`)
- latest screenshot thumbnails
- current step and total runtime
- per-step status cells with slow/hot duration markers
- failure cards with VLM question/answer and screenshots
- a timeline drawer for individual steps

Dead runs are inferred from artifacts and event age, so old runs that no
longer have a process should not remain as live-running forever.

## Author Workbench

`/author` is a full-page tool for creating reproducible functional flows.

Current capabilities:

- VM dropdown populated from config, with Proxmox running/stopped status
- lazy session creation when capture/localize/verify/action is used
- VNC screen capture and manual refresh
- VLM localize with a precise X marker on the rendered image
- VLM yes/no verify
- hover, left click, right click, type, key, wait, shell, and launch actions
- manual screenshot coordinate picking by clicking the captured image
- JSON step append or full draft-step replacement through the agent CLI/API
- browser draft steps JSON editor with load, replace, and append controls
- validation and session close from the browser workbench and agent CLI/API
- automatic capture after actions
- draft YAML preview and export

Action semantics:

| UI action | YAML emitted | Runtime behavior |
|-----------|--------------|------------------|
| Hover | `localize: ...`, `hover: true` | VNC mouse move only |
| Left click | `localize: ...` | VNC button 1 |
| Right click | `localize: ...`, `click: right` | VNC button 3 |
| Type | `type: ...` | VNC text input |
| Key | `key: ...` | VNC key press |
| Wait | `wait: ...` | Bounded server-side wait |
| Shell | `shell: ...` | SSH shell command |
| Launch | `launch: ...`, `wait: ...` | SSH launch helper |

## Agent Authoring API

Agents should prefer the CLI wrapper because it prints compact JSON:

```bash
mosdat author --url http://127.0.0.1:8082 vms
mosdat author --url http://127.0.0.1:8082 doctor
mosdat author --url http://127.0.0.1:8082 start --vm ubuntu2404
mosdat author --url http://127.0.0.1:8082 capture --session <session-id>
mosdat author --url http://127.0.0.1:8082 localize --session <session-id> --prompt "help tooltip"
mosdat author --url http://127.0.0.1:8082 click --session <session-id> --x 5 --y 6 --prompt "help tooltip"
mosdat author --url http://127.0.0.1:8082 type --session <session-id> --text "hello"
mosdat author --url http://127.0.0.1:8082 key --session <session-id> --key enter
mosdat author --url http://127.0.0.1:8082 validate --session <session-id>
mosdat author --url http://127.0.0.1:8082 export --session <session-id> --name tooltip-flow
mosdat author --url http://127.0.0.1:8082 export --session <session-id> --name tooltip-flow --output shared/scenarios/functional/tooltip-flow.yaml
mosdat author --url http://127.0.0.1:8082 step --session <session-id> --json '{"key":"escape"}'
mosdat author --url http://127.0.0.1:8082 close --session <session-id>
```

Raw HTTP endpoints:

| Endpoint | Purpose |
|----------|---------|
| `GET /api/author/vms` | Configured VMs and Proxmox power state |
| `POST /api/author/session` | Create a session |
| `GET /api/author/session?session=...` | Session state, latest result, draft steps |
| `POST /api/author/capture` | JSON VNC capture payload |
| `GET /api/author/screenshot?session=...` | Browser-compatible capture payload |
| `POST /api/author/vlm/localize` | Locate a UI target |
| `POST /api/author/vlm/verify` | Ask a yes/no screen question |
| `POST /api/author/action` | Run confirmed action |
| `POST /api/author/validate` | Validate draft YAML through scenario parser |
| `GET /api/author/export?session=...&name=...` | Export scenario YAML |
| `POST /api/author/step` | Append a single step or replace full draft steps |
| `POST /api/author/close` | Close the VNC-backed authoring session |

## How It Works

```
results/functional/<run>/<vm>/events.jsonl   <- tailed by EventWatcher
results/functional/<run>/<vm>/*.png          <- discovered via scandir
        |
        v
SSEBroadcaster -> /stream -> browser

Proxmox VNC + configured VMs -> AuthorManager -> /api/author/*
```

The dashboard is dependency-light: stdlib HTTP server, embedded HTML/CSS/JS,
and no frontend build step.

## Stopping

Press `Ctrl-C` in the terminal running `mosdat live`.
