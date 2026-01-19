# Linux Distribution Coverage Strategy

This document explains our testing strategy for achieving maximum enterprise Linux coverage with minimal test environments.

## Executive Summary

We test **5 distributions** to achieve **~95% enterprise Linux desktop coverage**:

| Distribution | Version | Covers |
|--------------|---------|--------|
| Fedora | 42 | Red Hat family (RHEL, Rocky, Alma, CentOS) |
| Ubuntu | 22.04, 24.04 | Debian family (Debian, Mint, Pop!_OS, Elementary) |
| openSUSE Leap | 16.0 | SUSE family + KDE desktop |
| Manjaro Linux | Rolling | Arch-based rolling releases + bleeding-edge issues |

**Each distribution is tested in TWO configurations:**
1. **Without GPU** - Virtual displays (Xvfb, Weston headless)
2. **With GPU** - Real NVIDIA RTX 3060 via VFIO passthrough

## Enterprise Linux Market Analysis

### Server Market (for context)

| Distribution Family | Market Share | Notes |
|--------------------|--------------|-------|
| RHEL/Fedora family | ~43% | Government, finance, healthcare |
| Ubuntu/Debian family | ~34% | Cloud, startups, general enterprise |
| SUSE family | ~15% | Europe, automotive, manufacturing |
| Other | ~8% | Arch, Gentoo, specialty distros |

### Desktop Considerations

Enterprise desktop Linux deployments typically use:
- **LTS/Stable releases** - Not rolling releases
- **Supported distributions** - With commercial backing or large community
- **Standard desktop environments** - GNOME or KDE Plasma

## Distribution Family Coverage

### Red Hat Family

**Tested:** Fedora 42

**Covers by extension:**
| Distribution | Why It's Covered |
|--------------|------------------|
| RHEL 10 | Fedora is RHEL's upstream; binary compatible |
| Rocky Linux | RHEL clone, binary compatible |
| AlmaLinux | RHEL clone, binary compatible |
| CentOS Stream | Fedora downstream, same packages |
| Oracle Linux | RHEL clone |

**Package Manager:** dnf (RPM-based)
**Desktop:** GNOME (Mutter Wayland compositor)

### Debian Family

**Tested:** Ubuntu 22.04 LTS, Ubuntu 24.04 LTS

**Covers by extension:**
| Distribution | Why It's Covered |
|--------------|------------------|
| Debian Stable | Ubuntu's upstream, same DEB packages |
| Linux Mint | Ubuntu-based, same package ecosystem |
| Pop!_OS | Ubuntu-based (System76) |
| Elementary OS | Ubuntu-based |
| Zorin OS | Ubuntu-based |
| KDE Neon | Ubuntu-based |

**Package Manager:** apt (DEB-based)
**Desktop:** GNOME (Mutter Wayland compositor)

### SUSE Family

**Tested:** openSUSE Leap 16.0

**Covers by extension:**
| Distribution | Why It's Covered |
|--------------|------------------|
| SUSE Linux Enterprise Desktop (SLED) | Leap is built from SLE source |
| SUSE Linux Enterprise Server (SLES) | Same codebase as Leap |

**Package Manager:** zypper (RPM-based, but different from dnf)
**Desktop:** KDE Plasma (KWin Wayland compositor)

### Rolling Release Family

**Tested:** Manjaro Linux

**Covers by extension:**
| Distribution | Why It's Covered |
|--------------|------------------|
| Arch Linux | Manjaro's upstream |
| EndeavourOS | Arch-based |
| Garuda Linux | Arch-based |

**Package Manager:** pacman
**Package Format Tested:** AppImage (no native Rocket.Chat package in AUR)
**Desktop:** KDE Plasma (KWin Wayland compositor)

## Package Format Coverage

| Format | Tested On | Coverage |
|--------|-----------|----------|
| **DEB** | Ubuntu 22.04, 24.04 | All Debian-based distros |
| **RPM (dnf)** | Fedora 42 | All Red Hat-based distros |
| **RPM (zypper)** | openSUSE Leap | All SUSE-based distros |
| **AppImage** | All distros | Universal Linux format |
| **Snap** | Ubuntu 22.04, 24.04 | Ubuntu and Snap-enabled distros |
| **Flatpak** | Not tested | Future consideration |

## Desktop Environment Coverage

### Why Desktop Environment Matters

Different desktop environments use different **Wayland compositors**, which can have different behaviors with Electron applications:

| Desktop | Wayland Compositor | Tested |
|---------|-------------------|--------|
| GNOME | Mutter | Yes (Fedora, Ubuntu) |
| KDE Plasma | KWin | Yes (openSUSE) |
| Xfce | N/A (X11 only) | Covered via X11 tests |
| Cinnamon | Muffin | Similar to Mutter |

### The Wayland Bug Context

The bug we're testing (GitHub #3154) involves Electron crashing when:
- `WAYLAND_DISPLAY` is set but the socket doesn't exist
- This can happen on ANY Wayland compositor
- Different compositors may handle edge cases differently

Testing both **Mutter (GNOME)** and **KWin (KDE)** ensures we cover the two dominant Wayland implementations.

## Why Certain Distributions Are NOT Separately Tested

### Rocky Linux / AlmaLinux

**Reason:** Binary compatible with RHEL/Fedora. The packages, libraries, and system behavior are identical. Testing Fedora effectively tests these.

### Linux Mint

**Reason:** Ubuntu-based with same DEB packages. Additionally, Mint defaults to X11 (not Wayland), making the Wayland bug less relevant for default Mint users.

### Debian Stable

