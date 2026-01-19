# AI Agent Instructions

## Project Overview

Test framework for Rocket.Chat Electron Linux builds, specifically the Wayland/X11 crash fix (PR #3171).

## Completion Criteria (CRITICAL)

**An OS is considered FULLY TESTED only when ALL of the following are validated:**

### 1. Package Format Testing

Each OS must test ALL supported package formats:

| OS | Required Packages |
|----|-------------------|
| Ubuntu 22.04 | DEB, AppImage, Snap |
| Ubuntu 24.04 | DEB, AppImage, Snap |
| Fedora 42 | RPM, AppImage |
| openSUSE Leap 16.0 | RPM, AppImage |
| Manjaro Linux | AppImage |

### 2. GPU Configuration Testing (BOTH Required)

**Each package format must be tested in BOTH configurations:**

| Config | Description | Test Scripts |
|--------|-------------|--------------|
| **Without GPU** | Virtual displays (Xvfb, Weston headless) | `shared/tests/run-all.sh` |
| **With GPU** | Real NVIDIA RTX 3060 via VFIO passthrough | `shared/tests/run-gpu-tests.sh` |

### 3. Display Scenario Coverage

**Without GPU tests:**
- `x11` - Xvfb virtual X11
- `wayland` - Weston headless
- `wayland-fake` - Invalid socket (THE BUG)
- `wayland-fallback` - Wayland vars + X11 available
- `no-display` - Headless

**With GPU tests:**
- `gpu-wayland-real` - Real Wayland desktop session
- `gpu-wayland-fake` - Invalid socket with real X11 fallback
- `gpu-x11` - Real X11/XWayland session
- `gpu-wayland-nodisp` - WAYLAND_DISPLAY unset

### 4. Full Test Matrix Per OS

```
Tests = Packages × GPU_Configs × Scenarios

Example for Fedora 42:
  Packages: RPM, AppImage (2)
  GPU Configs: Without GPU, With GPU (2)
  Scenarios: 5 without GPU + 4 with GPU (9)
  Total: 2 × 9 = 18 test combinations
```

### Completion Checklist

An OS is **NOT DONE** until:
- [ ] All package formats installed and tested
- [ ] All tests pass WITHOUT GPU (virtual displays)
- [ ] All tests pass WITH GPU (real NVIDIA passthrough)
- [ ] Results documented in `results/` directory
- [ ] TEST-MATRIX.md updated with results

**DO NOT mark an OS as "done" until BOTH GPU configurations are tested.**

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

### Without GPU (Virtual Displays)

| Test | Display Server | What |
|------|----------------|------|
| `x11` | Xvfb | Virtual X11 |
| `wayland` | Weston headless | Virtual Wayland |
| `wayland-fake` | Xvfb only | **THE BUG** - Invalid socket |
| `wayland-fallback` | Xvfb only | Wayland vars + X11 available |
| `no-display` | None | Headless environment |

### With GPU (Real Desktop Session)

| Test | Display Server | What |
|------|----------------|------|
| `gpu-wayland-real` | GNOME/KDE Wayland | Real compositor with GPU |
| `gpu-wayland-fake` | XWayland fallback | Invalid socket, real X11 |
| `gpu-x11` | XWayland or X11 | Real X11 session |
| `gpu-wayland-nodisp` | XWayland | WAYLAND_DISPLAY unset |

### GPU Passthrough Workflow

```bash
# 1. Attach GPU to VM
./os/<os>/gpu-control.sh --attach

# 2. Wait for VM to boot into desktop session
# 3. Run GPU tests via SSH
ssh jean@<VM_IP> '/tmp/tests/run-gpu-tests.sh'

# 4. Detach GPU for next VM
./os/<os>/gpu-control.sh --detach
```

## VM Setup (Required Before Testing)

Before testing a VM, ensure:

1. **SSH key access**: `ssh-copy-id jean@<VM_IP>` (password: see .env)
2. **Passwordless sudo**: `echo "jean ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/jean`
3. **Test dependencies**: `Xvfb`, `weston` installed
   - Fedora: `sudo dnf install -y xorg-x11-server-Xvfb weston`
   - Ubuntu: `sudo apt install -y xvfb weston`
   - openSUSE: `sudo zypper install -y xvfb weston`
   - Manjaro/Arch: `sudo pacman -S xorg-server-xvfb weston`

## ISO Storage Management

ISOs are stored on a CIFS/SMB share. Proxmox only sees ISOs in `template/iso/` subfolder.

| Setting | Value |
|---------|-------|
| Server | 192.168.13.11 |
| Share | isos |
| Username | guest (no password) |
| Required path | `template/iso/` |

**To list ISOs:**
```bash
docker run --rm alpine sh -c "apk add --no-cache samba-client >/dev/null 2>&1 && smbclient //192.168.13.11/isos -N -c 'cd template/iso; ls'"
```

**To move ISO to correct location:**
```bash
docker run --rm alpine sh -c "apk add --no-cache samba-client >/dev/null 2>&1 && smbclient //192.168.13.11/isos -N -c 'rename \"filename.iso\" \"template/iso/filename.iso\"'"
```

**Check Proxmox sees it:**
```bash
TICKET=$(curl -k -s -d "username=root@pam&password=cb6wist3" "https://192.168.13.85:8006/api2/json/access/ticket" | jq -r '.data.ticket')
curl -k -s -b "PVEAuthCookie=$TICKET" "https://192.168.13.85:8006/api2/json/nodes/pve/storage/mushu-isos/content" | jq -r '.data[] | .volid'
```

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
