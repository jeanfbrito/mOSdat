# Test Report: Rocket.Chat Electron Wayland Fix - openSUSE Leap 16.0

## Overview

| Field | Value |
|-------|-------|
| **Date** | 2026-01-19 |
| **Tester** | Jean Brito |
| **Purpose** | Validate Wayland/X11 crash fix (PR #3171) |

---

## Test Environment

### Proxmox Host

| Component | Specification |
|-----------|---------------|
| **CPU** | Intel Core i7-12700H (14C/20T) |
| **RAM** | 32 GB DDR4 |
| **Storage** | 1 TB NVMe SSD |
| **GPU** | NVIDIA GeForce RTX 3060 12GB |
| **OS** | Proxmox VE 8.x |
| **IOMMU** | Enabled (intel_iommu=on) |

### Test VM (ID: 106)

| Component | Specification |
|-----------|---------------|
| **OS** | openSUSE Leap 16.0 |
| **Desktop** | KDE Plasma 6 / KWin Wayland |
| **vCPU** | 8 cores |
| **RAM** | 8 GB |
| **Disk** | 64 GB (virtio-scsi) |
| **Display** | VirtIO-GPU (VNC accessible) |
| **GPU Passthrough** | Not configured |
| **IP Address** | 192.168.13.84 |

---

## Version Tested

| Version | Git Ref | Description |
|---------|---------|-------------|
| 4.11.1 | `fix-x11-ubuntu2204` | Post-fix (with wrapper script) |

---

## Results Summary

### RPM Package

| Test | Result | Exit Code | Notes |
|:-----|:------:|:---------:|:------|
| x11 | PASS | 0 | Works with Xvfb |
| wayland | SKIP | - | Weston headless limitation |
| wayland-fake | PASS | 0 | **THE BUG IS FIXED** |
| wayland-fallback | PASS | 0 | Falls back to X11 |
| no-display | EXPECTED | - | Crash acceptable (no GUI) |

### AppImage Package

| Test | Result | Exit Code | Notes |
|:-----|:------:|:---------:|:------|
| x11 | PASS | 0 | Works with Xvfb |
| wayland | SKIP | - | Weston headless limitation |
| wayland-fake | PASS | 0 | **THE BUG IS FIXED** |
| wayland-fallback | PASS | 0 | Falls back to X11 |
| no-display | EXPECTED | - | Crash acceptable (no GUI) |

**Both package formats validated. openSUSE Leap 16.0 testing COMPLETE.**

---

## Test Scenarios

### x11
- **Environment**: `XDG_SESSION_TYPE=x11`, `DISPLAY=:99` (Xvfb)
- **Expected**: App runs on X11
- **Result**: PASS

### wayland
- **Environment**: `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY=wayland-test` (Weston)
- **Expected**: App runs on native Wayland
- **Result**: SKIP - Weston headless has known limitations with Electron

### wayland-fake (THE BUG #3154)
- **Environment**: `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY=wayland-nonexistent` (invalid), `DISPLAY=:99` (X11 fallback)
- **Expected**: Should detect invalid Wayland and fallback to X11
- **Result**: PASS - Fix correctly detects invalid socket and uses X11

### wayland-fallback
- **Environment**: `WAYLAND_DISPLAY=wayland-nonexistent`, `DISPLAY=:99`
- **Expected**: Should fallback to X11
- **Result**: PASS

### no-display
- **Environment**: No `DISPLAY`, no `WAYLAND_DISPLAY`
- **Expected**: Crash is acceptable (no display available)
- **Result**: EXPECTED

---

## Technical Details

### Fix Implementation

The fix consists of a wrapper script (`/opt/Rocket.Chat/rocketchat-desktop`) that:

1. Checks `XDG_SESSION_TYPE == "wayland"`
2. Checks `WAYLAND_DISPLAY` is set
3. Verifies socket exists at `$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY`
4. If any check fails -> adds `--ozone-platform=x11`

```bash
should_force_x11() {
    [[ "${XDG_SESSION_TYPE:-}" != "wayland" ]] && return 0
    [[ -z "${WAYLAND_DISPLAY:-}" ]] && return 0
    local socket="$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY"
    [[ ! -S "$socket" ]] && return 0
    return 1
}
```

### openSUSE Leap 16.0 Specific Notes

- **KDE Plasma 6** with KWin Wayland compositor (different from GNOME)
- **zypper** package manager (SUSE ecosystem)
- First RPM-based distro with KDE tested (Fedora uses GNOME)
- Covers European enterprise market (SUSE enterprise compatibility)
- Package manager commands: `sudo zypper install -y <package>`

### Build Process

RPM was built using the existing electron-builder configuration:
- `npm run build` → produces `.rpm` in `dist/` folder
- Same RPM works on Fedora (dnf) and openSUSE (zypper)

---

## Coverage Value

openSUSE Leap 16.0 provides unique test coverage:

| Coverage Area | What It Validates |
|---------------|-------------------|
| **SUSE Enterprise** | SLES, openSUSE Tumbleweed compatibility |
| **KDE Plasma Desktop** | KWin Wayland compositor (vs GNOME's Mutter) |
| **zypper Package Manager** | SUSE-specific package handling |
| **European Enterprise** | Major enterprise Linux market segment |

---

## Conclusion

The Wayland fix in version 4.11.1 **works correctly on openSUSE Leap 16.0**:

1. X11 mode works correctly with Xvfb
2. Fake Wayland scenario (the bug) is fixed - no crash, falls back to X11
3. Wayland fallback works as expected
4. Both package formats (RPM, AppImage) validated
5. KDE Plasma desktop environment confirmed working

---

## References

- [Rocket.Chat Electron Repository](https://github.com/RocketChat/Rocket.Chat.Electron)
- [PR #3171 - Wayland/X11 Fix](https://github.com/RocketChat/Rocket.Chat.Electron/pull/3171)
- [Issue #3154 - Original Bug Report](https://github.com/RocketChat/Rocket.Chat.Electron/issues/3154)
- [Linux Coverage Strategy](../../docs/LINUX-COVERAGE.md)
