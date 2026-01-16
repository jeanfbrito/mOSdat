# mOSdat

**Multi-OS Desktop App Testing**

[![Proxmox](https://img.shields.io/badge/Proxmox-VE%208.x-orange.svg)](https://www.proxmox.com/)
[![Rocket.Chat](https://img.shields.io/badge/Rocket.Chat-Desktop-red.svg)](https://github.com/RocketChat/Rocket.Chat.Electron)

Automated testing framework that spins up real VMs, deploys your desktop app, and validates it actually works - across multiple operating systems, display servers, and GPU configurations.

---

## Why?

> "Works on my machine" isn't good enough.

Desktop apps behave differently on Fedora vs Ubuntu, Wayland vs X11, with GPU vs without. Manual testing is slow and inconsistent. **mOSdat automates it.**

---

## What It Does

```
Build app → Deploy to VM → Run test scenarios → Get results
     ↓           ↓              ↓                  ↓
  From Git    Proxmox API    Wayland/X11      Pass/Fail matrix
```

**One command. Multiple OSes. Real results.**

```bash
./full-test.sh
```

---

## Real Results

Testing [Rocket.Chat Desktop](https://github.com/RocketChat/Rocket.Chat.Electron) Wayland fix:

| Scenario | Before Fix | After Fix |
|:---------|:----------:|:---------:|
| Real Wayland session | PASS | PASS |
| Fake Wayland socket | SEGFAULT | **PASS** |
| Missing display var | SEGFAULT | **PASS** |
| X11 fallback | SEGFAULT | **PASS** |

The fix works. We proved it. Automatically.

---

## Features

- **VM Orchestration** - Proxmox API controls VMs programmatically
- **GPU Passthrough** - Test with real NVIDIA acceleration via VFIO
- **Display Server Matrix** - Wayland, X11, headless scenarios
- **Automated Pipeline** - Build from any git ref, deploy, test, report

---

## Tested Platforms

- Fedora 42 (GNOME/Wayland)
- Ubuntu 22.04 (GNOME)

---

## Quick Start

```bash
# Configure
cp shared/config.example.sh shared/config.local.sh

# Run
cd os/fedora-42
./full-test.sh
```

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - How it works
- [Hardware](docs/HARDWARE.md) - Test environment specs
- [Case Studies](docs/CASE-STUDIES.md) - Validated tests
- [Proxmox Setup](docs/PROXMOX-SETUP.md) - VFIO/GPU passthrough
- [Troubleshooting](docs/TROUBLESHOOTING.md) - Common issues

---

## Built With

- [opencode](https://github.com/opencode-ai/opencode) + [oh-my-opencode](https://github.com/code-yeongyu/oh-my-opencode)

---

**Current target**: [Rocket.Chat Desktop](https://github.com/RocketChat/Rocket.Chat.Electron)
