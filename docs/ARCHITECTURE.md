# Test Framework Architecture

## Overview

This framework tests desktop applications across multiple operating systems
using Proxmox VMs, optional GPU passthrough, VNC-backed input, and VLM screen
understanding. The current implementation is Python-first (`automation/`) with
TOML app/VM configuration and YAML functional scenarios.

## Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Host Machine                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              mOSdat Python CLI                       │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │    │
│  │  │ config   │  │ runner   │  │ live dashboard   │   │    │
│  │  └────┬─────┘  └────┬─────┘  └────────┬─────────┘   │    │
│  │       │             │                 │             │    │
│  │       ▼             ▼                 ▼             │    │
│  │  ┌──────────────────────────────────────────────┐   │    │
│  │  │            Proxmox API (REST)                │   │    │
│  │  └──────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
│                            │                                 │
└────────────────────────────┼─────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    Proxmox VE Server                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  VM 100     │  │  VM 101     │  │  VM 102-105         │  │
│  │  Fedora 42  │  │  Ubuntu     │  │  (Other VMs)        │  │
│  │  + GPU?     │  │  22.04      │  │                     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              NVIDIA RTX 3060 (VFIO)                 │    │
│  │              Can be attached to any VM              │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow

### Configuration

```
examples/*.toml
  ├── [app] source/build/package metadata
  ├── [proxmox] API connection
  ├── [vlm] model endpoint and expected model
  └── [[vm]] VM name, VMID, desktop, packages, SSH user
```

The main command surface is:

| Command | Purpose |
|---------|---------|
| `mosdat run` | Build/deploy/test matrix |
| `mosdat test` | Test pre-built package(s) |
| `mosdat functional` | Run VLM functional UI scenarios |
| `mosdat live` | Serve live triage dashboard and Author Workbench |
| `mosdat author` | Agent CLI for authoring API |
| `mosdat dashboard` | Generate static historical dashboard |
| `mosdat visual` | Capture/check visual references |
| `mosdat confirm` | Confirm or verify-fix GitHub issues |

## Agent Authoring API

`mosdat live --config <config.toml>` also exposes an authoring API for agents
and the browser workbench. The intended flow is:

1. `GET /api/author/vms` to choose a running VM.
2. `POST /api/author/session` with `{"vm": "ubuntu2404"}`.
3. `POST /api/author/capture` with `{"session_id": "..."}` to refresh VNC.
4. `POST /api/author/vlm/localize` or `/api/author/vlm/verify` to inspect the screen.
5. `POST /api/author/action` with `confirm: true` to run `hover`, `click`, `type`, `key`, `shell`, `wait`, or `launch`.
6. `POST /api/author/validate` to check that the draft flow is runnable.
7. `GET /api/author/export?session=...&name=...` to retrieve scenario YAML.

Agents should prefer the CLI wrapper because it prints compact JSON:

```
python -m automation.main author --url http://127.0.0.1:8082 vms
python -m automation.main author --url http://127.0.0.1:8082 start --vm ubuntu2404
python -m automation.main author --url http://127.0.0.1:8082 localize --session SESSION --prompt "help tooltip"
python -m automation.main author --url http://127.0.0.1:8082 action --session SESSION --kind hover --json '{"x":5,"y":6,"prompt":"help tooltip"}'
python -m automation.main author --url http://127.0.0.1:8082 validate --session SESSION
python -m automation.main author --url http://127.0.0.1:8082 export --session SESSION --name tooltip-flow
```

### Functional Test Phase

```
YAML scenario -> FunctionalRunner
        |
        ├── Proxmox VNC capture/input
        ├── VLM localize/verify
        ├── SSH only for shell/launch/focus helpers
        └── events.jsonl + screenshots in results/functional/<run>/<vm>/
```

VNC input is display-server agnostic and is the preferred path for clicks,
hover, typing, and key presses. This avoids X11/Wayland focus and xauth
problems.

### Build Phase

```
1. Git checkout specific version
2. yarn build (TypeScript → JavaScript)
3. electron-builder (→ RPM/DEB/AppImage)
4. Package stored in dist/
```

### Deploy Phase

```
1. Get VM IP via Proxmox guest agent API
2. SCP package to VM /tmp/
3. SSH: sudo dnf/apt install package
4. Verify installation
```

### Legacy Smoke Test Phase

```
1. SSH into VM
2. Set environment variables (WAYLAND_DISPLAY, etc.)
3. Run rocketchat-desktop with timeout
4. Capture exit code and output
5. Parse results (PASS/FAIL/SEGFAULT)
```

## Test Cases

| Test | Environment | Expected (Fixed) |
|------|-------------|------------------|
| wayland-real | Valid Wayland socket | Native Wayland |
| wayland-fake | Non-existent socket | X11 fallback (no crash) |
| wayland-nodisp | No WAYLAND_DISPLAY | X11 fallback |
| x11 | X11 session | X11 |

## Exit Code Interpretation

| Code | Meaning | Result |
|------|---------|--------|
| 0 | Clean exit | PASS |
| 124 | Timeout (app ran N seconds) | PASS |
| 139 | SIGSEGV (segfault) | FAIL |
| 134 | SIGABRT | FAIL |
| 6 | SIGABRT (alternate) | FAIL |
| Other | Unknown | UNKNOWN |

## GPU Passthrough

### Compute Mode (Default)

```
hostpci0=0000:01:00,pcie=1
```

- GPU available for compute/rendering
- VNC console still works
- Recommended for testing

### Primary Mode

```
hostpci0=0000:01:00,pcie=1,x-vga=1
```

- GPU is primary display
- VNC console blank
- Requires physical monitor
- Not recommended for automated testing

## File Organization

```
mOSdat/
├── automation/          # CLI, config, Proxmox, VLM, runners, dashboards
├── examples/            # App/VM TOML configs
├── shared/scenarios/    # Functional YAML scenarios
├── docs/                # Architecture and runbooks
├── tests/               # Pytest coverage and CLI help snapshots
└── results/             # Generated run artifacts (mostly gitignored)
```

Older shell helpers under `shared/` are retained for compatibility and low-level
reference, but the Python CLI is the canonical workflow.

## Why Configuration Is Per VM/App?

1. **Package formats differ**: RPM, DEB, AppImage, Snap, Flatpak, MSI.
2. **Package managers differ**: dnf, apt, pacman, zypper, snap, flatpak.
3. **Desktop behavior differs**: GNOME, KDE, X11, Wayland, Windows.
4. **Launch and cleanup paths differ**: app names, temp dirs, package paths.
5. **Reproducibility matters**: TOML config makes VM/app/package assumptions explicit.
