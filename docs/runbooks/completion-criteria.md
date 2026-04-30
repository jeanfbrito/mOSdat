# Completion Criteria

← Back to [AGENTS.md](../../AGENTS.md)

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

### 2. GPU Configuration Testing

**Each package format must be tested in BOTH configurations:**

| Config | Description | Test Scripts |
|--------|-------------|--------------|
| **Without GPU** | Virtual displays (Xvfb, Weston headless) | `shared/scenarios/smoke-linux/run-all.sh` |
| **With GPU** | Real NVIDIA RTX 3060 via VFIO passthrough | `shared/scenarios/smoke-linux/run-gpu-tests.sh` |

**Why test ALL packages with GPU?** Different package formats may have different:
- Library bundling (system vs bundled libs)
- Sandbox configurations (especially Snap)
- Environment variable handling
- Desktop integration paths

Testing all packages ensures no format-specific regressions.

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

### Test Count Per OS (Full Matrix)

| OS | Without-GPU | With-GPU | Total |
|----|-------------|----------|-------|
| Fedora 42 | 2 pkg × 5 = 10 | 2 pkg × 4 = 8 | **18** |
| Ubuntu 22.04 | 3 pkg × 5 = 15 | 3 pkg × 4 = 12 | **27** |
| Ubuntu 24.04 | 3 pkg × 5 = 15 | 3 pkg × 4 = 12 | **27** |
| openSUSE | 2 pkg × 5 = 10 | 2 pkg × 4 = 8 | **18** |
| Manjaro | 1 pkg × 5 = 5 | 1 pkg × 4 = 4 | **9** |
| **TOTAL** | 55 | 44 | **99** |

**All package formats must be tested with GPU** to catch format-specific issues.

### Completion Checklist

An OS is **NOT DONE** until:
- [ ] All package formats installed and tested
- [ ] All tests pass WITHOUT GPU (virtual displays)
- [ ] All tests pass WITH GPU (real NVIDIA passthrough)
- [ ] Results documented in `results/` directory
- [ ] TEST-MATRIX.md updated with results

**DO NOT mark an OS as "done" until BOTH GPU configurations are tested.**
