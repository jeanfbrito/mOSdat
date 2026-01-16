# Case Studies

## 1. Wayland/X11 Crash Fix (January 2026)

### The Problem

Electron apps crash with `SIGSEGV` (exit code 139) when `WAYLAND_DISPLAY` environment variable points to a non-existent socket.

**Related:**
- [PR #3171](https://github.com/RocketChat/Rocket.Chat.Electron/pull/3171) - The fix
- [Issue #3154](https://github.com/RocketChat/Rocket.Chat.Electron/issues/3154) - Bug report

### Real User Scenarios That Trigger This

| Scenario | What Happens |
|----------|--------------|
| Compositor crash | GNOME Shell crashes, socket deleted, env vars remain |
| Session mismatch | Logged into X11, but WAYLAND_DISPLAY leaked from previous session |
| Misconfigured system | Partial Wayland install, vars set but nothing works |
| Terminal from SSH | App launched while desktop session has different display |

### The Fix

Wrapper script (`rocketchat-desktop`) that:
1. Checks if `WAYLAND_DISPLAY` is set
2. Verifies the socket actually exists and is accessible
3. If Wayland is broken, forces X11 fallback with `--ozone-platform=x11`
4. Only then launches the actual Electron binary

### Test Results

#### Fedora 42 (Wayland Native)

| Scenario | Old v4.11.0 | New v4.11.1 |
|:---------|:-----------:|:-----------:|
| Real Wayland | ✅ PASS | ✅ PASS |
| Fake Wayland socket | ❌ SEGFAULT (139) | ✅ PASS |
| Missing WAYLAND_DISPLAY | ❌ SEGFAULT (139) | ✅ PASS |
| X11 fallback | ❌ SEGFAULT (139) | ✅ PASS |

#### Ubuntu 22.04 (X11 Default)

| Scenario | Old v4.11.0 | New v4.11.1 |
|:---------|:-----------:|:-----------:|
| Real X11 | ✅ PASS | ✅ PASS |
| Fake Wayland socket | ✅ PASS* | ✅ PASS |

*Ubuntu 22.04 defaults to X11, so the Wayland bug doesn't manifest in normal usage.

### Git References

| Version | Git Ref | Description |
|---------|---------|-------------|
| Old (broken) | `22c1646` | v4.11.0 - crashes on misconfigured Wayland |
| New (fixed) | `fix-x11-ubuntu2204` | v4.11.1 - wrapper prevents crash |

### Conclusion

Fix validated. The wrapper script successfully detects invalid Wayland environments and forces X11 fallback, preventing segmentation faults.

See [results/](../results/) for detailed test reports.
