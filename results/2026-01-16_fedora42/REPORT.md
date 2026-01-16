# Test Report: Rocket.Chat Electron Wayland Fix

## Overview

| Field | Value |
|-------|-------|
| **Date** | 2026-01-16 |
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

### Test VM (ID: 100)

| Component | Specification |
|-----------|---------------|
| **OS** | Fedora 42 Workstation |
| **Desktop** | GNOME 47 / Wayland |
| **vCPU** | 8 cores |
| **RAM** | 8 GB |
| **Disk** | 64 GB (virtio-scsi) |
| **Display** | VirtIO-GPU (VNC accessible) |
| **GPU Passthrough** | RTX 3060 (tested with/without) |

---

## Versions Tested

| Version | Git Ref | Description |
|---------|---------|-------------|
| 4.11.0 | `22c1646` | Pre-fix (problematic) |
| 4.11.1 | `fix-x11-ubuntu2204` | Post-fix (with wrapper script) |

---

## Results Summary

### Old Version (4.11.0) - Without Fix

| Test | No GPU | With GPU | Exit Code |
|:-----|:------:|:--------:|:---------:|
| wayland-real | ✅ PASS | ✅ PASS | 0 |
| wayland-fake | ❌ FAIL | ❌ FAIL | 139 (SIGSEGV) |
| wayland-nodisp | ❌ FAIL | ❌ FAIL | 139 (SIGSEGV) |
| x11 | ❌ FAIL | ❌ FAIL | 139 (SIGSEGV) |

### New Version (4.11.1) - With Fix

| Test | No GPU | With GPU | Exit Code |
|:-----|:------:|:--------:|:---------:|
| wayland-real | ✅ PASS | ✅ PASS | 0 |
| wayland-fake | ✅ PASS | ✅ PASS | 0 |
| wayland-nodisp | ✅ PASS | ✅ PASS | 0 |
| x11 | ✅ PASS | ✅ PASS | 0 |

---

## Test Scenarios

### wayland-real
- **Environment**: `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY=wayland-0` (valid socket)
- **Expected**: App runs on native Wayland
- **Result**: ✅ Both versions pass

### wayland-fake
- **Environment**: `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY=wayland-fake-nonexistent`
- **Expected**: Old crashes, new falls back to X11
- **Result**: 
  - Old: ❌ SIGSEGV (exit 139)
  - New: ✅ X11 fallback works

### wayland-nodisp
- **Environment**: `XDG_SESSION_TYPE=wayland`, `WAYLAND_DISPLAY` unset
- **Expected**: Old crashes, new falls back to X11
- **Result**:
  - Old: ❌ SIGSEGV (exit 139)
  - New: ✅ X11 fallback works

### x11
- **Environment**: `XDG_SESSION_TYPE=x11`, no Wayland
- **Expected**: App runs on X11
- **Result**:
  - Old: ❌ SIGSEGV (exit 139) - no DISPLAY fallback
  - New: ✅ Runs on XWayland (:0)

---

## Technical Details

### Fix Implementation

The fix consists of a wrapper script (`/opt/Rocket.Chat/rocketchat-desktop`) that:

1. Checks `XDG_SESSION_TYPE == "wayland"`
2. Checks `WAYLAND_DISPLAY` is set
3. Verifies socket exists at `$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY`
4. If any check fails → adds `--ozone-platform=x11`

```bash
should_force_x11() {
    [[ "${XDG_SESSION_TYPE:-}" != "wayland" ]] && return 0
    [[ -z "${WAYLAND_DISPLAY:-}" ]] && return 0
    local socket="$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY"
    [[ ! -S "$socket" ]] && return 0
    return 1
}
```

### Why Wrapper Script?

Chromium's Ozone layer initializes **before** Electron JavaScript runs. By the time `app.commandLine.appendSwitch()` executes, Chromium has already attempted (and failed) to connect to Wayland, causing a fatal crash.

The wrapper script runs **before** the Electron binary, allowing us to pass the `--ozone-platform=x11` flag at startup.

---

## GPU Passthrough Notes

| Setting | Value |
|---------|-------|
| **Mode** | Compute (not primary) |
| **Config** | `hostpci0=0000:01:00,pcie=1` |
| **VNC** | Remains accessible |
| **Effect on Fix** | None (identical results) |

---

## Reproduction Steps

```bash
# 1. Install old version (4.11.0)
sudo dnf install rocketchat-4.11.0.rpm

# 2. Trigger crash
export XDG_SESSION_TYPE=wayland
export WAYLAND_DISPLAY=fake-socket
export XDG_RUNTIME_DIR=/run/user/1000
/opt/Rocket.Chat/rocketchat-desktop
# Result: Segmentation fault (core dumped)

# 3. Install new version (4.11.1)  
sudo dnf install rocketchat-4.11.1.rpm

# 4. Verify fix
/opt/Rocket.Chat/rocketchat-desktop
# Result: App runs (falls back to X11)
```

---

## Conclusion

The Wayland fix in version 4.11.1 **successfully prevents all crash scenarios** by:

1. ✅ Detecting invalid Wayland configurations before Chromium initializes
2. ✅ Forcing X11 fallback when Wayland is misconfigured
3. ✅ Allowing native Wayland when configuration is valid
4. ✅ Working identically with and without GPU passthrough

---

## References

- [Rocket.Chat Electron Repository](https://github.com/RocketChat/Rocket.Chat.Electron)
- [PR #3171 - Wayland/X11 Fix](https://github.com/RocketChat/Rocket.Chat.Electron/pull/3171)
- [Issue #3154 - Original Bug Report](https://github.com/RocketChat/Rocket.Chat.Electron/issues/3154)
