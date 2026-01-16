# mOSdat - Multi-OS Desktop App Testing

[![Proxmox](https://img.shields.io/badge/Proxmox-VE%208.x-orange.svg)](https://www.proxmox.com/)
[![Rocket.Chat](https://img.shields.io/badge/Rocket.Chat-Desktop-red.svg)](https://github.com/RocketChat/Rocket.Chat.Electron)

A Proxmox-based testing framework that validates desktop applications across multiple operating systems.

**Spin up VMs. Deploy your app. Verify it works.**

---

## Current Target: Rocket.Chat Desktop

This framework is currently configured to test [Rocket.Chat Desktop](https://github.com/RocketChat/Rocket.Chat.Electron) (Electron-based) across Linux distributions.

---

## Features

- **VM Orchestration** - Control Proxmox VMs via API (start, stop, status)
- **GPU Passthrough** - NVIDIA VFIO passthrough for GPU-accelerated testing
- **Display Server Testing** - Wayland, X11, and headless scenarios
- **Automated Pipeline** - Build, deploy, and test in one command

---

## Supported Platforms

| OS | Status | Package Format | VM ID |
|----|:------:|----------------|:-----:|
| Fedora 42 | Tested | RPM | 100 |
| Ubuntu 22.04 | Tested | DEB | 101 |

---

## Quick Start

```bash
# 1. Configure credentials
cp shared/config.example.sh shared/config.local.sh
# Edit with your Proxmox credentials

# 2. Run full test suite
cd os/fedora-42
./full-test.sh
```

---

## Directory Structure

```
mOSdat/
├── shared/           # Config and Proxmox API helpers
├── os/<distro>/      # OS-specific scripts (build, deploy, test)
├── results/          # Test reports
└── docs/             # Documentation
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Technical design |
| [HARDWARE.md](docs/HARDWARE.md) | Machine specs (host, Proxmox, VMs) |
| [PROXMOX-SETUP.md](docs/PROXMOX-SETUP.md) | VFIO/GPU passthrough setup |
| [CASE-STUDIES.md](docs/CASE-STUDIES.md) | Validated tests and fixes |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues |

---

## Tools Used

Developed with:

- **[opencode](https://github.com/opencode-ai/opencode)** - AI-powered coding assistant CLI
- **[oh-my-opencode](https://github.com/code-yeongyu/oh-my-opencode)** - Enhanced agent orchestration plugin

---

## Related

- [Rocket.Chat Desktop](https://github.com/RocketChat/Rocket.Chat.Electron)
