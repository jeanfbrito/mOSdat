# mOSdat

### Multi-OS Desktop App Testing Framework

> **Test desktop apps like users actually use them.**  
> Real GPUs. Real display servers. Real operating systems. Automated.

---

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Linux-blue?style=for-the-badge&logo=linux" alt="Linux">
  <img src="https://img.shields.io/badge/Proxmox-VE%208.x-orange?style=for-the-badge&logo=proxmox" alt="Proxmox">
  <img src="https://img.shields.io/badge/GPU-NVIDIA%20VFIO-76B900?style=for-the-badge&logo=nvidia" alt="NVIDIA">
</p>

---

## The Problem

You've built a desktop app. It works on your machine. But does it work on:

- Fedora with Wayland?
- Ubuntu with X11?
- A system with a broken display server?
- When the GPU driver behaves differently?
- When Wayland claims to exist but doesn't really?

**Manual testing across all these scenarios takes weeks.**  
Setting up N machines with N configurations is a nightmare.  
And containers? They don't have real GPUs or real display servers.

---

## The Solution

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

**One command. Multiple VMs. Real hardware. Automated results.**

---

## What Makes This Different

### Real GPU Passthrough

Not emulated. Not mocked. **Actual NVIDIA GPUs** passed through to VMs via VFIO.

Your tests run on hardware that mirrors what your users actually have.

### True Display Server Testing

- **Native Wayland** — Full compositor, real protocols
- **X11/XWayland** — The classic, still everywhere
- **Broken Wayland** — Fake sockets, missing vars, the chaos users create

### Zero Human Intervention

```
git ref → build app → VM boots → app deploys → tests run → results collected
```

No clicking through installers. No manual verification. The entire pipeline is orchestrated through Proxmox's API.

### Reproducible Environments

Same VM. Same test sequence. Same results.  
No more "it worked yesterday" or "works on my machine."

---

## Real Results

We used mOSdat to validate a Wayland compatibility fix for [Rocket.Chat Desktop](https://github.com/RocketChat/Rocket.Chat.Electron).

| Scenario | Before Fix | After Fix |
|:---------|:----------:|:---------:|
| Real Wayland session | PASS | PASS |
| Fake Wayland socket | SEGFAULT | **PASS** |
| Missing display variable | SEGFAULT | **PASS** |
| X11 fallback | SEGFAULT | **PASS** |

**Three crash scenarios caught and verified fixed** — automatically, in minutes instead of days.

See [Case Studies](docs/CASE-STUDIES.md) for details.

---

## Tested Platforms

- Fedora 42 (GNOME/Wayland)
- Ubuntu 22.04 LTS (GNOME)

---

## Documentation

| Document | Description |
|:---------|:------------|
| [Architecture](docs/ARCHITECTURE.md) | How the pieces fit together |
| [Hardware](docs/HARDWARE.md) | Test environment specs |
| [Proxmox Setup](docs/PROXMOX-SETUP.md) | VFIO and GPU passthrough |
| [Case Studies](docs/CASE-STUDIES.md) | Real-world testing examples |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | When things go wrong |

---

## Why This Matters

**Desktop app testing is stuck in 2010.**

Web apps have Playwright, Cypress, BrowserStack. They run in CI, in containers, everywhere.

Desktop apps? You're still spinning up VMs manually, clicking through installers, and hoping someone remembers to test on Fedora.

**mOSdat brings desktop app testing into the modern era.**

- Real hardware testing
- Reproducible environments
- Automated pipelines
- Actual results you can trust

---

## Built With

- **[Proxmox VE](https://www.proxmox.com/)** — VM orchestration
- **VFIO/IOMMU** — GPU passthrough
- **[opencode](https://github.com/opencode-ai/opencode) + [oh-my-opencode](https://github.com/code-yeongyu/oh-my-opencode)** — AI-assisted development

---

<p align="center">
  <strong>Stop testing desktop apps like it's 2010.</strong><br>
  <em>Automate everything. Verify on real hardware.</em>
</p>