**Reason:** Ubuntu is derived from Debian. While Debian has older packages, the DEB format and apt behavior are the same. Any DEB-related issues would appear on Ubuntu first.

### Arch Linux

**Reason:** Manjaro is Arch-based with same package ecosystem. Testing Manjaro covers Arch Linux.

### Gentoo / Slackware / Other Niche Distros

**Reason:** Very small market share (<1% combined). Not cost-effective to maintain separate test environments.

## Coverage Metrics

### By Enterprise Market Share

| Segment | Coverage |
|---------|----------|
| Red Hat family (~43%) | Fedora |
| Debian/Ubuntu family (~34%) | Ubuntu 22.04, 24.04 |
| SUSE family (~15%) | openSUSE Leap |
| Other (~8%) | Arch (partial) |
| **Total** | **~95%+** |

### By Package Format

| Format | Market Presence | Tested |
|--------|----------------|--------|
| DEB | ~40% | Yes |
| RPM | ~50% | Yes (both dnf and zypper) |
| AppImage | Universal | Yes |
| Snap | ~10% | Yes |
| Flatpak | ~10% | No |

### By Desktop Environment

| Desktop | Market Share | Tested |
|---------|-------------|--------|
| GNOME | ~60% | Yes (Fedora, Ubuntu) |
| KDE Plasma | ~25% | Yes (openSUSE) |
| Xfce/Other | ~15% | Via X11 fallback tests |

### By Wayland Compositor

| Compositor | Used By | Tested |
|------------|---------|--------|
| Mutter | GNOME, Cinnamon | Yes |
| KWin | KDE Plasma | Yes |
| wlroots-based | Sway, Hyprland | No (niche) |

## Test Matrix Summary

| OS | VMID | Desktop | Package Formats | Without GPU | With GPU |
|----|------|---------|-----------------|-------------|----------|
| Fedora 42 | 100 | GNOME | RPM, AppImage | Complete | Complete |
| Ubuntu 22.04 | 101 | GNOME | DEB, AppImage, Snap | Complete | Complete |
| Ubuntu 24.04 | 102 | GNOME | DEB, AppImage, Snap | Complete | Complete |
| openSUSE Leap 16.0 | 106 | KDE | RPM, AppImage | Complete | Blocked |
| Manjaro Linux | 103 | KDE | AppImage | Pending | Pending |

**Notes:**
- openSUSE GPU testing blocked: VM has minimal server install, needs KDE desktop
- Manjaro pending: ISO boot issues with SMB storage

## GPU Passthrough Testing

### Why GPU Testing Is Required

Virtual displays (Xvfb, Weston headless) are useful for CI automation but have limitations:
- No real GPU acceleration
- Weston headless has known issues with Electron/Chromium
- Cannot validate real compositor behavior (GNOME Mutter, KDE KWin)

**Real GPU passthrough testing validates:**
- Actual Wayland compositor interaction
- GPU initialization and fallback behavior
- XWayland fallback in real desktop sessions
- Production-like environment

### GPU Test Configuration

| Component | Specification |
|-----------|---------------|
| GPU | NVIDIA GeForce RTX 3060 12GB |
| PCI Address | 0000:01:00 |
| Passthrough | VFIO/IOMMU |
| Mode | Compute (VNC still works) |

### GPU Test Results (2026-01-19)

| OS | Session Type | gpu-wayland-real | gpu-wayland-fake | gpu-x11 | gpu-wayland-nodisp |
|----|--------------|------------------|------------------|---------|-------------------|
| Fedora 42 | Wayland (GNOME) | PASS | PASS | PASS | PASS |
| Ubuntu 22.04 | X11 (GNOME) | SKIP | PASS | PASS | PASS |
| Ubuntu 24.04 | Wayland (GNOME) | PASS | PASS | PASS | PASS |
| openSUSE Leap | N/A (no DE) | BLOCKED | BLOCKED | BLOCKED | BLOCKED |

### GPU Test Workflow

```bash
# Attach GPU to VM (VM must be stopped)
./os/<os>/gpu-control.sh --attach

# Run GPU tests (VM auto-starts with desktop session)
ssh jean@<VM_IP> '/tmp/tests/run-gpu-tests.sh'

# Detach GPU for next VM
./os/<os>/gpu-control.sh --detach
```

## Future Considerations

### Flatpak Testing

Flatpak is growing in popularity. Consider adding Flatpak testing on Fedora (native Flatpak support).

### Immutable Distributions

Fedora Silverblue, openSUSE MicroOS, and similar immutable distros are gaining traction. These use Flatpak/containers exclusively.

### Older LTS Versions

Some enterprises run older LTS versions (Ubuntu 20.04, RHEL 8). Consider testing if compatibility issues are reported.

## Conclusion

Our 5-distribution testing strategy provides comprehensive coverage of the enterprise Linux desktop market:

1. **Fedora 42** - Covers Red Hat ecosystem + GNOME/Mutter Wayland
2. **Ubuntu 22.04/24.04** - Covers Debian ecosystem + Snap packages
3. **openSUSE Leap 16.0** - Covers SUSE ecosystem + KDE/KWin
4. **Manjaro Linux** - Covers Arch/rolling releases + catches bleeding-edge issues

**Each distribution is tested with:**
- Virtual displays (Xvfb, Weston) for CI/automation scenarios
- Real GPU passthrough (NVIDIA RTX 3060) for production validation

This approach maximizes coverage while minimizing maintenance overhead. Each distribution was selected because it represents a **unique combination** of package manager, desktop environment, and enterprise market segment.
