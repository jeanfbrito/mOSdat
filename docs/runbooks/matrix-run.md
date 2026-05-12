# Matrix Run

← Back to [AGENTS.md](../../AGENTS.md)

## Full Test Matrix Execution (Parallel Workflow)

When running a full test matrix for a release (e.g., `4.12.0-alpha.2`), follow this parallelization strategy:

### Execution Waves

```
Wave 1: Pre-flight checks (verify VMs, backup dist/)
Wave 2: Build all packages (sequential - shared build env)
Wave 3: WITHOUT GPU tests - ALL 5 VMs IN PARALLEL
Wave 4-8: WITH GPU tests - SEQUENTIAL (only one GPU)
Wave 9: Generate report
```

### Parallel Opportunities

| Phase | Parallel? | Why |
|-------|-----------|-----|
| Build | NO | Shared dist/ folder |
| Without GPU tests | YES (5x) | Independent VMs, no GPU needed |
| With GPU tests | NO | Single GPU, must rotate |

### Quick Commands

```bash
# Full test matrix for a release — Python runner is canonical
cd ${FRAMEWORK_PATH}

# Run full matrix (build + deploy + test all VMs):
mosdat run examples/rocketchat.toml

# Resume after interruption:
mosdat run examples/rocketchat.toml --resume

# Skip build phase (use pre-built packages):
mosdat run examples/rocketchat.toml --skip-build

# Test a single VM:
mosdat run examples/rocketchat.toml --only fedora42

# Quick test with a pre-built package:
mosdat test examples/rocketchat.toml /path/to/package.rpm --vms fedora42

# Watch progress:
mosdat live --port 8082 --results results --config examples/rocketchat.toml
```

### VM Quick Reference

| OS | VMID | IP | Packages | Desktop |
|----|------|-----|----------|---------|
| Fedora 42 | 100 | 192.168.13.80 | RPM, AppImage | GNOME Wayland |
| Ubuntu 22.04 | 101 | 192.168.13.81 | DEB, AppImage, Snap | GNOME X11 |
| Ubuntu 24.04 | 102 | 192.168.13.82 | DEB, AppImage, Snap | GNOME Wayland |
| Manjaro | 103 | 192.168.13.83 | AppImage | KDE Wayland |
| openSUSE | 106 | 192.168.13.84 | RPM, AppImage | KDE X11 |

### Results Structure

```
results/smoke/<date>_full-matrix-<version>/
├── packages.txt           # List of built packages
├── fedora42-rpm-no-gpu.log
├── fedora42-rpm-gpu.log
├── fedora42-appimage-no-gpu.log
├── fedora42-appimage-gpu.log
├── ubuntu2204-deb-no-gpu.log
├── ...
└── REPORT.md              # Summary report
```

---

## Complete Test Runbook

Use the Python CLI and TOML config as the source of truth. Do not put local
passwords or Proxmox tickets in this document; keep credentials in your local
environment or config file.

### Pre-flight

```bash
cd ${FRAMEWORK_PATH:-/home/jean/projects/linux-testing/mOSdat}
mosdat validate examples/rocketchat.toml
mosdat list-vms examples/rocketchat.toml
```

For interactive/live runs, start the dashboard first:

```bash
mosdat live --port 8082 --results results --config examples/rocketchat.toml
```

### Full Matrix

```bash
# Build, deploy, and test all configured VMs/packages
mosdat run examples/rocketchat.toml

# Resume after interruption
mosdat run examples/rocketchat.toml --resume

# Reuse existing packages
mosdat run examples/rocketchat.toml --skip-build

# Restrict scope
mosdat run examples/rocketchat.toml --only fedora42
```

### Functional VLM Runs

```bash
mosdat functional examples/rocketchat.toml --vms fedora42,ubuntu2404 --test rocketchat-smoke-linux
mosdat functional examples/rocketchat.toml --vms ubuntu2404 --test rocketchat-smoke-linux --save-screenshots
mosdat functional examples/rocketchat.toml --vms ubuntu2404 --test rocketchat-smoke-linux --from-step 3 --until-step 5

# Full Windows smoke with replay recording (10 FPS baseline)
mosdat functional examples/rocketchat.toml --vms windows11 --test rocketchat-smoke \
  --skip-workspace-check --skip-model-check --skip-warmup \
  --record-session --record-fps 10 --timeout 1800
```

Functional results are written under:

```
results/functional/<timestamp>_<test>/<vm>/
├── events.jsonl
├── *.png
├── recording/session.mp4
├── recording/session.gif      # optional (--record-gif)
└── summary/report artifacts
```

### Agent Authoring

Use the author API to build or refine a scenario while watching the VM:

```bash
mosdat author --url http://127.0.0.1:8082 vms
mosdat author --url http://127.0.0.1:8082 start --vm ubuntu2404
mosdat author --url http://127.0.0.1:8082 capture --session <session-id>
mosdat author --url http://127.0.0.1:8082 localize --session <session-id> --prompt "help tooltip"
mosdat author --url http://127.0.0.1:8082 action --session <session-id> --kind hover --json '{"x":5,"y":6,"prompt":"help tooltip"}'
mosdat author --url http://127.0.0.1:8082 validate --session <session-id>
mosdat author --url http://127.0.0.1:8082 export --session <session-id> --name tooltip-flow
```

Open `http://localhost:8082/author` for the browser version of the same flow.

### Reporting

```bash
mosdat dashboard --root results --output results/functional/dashboard.html
mosdat report --root results
```

Use `docs/runbooks/agent-monitoring.md` for long-running run monitoring and
staleness checks.
