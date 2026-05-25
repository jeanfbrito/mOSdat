# mOSdat

### Multi-OS Desktop App Testing Framework

Automated testing infrastructure using Proxmox VMs to validate desktop applications across Linux distributions, Windows desktops, display servers, and GPU configurations.

> Supersedes the archived [`electron-linux-testing`](https://github.com/jeanfbrito/electron-linux-testing) Vagrant prototype (Jan 2026).

![Docker](https://img.shields.io/badge/docker-ready-blue)

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Linux-blue?style=for-the-badge&logo=linux" alt="Linux">
  <img src="https://img.shields.io/badge/Platform-Windows-0078D4?style=for-the-badge&logo=windows" alt="Windows">
  <img src="https://img.shields.io/badge/Proxmox-VE%208.x-orange?style=for-the-badge&logo=proxmox" alt="Proxmox">
  <img src="https://img.shields.io/badge/GPU-NVIDIA%20VFIO-76B900?style=for-the-badge&logo=nvidia" alt="NVIDIA">
</p>

---

## Install

```bash
pip install -e .
mosdat --help
```

Typical local development:

```bash
python -m pytest -q
mosdat validate examples/rocketchat.toml
mosdat list-vms examples/rocketchat.toml
```

---

## Run via Docker

A Dockerfile is provided for containerized execution:

```bash
# Build locally
docker build -t mosdat:dev .

# Run help
docker run --rm mosdat:dev

# Run with a config file
docker run --rm -v $(pwd)/myconfig.toml:/app/myconfig.toml mosdat:dev \
  functional /app/myconfig.toml --vms ubuntu2404
```

To use Docker images from the registry (when published):

```bash
# Pull from registry (forward-looking; not yet published)
docker pull ghcr.io/jeanfbrito/mosdat:latest

# Run the smoke test scenario
docker run --rm ghcr.io/jeanfbrito/mosdat:latest \
  functional examples/rocketchat.toml --vms ubuntu2404 --test rocketchat-smoke-linux
```

---

## Overview

Testing desktop apps properly requires real environments — different distros, display servers, Windows releases, and GPU configurations. Containers can't do this. Manual testing doesn't scale.

mOSdat uses Proxmox to orchestrate VMs, drive real desktops over VNC/SSH, pass through NVIDIA GPUs via VFIO when needed, and collect reproducible artifacts for triage.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              mOSdat                                     │
│                                                                         │
│   ┌─────────┐    ┌──────────────┐    ┌─────────────────────────────┐   │
│   │ mosdat  │───▶│   Proxmox    │───▶│         Test VMs            │   │
│   │   CLI   │    │  Orchestrator│    │  ┌───────┐  ┌───────┐       │   │
│   └─────────┘    └──────────────┘    │  │Fedora │  │Ubuntu │  ...  │   │
│                         │            │  │+GPU   │  │+GPU   │       │   │
│                         │            │  │+Wayland│ │+X11   │       │   │
│                         ▼            │  └───────┘  └───────┘       │   │
│                  ┌──────────────┐    └─────────────────────────────┘   │
│                  │   Results    │                   │                  │
│                  │    Report    │◀──────────────────┘                  │
│                  └──────────────┘                                      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Features

**GPU Passthrough** — Real NVIDIA GPUs via VFIO, not emulated

**Display Server Matrix** — Native Wayland, X11, XWayland, and misconfigured environments

**Linux + Windows VMs** — Shared scenario runner for Linux desktops plus Windows 10/11 functional coverage

**Full Pipeline** — Build from git ref → deploy to VM → run tests → collect results

**Accessibility-first UI Automation** — Use AT-SPI role/name targeting on Linux when available, with VLM localization as fallback

**VLM Functional Testing** — Drive real desktops through Proxmox VNC, with VLM localize/verify steps that work across X11, Wayland, and Windows

**Live Triage Dashboard** — Watch current and historical functional runs, stale/dead runs, failures, screenshots, and step timelines from a LAN web UI

**Author Workbench + Agent API** — Create reusable VLM test flows from a browser or via `mosdat author`, including manual coordinate picking, hover, left/right click, type, key, wait, shell, launch, draft-step JSON editing, validation, and YAML export

**Preflight, Replay, Doctor** — Validate scenario/VM readiness, replay cached VLM checks, and diagnose VM health without rerunning a full matrix

**Reproducible** — Same VM snapshot, same test sequence, consistent results

---

## Common Workflows

> **Recommended authoring workflow:** Author routines first (`shared/routines/`), then compose scenarios that call them. See `docs/AUTO-AUTHORING.md`.

Run a functional VLM smoke test:

```bash
mosdat functional examples/rocketchat.toml --vms ubuntu2404 --test rocketchat-smoke-linux
```

Build a Rocket.Chat Electron PR, deploy it, and verify the tested app contains the expected symbol:

```bash
mosdat build --pr 3325 --target deb --deploy ubuntu2204,ubuntu2404 \
  --verify-symbol isTelephonyEnabled
```

Preflight a functional scenario before spending VM/VLM time:

```bash
mosdat preflight examples/rocketchat.toml \
  --vms ubuntu2404 \
  --test rocketchat-smoke-linux
```

Inspect the live Linux accessibility tree for semantic selectors:

```bash
mosdat atspi-dump examples/rocketchat.toml --vms ubuntu2404 --format tree
```

Diagnose VM and host health:

```bash
mosdat doctor examples/rocketchat.toml --vms ubuntu2404
```

Run a recorded functional session replay (change-filtered frames, smaller artifact size):

```bash
mosdat functional examples/rocketchat.toml \
  --vms windows11 \
  --test rocketchat-smoke \
  --record-fps 10 \
  --record-gif
```

> Recording is on by default. Use `--no-record-session` to opt out.

Serve the live dashboard and authoring workbench:

```bash
mosdat live --port 8082 --results results --config examples/rocketchat.toml
```

Open:

- Runs dashboard: `http://<host>:8082/`
- Author Workbench: `http://<host>:8082/author`
- Recording artifacts: open from the run cards or under
  `http://<host>:8082/artifact/...`

Use the agent authoring API through the CLI:

```bash
mosdat author --url http://127.0.0.1:8082 vms
mosdat author --url http://127.0.0.1:8082 doctor
# doctor includes a non-blocking verify_model_configured warning when yes/no checks reuse the localize model
mosdat author --url http://127.0.0.1:8082 start --vm ubuntu2404
mosdat author --url http://127.0.0.1:8082 capture --session <session-id> --output /tmp/screen.bmp
mosdat author --url http://127.0.0.1:8082 localize --session <session-id> --prompt "help tooltip"
mosdat author --url http://127.0.0.1:8082 describe --session <session-id> --x 120 --y 240
mosdat author --url http://127.0.0.1:8082 click --session <session-id> --x 5 --y 6 --prompt "help tooltip"
mosdat author --url http://127.0.0.1:8082 prompt-click --session <session-id> --prompt "help tooltip"
mosdat author --url http://127.0.0.1:8082 prompt-hover --session <session-id> --prompt "help tooltip"
mosdat author --url http://127.0.0.1:8082 prompt-type --session <session-id> --prompt "message box" --text "hello"
mosdat author --url http://127.0.0.1:8082 type --session <session-id> --text "hello"
mosdat author --url http://127.0.0.1:8082 key --session <session-id> --key enter
mosdat author --url http://127.0.0.1:8082 validate --session <session-id>
mosdat author --url http://127.0.0.1:8082 export --session <session-id> --name tooltip-flow
mosdat author --url http://127.0.0.1:8082 export --session <session-id> --name tooltip-flow --output shared/scenarios/functional/tooltip-flow.yaml
mosdat author --url http://127.0.0.1:8082 step --session <session-id> --json '{"key":"escape"}'
mosdat author --url http://127.0.0.1:8082 step --session <session-id> --steps-json '[{"key":"escape"},{"wait":1}]'
mosdat author --url http://127.0.0.1:8082 close --session <session-id>
```

Generate the static historical dashboard:

```bash
mosdat dashboard --root results --output results/functional/dashboard.html
```

Replay a cached VLM verification against an existing result directory:

```bash
mosdat replay results/functional/<run-dir>/<vm> --step 5
```

## Results

Validated a Wayland compatibility fix for [Rocket.Chat Desktop](https://github.com/RocketChat/Rocket.Chat.Electron):

| Scenario | Before Fix | After Fix |
|:---------|:----------:|:---------:|
| Real Wayland session | PASS | PASS |
| Fake Wayland socket | SEGFAULT | **PASS** |
| Missing display variable | SEGFAULT | **PASS** |
| X11 fallback | SEGFAULT | **PASS** |

### Historical GPU Passthrough Test Results

Real hardware validation with NVIDIA RTX 3060 via VFIO:

| OS | gpu-wayland-real | gpu-wayland-fake | gpu-x11 | gpu-wayland-nodisp |
|----|------------------|------------------|---------|-------------------|
| Fedora 42 | PASS | PASS | PASS | PASS |
| Ubuntu 22.04 | SKIP (X11 default) | PASS | PASS | PASS |
| Ubuntu 24.04 | PASS | PASS | PASS | PASS |
| openSUSE Leap 16.0 | SKIP (X11 default) | PASS | PASS | N/A |
| Manjaro Linux 26.0.1 | PASS | PASS | PASS | N/A |

See [Test Matrix](docs/TEST-MATRIX.md) and [Case Studies](docs/CASE-STUDIES.md) for details.

---

## Tested Platforms

| Platform | Desktop | Package formats | Scenario coverage | Status |
|----------|---------|-----------------|-------------------|--------|
| Fedora 42 | GNOME (Wayland) | RPM, AppImage, Flatpak | Smoke + TEL QA | Complete |
| Ubuntu 22.04 LTS | GNOME (X11) | DEB, AppImage, Flatpak, Snap | Smoke + TEL QA | Complete |
| Ubuntu 24.04 LTS | GNOME (Wayland) | DEB, AppImage, Flatpak, Snap | Smoke + TEL QA | Complete |
| openSUSE Leap 16.0 | KDE (X11) | RPM, AppImage, Flatpak | Smoke + TEL QA | Complete |
| Manjaro Linux 26.0.1 | KDE (Wayland) | AppImage, Flatpak | Smoke + TEL QA | Complete |
| Windows 10 | Windows desktop | EXE | Smoke + TEL QA | Configured |
| Windows 11 | Windows desktop | EXE | Smoke + TEL QA | Configured |

**Notes:**
- All 5 target distributions fully tested with real GPU passthrough
- openSUSE using nouveau driver (open source) with software rendering
- Manjaro running latest kernel (6.18) with KDE Plasma on Wayland
- Windows 10/11 VMs are configured for functional scenario coverage and EXE install flows

See [Linux Coverage Strategy](docs/LINUX-COVERAGE.md) for why these distributions were selected.

---

## Documentation

| Document | Description |
|:---------|:------------|
| [Architecture](docs/ARCHITECTURE.md) | System design |
| [Hardware](docs/HARDWARE.md) | Test environment specs |
| [Linux Coverage](docs/LINUX-COVERAGE.md) | Distribution selection strategy |
| [Test Matrix](docs/TEST-MATRIX.md) | Test results by OS |
| [Proxmox Setup](docs/PROXMOX-SETUP.md) | VFIO and GPU passthrough |
| [Case Studies](docs/CASE-STUDIES.md) | Test examples |
| [Functional Linux Tests](docs/FUNCTIONAL-TESTS-LINUX.md) | Linux AT-SPI selectors, VNC input, and VLM verification |
| [AT-SPI Authoring](docs/atspi-authoring.md) | Accessibility-first Linux selector workflow |
| [Reusable Routines](docs/routines.md) | Shared scenario routine library |
| [Live Dashboard](docs/runbooks/live-dashboard.md) | Real-time triage dashboard and Author Workbench |
| [Matrix Run](docs/runbooks/matrix-run.md) | Current matrix execution runbook |
| [Agent Monitoring](docs/runbooks/agent-monitoring.md) | Long-running run monitoring patterns |
| [Visual Regression](docs/runbooks/visual-regression.md) | Screenshot reference capture/check workflow |
| [Completion Criteria](docs/runbooks/completion-criteria.md) | Done criteria for OS/package/GPU coverage |
| [Triage](docs/runbooks/triage.md) | Failure triage and exit-code interpretation |
| [Auto-Authoring](docs/AUTO-AUTHORING.md) | **Generate functional test YAMLs from code changes via `mosdat draft`** |
| [Issue Confirmation](docs/issue-confirm-tool.md) | GitHub issue confirmation workflow |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues |

---

## Built With

- [Proxmox VE](https://www.proxmox.com/) — VM orchestration
- VFIO/IOMMU — GPU passthrough
- Python 3.11+ — CLI, runner, and scenario orchestration
- [opencode](https://github.com/opencode-ai/opencode) + [oh-my-opencode](https://github.com/code-yeongyu/oh-my-opencode)
