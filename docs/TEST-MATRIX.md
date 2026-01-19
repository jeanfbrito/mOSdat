# Test Matrix

## Package Formats by OS

| OS | DEB | RPM | AppImage | Snap | Flatpak |
|----|:---:|:---:|:--------:|:----:|:-------:|
| Fedora 42 | - | ✅ | ✅ | - | 🔜 |
| Ubuntu 22.04 | ✅ | - | ✅ | ✅ | 🔜 |
| Ubuntu 24.04 | ✅ | - | ✅ | ✅ | 🔜 |
| openSUSE Leap 16.0 | - | ✅ | ✅ | - | 🔜 |
| Manjaro Linux | - | - | 🔲 | - | 🔜 |

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
| With GPU | NVIDIA via VFIO passthrough |
| Without GPU | Software rendering only |

## Full Test Matrix

Each combination: `OS × Package × Display × GPU`

### Fedora 42

**With GPU (RTX 3060 passthrough):**

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| RPM | ✅ | ✅ | ✅ | ✅ | ✅ |
| AppImage | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |

**Without GPU:**

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| RPM | ✅ | ✅ | ✅ | ✅ | ✅ |
| AppImage | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |

Note: RPM tested with native GNOME Wayland. AppImage tested with Weston headless (has limitations).

### Ubuntu 22.04

**With GPU (RTX 3060 passthrough):**

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| DEB | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |
| AppImage | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |
| Snap | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |

**Without GPU:**

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| DEB | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |
| AppImage | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |
| Snap | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |

Note: wayland test uses Weston headless which has known incompatibility with Electron/Chromium GPU initialization. Real Wayland desktops (GNOME/KDE) work correctly.

### Ubuntu 24.04

**Without GPU:**

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| DEB | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |
| AppImage | ✅ | ✅ | ✅ | ✅ | ✅ |
| Snap | ✅ | ✅ | ✅ | ✅ | ⚠️ |

Note: Ubuntu 24.04 has GNOME 46 with improved Wayland support. AppImage and Snap work well with Weston headless.

### openSUSE Leap 16.0

**Unique Coverage:**
- SUSE enterprise ecosystem (zypper package manager)
- KDE Plasma desktop (KWin Wayland compositor)
- European enterprise market

**Without GPU:**

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| RPM | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |
| AppImage | ✅ | ⏭️ | ✅ | ✅ | ⚠️ |

Note: wayland test uses Weston headless which has known incompatibility with Electron/Chromium GPU initialization. Real KDE Plasma Wayland sessions work correctly.

### Manjaro Linux

🔲 Not yet tested - VM needs installation via VNC.

**Unique Coverage:**
- Arch-based rolling release distribution
- KDE Plasma desktop (KWin Wayland compositor)
- Catches bleeding-edge compatibility issues
- Developer workstation use case
- Covers Arch Linux ecosystem (pacman, AUR)

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| AppImage | 🔲 | 🔲 | 🔲 | 🔲 | 🔲 |

Legend: ✅ Pass | ❌ Fail | 🔲 Not tested | ⏭️ Skip (needs GPU) | ⚠️ Expected (no display) | 🔜 Planned

## The Bug (GitHub #3154)

**Problem:** When `WAYLAND_DISPLAY` environment variable is set but the Wayland socket doesn't exist, Electron tries to connect and crashes with SEGFAULT (exit code 139).

**Real scenarios where this happens:**
1. User logged into X11 but env vars leaked from previous Wayland session
2. GNOME Shell/compositor crashed, socket removed, env vars remain
3. App started from terminal in different session context
4. Misconfigured system with partial Wayland setup

**The Fix (PR #3171):** Wrapper script that checks if Wayland socket is actually usable before letting Electron attempt connection. Falls back to X11 if Wayland is broken.
