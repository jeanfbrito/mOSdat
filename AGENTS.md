# AI Agent Instructions

## Project Overview

Test framework for Rocket.Chat Electron Linux builds, specifically the Wayland/X11 crash fix (PR #3171).

## Completion Criteria (IMPORTANT)

**An OS is considered tested when ALL package formats listed in [TEST-MATRIX.md](docs/TEST-MATRIX.md) have been validated.**

| OS | Required Packages |
|----|-------------------|
| Ubuntu 22.04 | DEB, AppImage, Snap |
| Ubuntu 24.04 | DEB, AppImage, Snap |
| Fedora 42 | RPM, AppImage |
| Arch Linux | AppImage |

Each package format must pass all display scenario tests (x11, wayland-fake, wayland-fallback) before the OS is marked complete.

**DO NOT mark an OS as "done" until all its packages are tested.**

## What It Does

1. Builds Rocket.Chat Electron from any git ref
2. Deploys to Proxmox VMs via REST API
3. Runs automated Wayland/X11 crash tests
4. Compares old vs new version results

## Architecture

```
Host (Ubuntu 24.04)          Proxmox (192.168.13.85)
┌─────────────────┐          ┌─────────────────────┐
│ test-framework/ │  ──API── │ VMs (Fedora/Ubuntu) │
│ build/deploy/   │  ──SSH── │ + RTX 3060 GPU      │
└─────────────────┘          └─────────────────────┘
```

See [docs/HARDWARE.md](docs/HARDWARE.md) for full specs.

## Key Files

| File | Purpose |
|------|---------|
| `shared/config.sh` | Config with env var defaults |
| `shared/proxmox-api.sh` | REST API helpers |
| `os/*/config.sh` | OS-specific (VMID, package format) |
| `os/*/build.sh` | Build RPM/DEB/AppImage |
| `os/*/deploy.sh` | Transfer + install on VM |
| `os/*/test.sh` | Run crash tests |
| `os/*/gpu-control.sh` | Attach/detach GPU |

## Test Matrix

| Test | What |
|------|------|
| `wayland-real` | Valid Wayland (should work) |
| `wayland-fake` | Invalid socket (old crashes) |
| `wayland-nodisp` | Missing WAYLAND_DISPLAY |
| `x11` | X11 session |

## VM Setup (Required Before Testing)

Before testing a VM, ensure:

1. **SSH key access**: `ssh-copy-id jean@<VM_IP>` (password: see .env)
2. **Passwordless sudo**: `echo "jean ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/jean`
3. **Test dependencies**: `Xvfb`, `weston` installed
   - Fedora: `sudo dnf install -y xorg-x11-server-Xvfb weston`
   - Ubuntu: `sudo apt install -y xvfb weston`

## Adding New OS

1. Copy existing OS folder
2. Update `config.sh` (VMID, package format)
3. Modify `deploy.sh` for package manager
4. Update `README.md`

## Code Style

- `set -euo pipefail`
- `log()`, `log_error()`, `log_success()`
- Exit: 0=success, 1=fail, 2=unknown

## Don't

- Hardcode credentials
- Commit binaries (.rpm, .deb)
- Modify `shared/` for OS-specific changes
