# Test Matrix

## Package Formats by OS

| OS | DEB | RPM | AppImage | Snap | Flatpak |
|----|:---:|:---:|:--------:|:----:|:-------:|
| Fedora 42 | - | ✅ | ✅ | - | 🔜 |
| Ubuntu 22.04 | ✅ | - | ✅ | ✅ | 🔜 |
| Ubuntu 24.04 | ✅ | - | ✅ | ✅ | 🔜 |
| openSUSE Leap 16.0 | - | ✅ | ✅ | - | 🔜 |
| Manjaro Linux 26.0.1 | - | - | ✅ | - | 🔜 |

See [LINUX-COVERAGE.md](LINUX-COVERAGE.md) for detailed coverage analysis.

## Display Scenarios

| Test | Description | Simulates |
|------|-------------|-----------|
| `x11` | Real X11 via Xvfb | Normal X11 desktop user |
| `wayland` | Real Wayland via Weston | Fedora/Ubuntu Wayland user |
| `wayland-fake` | WAYLAND_DISPLAY set, no socket | **THE BUG** - compositor crashed |
| `wayland-x11-fallback` | Wayland vars + X11 available | Should fallback to X11 |
| `no-display` | No display server | Headless/server environment |

## GPU Configurations

| Config | Description |
|--------|-------------|
| With GPU | NVIDIA RTX 3060 via VFIO passthrough |
| Without GPU | Software rendering (Xvfb/Weston headless) |

### GPU Passthrough Test Results

Real GPU passthrough testing with NVIDIA RTX 3060 attached to VMs:

| OS | Session Type | gpu-wayland-real | gpu-wayland-fake | gpu-x11 | gpu-wayland-nodisp |
|----|--------------|------------------|------------------|---------|-------------------|
| Fedora 42 | Wayland (GNOME) | ✅ PASS | ✅ PASS | ✅ PASS | ✅ PASS |
| Ubuntu 22.04 | X11 (GNOME) | ⏭️ SKIP | ✅ PASS | ✅ PASS | ✅ PASS |
| Ubuntu 24.04 | Wayland (GNOME) | ✅ PASS | ✅ PASS | ✅ PASS | ✅ PASS |
| openSUSE Leap | X11 (KDE Plasma) | ⏭️ SKIP | ✅ PASS | ✅ PASS | N/A |
| Manjaro Linux 26.0.1 | Wayland (KDE) | ✅ PASS | ✅ PASS | ✅ PASS | N/A |

**Notes:**
- Ubuntu 22.04 uses X11 by default, so gpu-wayland-real was skipped
- openSUSE Leap uses X11 (KDE Plasma with nouveau driver), gpu-wayland-real skipped
- Manjaro uses KDE Plasma on Wayland - all tests passed
- All tested VMs show GPU visible via `lspci | grep nvidia`

## Full Test Matrix

Each combination: `OS × Package × Display × GPU`

### Fedora 42

**With GPU (RTX 3060 passthrough) - Tested 2026-01-20:**

| Test | Result | Exit Code |
|------|--------|-----------|
| gpu-wayland-real | ✅ PASS | 0 |
| gpu-wayland-fake | ✅ PASS | 0 |
| gpu-x11-xwayland | ✅ PASS | 0 |
| gpu-wayland-nodisp | ✅ PASS | 0 |

**Without GPU (Virtual displays):**

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| RPM | ✅ | ✅ | ✅ | ✅ | ✅ |
| AppImage | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |

Note: GPU tests run on real GNOME Wayland session with XWayland fallback available.

### Ubuntu 22.04

**With GPU (RTX 3060 passthrough) - Tested 2026-01-20:**

| Test | Result | Exit Code | Note |
|------|--------|-----------|------|
| gpu-wayland-real | ⏭️ SKIP | - | Ubuntu 22.04 uses X11 by default |
| gpu-wayland-fake | ✅ PASS | 0 | |
| gpu-x11 | ✅ PASS | 0 | |
| gpu-wayland-nodisp | ✅ PASS | 0 | |

**Without GPU (Virtual displays):**

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| DEB | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |
| AppImage | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |
| Snap | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |

Note: Ubuntu 22.04 defaults to X11 (GNOME on Xorg), not Wayland.

### Ubuntu 24.04

**With GPU (RTX 3060 passthrough) - Tested 2026-01-20:**

| Test | Result | Exit Code |
|------|--------|-----------|
| gpu-wayland-real | ✅ PASS | timeout (expected) |
| gpu-wayland-fake | ✅ PASS | timeout (expected) |
| gpu-x11 | ✅ PASS | timeout (expected) |
| gpu-wayland-nodisp | ✅ PASS | timeout (expected) |

**Without GPU (Virtual displays):**

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| DEB | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |
| AppImage | ✅ | ✅ | ✅ | ✅ | ✅ |
| Snap | ✅ | ✅ | ✅ | ✅ | ⚠️ |

Note: Ubuntu 24.04 defaults to Wayland (GNOME 46). All GPU tests passed with real Wayland session.

### openSUSE Leap 16.0

**Unique Coverage:**
- SUSE enterprise ecosystem (zypper package manager)
- KDE Plasma desktop (KWin compositor)
- European enterprise market

**With GPU (RTX 3060 passthrough) - Tested 2026-01-20:**

| Test | Result | Exit Code | Note |
|------|--------|-----------|------|
| gpu-wayland-real | ⏭️ SKIP | - | Session is X11 (nouveau driver) |
| gpu-wayland-fake | ✅ PASS | 0 | App correctly fell back to X11 |
| gpu-x11 | ✅ PASS | 0 | Normal X11 launch worked |
| gpu-force-x11 | ✅ PASS | 0 | --ozone-platform=x11 worked |

Note: KDE Plasma installed with nouveau driver. GPU detected but using software rendering (no NVIDIA proprietary driver). All crash fix tests passed.

**Without GPU (Virtual displays):**

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| RPM | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |
| AppImage | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |

### Manjaro Linux

**Unique Coverage:**
- Arch-based rolling release distribution
- KDE Plasma desktop (KWin Wayland compositor)
- Catches bleeding-edge compatibility issues
- Developer workstation use case
- Covers Arch Linux ecosystem (pacman, AUR)

**With GPU (RTX 3060 passthrough) - Tested 2026-01-20:**

| Test | Result | Exit Code | Note |
|------|--------|-----------|------|
| gpu-wayland-real | ✅ PASS | 0 | Real KDE Wayland session |
| gpu-wayland-fake | ✅ PASS | 0 | App fell back to X11 correctly |
| gpu-x11 | ✅ PASS | 0 | Force X11 mode worked |

**Without GPU (Virtual displays) - Tested 2026-01-20:**

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| AppImage | ✅ | ✅ | ✅ | ✅ | ✅ |

Legend: ✅ Pass | ❌ Fail | 🔲 Not tested | ⏭️ Skip (needs GPU) | ⚠️ Expected (no display) | 🔜 Planned

## The Bug (GitHub #3154)

**Problem:** When `WAYLAND_DISPLAY` environment variable is set but the Wayland socket doesn't exist, Electron tries to connect and crashes with SEGFAULT (exit code 139).

**Real scenarios where this happens:**
1. User logged into X11 but env vars leaked from previous Wayland session
2. GNOME Shell/compositor crashed, socket removed, env vars remain
3. App started from terminal in different session context
4. Misconfigured system with partial Wayland setup

**The Fix (PR #3171):** Wrapper script that checks if Wayland socket is actually usable before letting Electron attempt connection. Falls back to X11 if Wayland is broken.
