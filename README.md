# mOSdat

### Multi-OS Desktop App Testing Framework

Automated testing infrastructure using Proxmox VMs with GPU passthrough to validate desktop applications across multiple Linux distributions and display server configurations.

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Linux-blue?style=for-the-badge&logo=linux" alt="Linux">
  <img src="https://img.shields.io/badge/Proxmox-VE%208.x-orange?style=for-the-badge&logo=proxmox" alt="Proxmox">
  <img src="https://img.shields.io/badge/GPU-NVIDIA%20VFIO-76B900?style=for-the-badge&logo=nvidia" alt="NVIDIA">
</p>

---

## Overview

Testing desktop apps properly requires real environments — different distros, display servers, GPU configurations. Containers can't do this. Manual testing doesn't scale.

mOSdat uses Proxmox to orchestrate VMs with actual NVIDIA GPUs passed through via VFIO, enabling automated testing across real hardware configurations.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              mOSdat                                     │
│                                                                         │
│   ┌─────────┐    ┌──────────────┐    ┌─────────────────────────────┐   │
│   │  Your   │───▶│   Proxmox    │───▶│         Test VMs            │   │
│   │  Code   │    │  Orchestrator│    │  ┌───────┐  ┌───────┐       │   │
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

**Full Pipeline** — Build from git ref → deploy to VM → run tests → collect results

**Reproducible** — Same VM snapshot, same test sequence, consistent results

---

## Results

Validated a Wayland compatibility fix for [Rocket.Chat Desktop](https://github.com/RocketChat/Rocket.Chat.Electron):

| Scenario | Before Fix | After Fix |
|:---------|:----------:|:---------:|
| Real Wayland session | PASS | PASS |
| Fake Wayland socket | SEGFAULT | **PASS** |
| Missing display variable | SEGFAULT | **PASS** |
| X11 fallback | SEGFAULT | **PASS** |

See [Case Studies](docs/CASE-STUDIES.md) for details.

---

## Tested Platforms

- Fedora 42 (GNOME/Wayland)
- Ubuntu 22.04 LTS (GNOME)

---

## Documentation

| Document | Description |
|:---------|:------------|
| [Architecture](docs/ARCHITECTURE.md) | System design |
| [Hardware](docs/HARDWARE.md) | Test environment specs |
| [Proxmox Setup](docs/PROXMOX-SETUP.md) | VFIO and GPU passthrough |
| [Case Studies](docs/CASE-STUDIES.md) | Test examples |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues |

---

## Built With

- [Proxmox VE](https://www.proxmox.com/) — VM orchestration
- VFIO/IOMMU — GPU passthrough
- [opencode](https://github.com/opencode-ai/opencode) + [oh-my-opencode](https://github.com/code-yeongyu/oh-my-opencode)
