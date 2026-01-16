# Case Studies

## 1. Wayland/X11 Crash Fix (January 2026)

**Problem**: Electron apps crash with `SIGSEGV` (exit code 139) when `WAYLAND_DISPLAY` environment variable points to a non-existent socket.

**Related**:
- [PR #3171](https://github.com/RocketChat/Rocket.Chat.Electron/pull/3171) - The fix
- [Issue #3154](https://github.com/RocketChat/Rocket.Chat.Electron/issues/3154) - Original bug report

**Solution**: A wrapper script that detects invalid Wayland configurations and forces X11 fallback before launching the Electron app.

### Test Matrix

Tested on Fedora 42 (VM ID 100) with GNOME/Wayland:

| Test Scenario | Description | Old v4.11.0 | New v4.11.1 |
|:--------------|:------------|:-----------:|:-----------:|
| `wayland-real` | Valid Wayland session | PASS | PASS |
| `wayland-fake` | Fake `WAYLAND_DISPLAY` pointing to non-existent socket | SEGFAULT (139) | PASS |
| `wayland-nodisp` | `WAYLAND_DISPLAY` unset | SEGFAULT (139) | PASS |
| `x11` | X11 session via XWayland | SEGFAULT (139) | PASS |

### Git References

| Version | Git Ref | Description |
|---------|---------|-------------|
| Old (broken) | `22c1646` | v4.11.0 - crashes on misconfigured Wayland |
| New (fixed) | `fix-x11-ubuntu2204` | v4.11.1 - wrapper prevents crash |

### Conclusion

Fix validated. The wrapper script successfully detects invalid Wayland environments and forces X11 fallback, preventing the segmentation fault.

See [results/2026-01-16_fedora42/REPORT.md](../results/2026-01-16_fedora42/REPORT.md) for the full test report.
