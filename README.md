# Rocket.Chat Electron Linux Test Framework

[![Platform](https://img.shields.io/badge/Platform-Linux-blue.svg)](https://www.linux.org/)
[![Proxmox](https://img.shields.io/badge/Proxmox-VE%208.x-orange.svg)](https://www.proxmox.com/)
[![Rocket.Chat](https://img.shields.io/badge/Rocket.Chat-Electron-red.svg)](https://github.com/RocketChat/Rocket.Chat.Electron)

A modular testing infrastructure for validating [Rocket.Chat Electron](https://github.com/RocketChat/Rocket.Chat.Electron) builds across multiple Linux distributions, with optional NVIDIA GPU passthrough.

---

## Purpose

Test the **Wayland/X11 crash fix** ([PR #3171](https://github.com/RocketChat/Rocket.Chat.Electron/pull/3171)) that prevents `SEGFAULT` when `WAYLAND_DISPLAY` points to a non-existent socket.

**The Problem**: Electron apps crash with SIGSEGV (exit 139) when Wayland is misconfigured.

**The Solution**: A wrapper script detects invalid Wayland and forces X11 fallback.

---

## Test Results (2026-01-16)

| Test Scenario | Old v4.11.0 | New v4.11.1 |
|:--------------|:-----------:|:-----------:|
| Real Wayland session | ✅ PASS | ✅ PASS |
| Fake Wayland socket | ❌ SEGFAULT | ✅ PASS |
| Missing WAYLAND_DISPLAY | ❌ SEGFAULT | ✅ PASS |
| X11 session | ❌ SEGFAULT | ✅ PASS |

See [results/](results/) for detailed reports.

---

## Quick Start

```bash
# 1. Configure
cp shared/config.example.sh shared/config.local.sh
# Edit with your credentials

# 2. Run tests
cd os/fedora-42
./full-test.sh
```

---

## Supported Platforms

| OS | Status | Package | VM ID |
|----|:------:|---------|:-----:|
| Fedora 42 | ✅ | RPM | 100 |
| Ubuntu 22.04 | ✅ | DEB | 101 |
| Ubuntu 24.04 | 📋 | DEB | 102 |
| Arch Linux | 📋 | AppImage | 103 |
| Windows 10 | 📋 | NSIS | 104 |
| Windows 11 | 📋 | NSIS | 105 |

---

## Directory Structure

```
test-framework/
├── shared/           # Config and API helpers
├── os/<distro>/      # OS-specific scripts
├── results/          # Test reports
└── docs/             # Documentation
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [HARDWARE.md](docs/HARDWARE.md) | Machine specs (host, Proxmox, VMs) |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Technical design |
| [PROXMOX-SETUP.md](docs/PROXMOX-SETUP.md) | VFIO/GPU setup |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues |

---

## Tools Used

This framework was developed with assistance from:

- **[opencode](https://github.com/opencode-ai/opencode)** - AI-powered coding assistant CLI
- **[oh-my-opencode](https://github.com/code-yeongyu/oh-my-opencode)** - Enhanced agent orchestration plugin

---

## Related

- [Rocket.Chat Electron](https://github.com/RocketChat/Rocket.Chat.Electron)
- [PR #3171](https://github.com/RocketChat/Rocket.Chat.Electron/pull/3171) - The fix
- [Issue #3154](https://github.com/RocketChat/Rocket.Chat.Electron/issues/3154) - Original bug
