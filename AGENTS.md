# AI Agent Instructions

## TOP RULE: NEVER BLOCK ON WAITS

**NEVER use long `sleep` commands or blocking waits while the user sits idle.**

When waiting for VMs, builds, or any async operation:
1. Fire background agents to monitor state
2. Continue with useful work in parallel
3. Check results when needed, don't block

```bash
# WRONG - blocks user
sleep 45 && check_status

# RIGHT - use background monitoring
background_task(agent="explore", prompt="Monitor VM 103 until IP appears...")
# Continue other work immediately
```

**Time is precious. Blocking waits waste user time.**

---

## Project Overview

Test framework for Rocket.Chat Electron Linux builds, specifically the Wayland/X11 crash fix (PR #3171).

## CRITICAL: Proxmox is a Separate Machine

**Proxmox runs on a DIFFERENT machine (192.168.13.85). All hardware info must be queried via API.**

```bash
# Query Proxmox GPU via API (NEVER use local lspci)
TICKET=$(curl -k -s -d "username=root@pam&password=cb6wist3" \
  "https://192.168.13.85:8006/api2/json/access/ticket" | jq -r '.data.ticket')
curl -k -s -b "PVEAuthCookie=$TICKET" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/hardware/pci" | \
  jq '.data[] | select(.vendor_name | test("NVIDIA"))'
```

**Proxmox GPU:** RTX 3060 at PCI `0000:01:00`

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
| **Without GPU** | Virtual displays (Xvfb, Weston headless) | `shared/tests/run-all.sh` |
| **With GPU** | Real NVIDIA RTX 3060 via VFIO passthrough | `shared/tests/run-gpu-tests.sh` |

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

---

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

### GPU Constraint

**CRITICAL:** Only ONE VM can have the GPU attached at a time.

```bash
# Before attaching to new VM, always detach from current
./os/<current-os>/gpu-control.sh --detach
./os/<new-os>/gpu-control.sh --attach
```

### Parallel Opportunities

| Phase | Parallel? | Why |
|-------|-----------|-----|
| Build | NO | Shared dist/ folder |
| Without GPU tests | YES (5x) | Independent VMs, no GPU needed |
| With GPU tests | NO | Single GPU, must rotate |

### Quick Commands

```bash
# Full test matrix for a release
cd /home/jean/projects/linux-testing/test-framework

# 1. Build all packages
cd /home/jean/projects/linux-testing/Rocket.Chat.Electron
git checkout <tag>
yarn install && yarn build
yarn electron-builder --publish never --linux rpm deb AppImage snap

# 2. Create results directory
mkdir -p results/$(date +%Y-%m-%d)_full-matrix-<version>

# 3. Deploy and test (WITHOUT GPU - can run in parallel)
# Fedora:
./os/fedora-42/deploy.sh && ./os/fedora-42/test.sh all

# 4. GPU tests (SEQUENTIAL - one at a time)
./os/fedora-42/gpu-control.sh --attach
# wait for boot, then:
ssh jean@192.168.13.80 '/tmp/tests/run-gpu-tests.sh'
./os/fedora-42/gpu-control.sh --detach
# repeat for next VM...
```

### VM Quick Reference

| OS | VMID | IP | Packages | Desktop |
|----|------|-----|----------|---------|
| Fedora 42 | 100 | 192.168.13.80 | RPM, AppImage | GNOME Wayland |
| Ubuntu 22.04 | 101 | 192.168.13.81 | DEB, AppImage, Snap | GNOME X11 |
| Ubuntu 24.04 | 102 | 192.168.13.82 | DEB, AppImage, Snap | GNOME Wayland |
| Manjaro | 103 | 192.168.13.83 | AppImage | KDE Wayland |
| openSUSE | 106 | 192.168.13.84 | RPM, AppImage | KDE X11 |

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

### Exit Code Interpretation

| Code | Meaning | Result |
|------|---------|--------|
| 0 | Clean exit | PASS |
| 124 | Timeout (app ran 10s) | PASS (didn't crash) |
| 139 | SIGSEGV | FAIL (the bug) |
| 134 | SIGABRT | FAIL |

### GPU Test Result Interpretation (CRITICAL)

**Not all GPU test failures indicate a problem with the crash fix.**

| Test | What It Validates | Failure Meaning |
|------|-------------------|-----------------|
| `wayland-fake` | **THE BUG** - crash fix | FAIL = regression, MUST investigate |
| `wayland-fallback` | X11 fallback mechanism | FAIL = regression, investigate |
| `gpu-wayland` | Real Wayland + GPU | FAIL = likely GPU/driver issue, not the fix |
| `gpu-x11` | Real X11 + GPU | FAIL = likely XAUTHORITY/sandbox issue |

**Known Non-Blocking Failures:**

| Scenario | Affected | Cause | Action |
|----------|----------|-------|--------|
| Snap + GPU crashes | Ubuntu 24.04 Snap | Snap sandbox blocks GPU access | Document as known limitation |
| gpu-wayland SKIP | Ubuntu 22.04, openSUSE | X11 session, no Wayland socket | Expected - these distros default to X11 |
| gpu-x11 FAIL on AppImage | Fedora, openSUSE | XAUTHORITY not found | Investigate, but not crash fix related |

**Decision Rule:**
- `wayland-fake` FAIL on ANY package → **BLOCKER** (the crash fix is broken)
- Other GPU test FAIL → Document, but not a release blocker if `wayland-fake` passes

### Results Structure

```
results/<date>_full-matrix-<version>/
├── packages.txt           # List of built packages
├── fedora42-rpm-no-gpu.log
├── fedora42-rpm-gpu.log
├── fedora42-appimage-no-gpu.log
├── fedora42-appimage-gpu.log
├── ubuntu2204-deb-no-gpu.log
├── ...
└── REPORT.md              # Summary report
```

### GPU Passthrough Troubleshooting

If GPU tests fail with "No NVIDIA GPU visible":

1. **Check GPU PCI address on Proxmox host:**
   ```bash
   lspci | grep -i nvidia
   # Example output: 03:00.0 VGA compatible controller: NVIDIA...
   ```

2. **Verify IOMMU enabled:**
   ```bash
   dmesg | grep -i iommu
   cat /proc/cmdline  # Should contain intel_iommu=on or amd_iommu=on
   ```

3. **Check vfio-pci driver binding:**
   ```bash
   lspci -nnk -s 03:00  # Should show: Kernel driver in use: vfio-pci
   ```

4. **Update GPU_PCI_ADDRESS in shared/config.sh if needed:**
   ```bash
   export GPU_PCI_ADDRESS="0000:03:00"  # Match your actual GPU address
   ```

5. **Verify VM config in Proxmox:**
   ```bash
   # Check if hostpci0 is set
   curl -k -s -b "PVEAuthCookie=$TICKET" \
     "https://$PROXMOX_HOST:8006/api2/json/nodes/pve/qemu/$VMID/config" | jq '.data.hostpci0'
   ```

---

## Complete Test Runbook (Copy-Paste Ready)

This section contains ALL commands needed to run a full test matrix. No discovery required.

### Environment Variables

```bash
# Source these at the start of any test session
export PROXMOX_HOST=192.168.13.85
export PROXMOX_USER=root@pam
export PROXMOX_PASSWORD=cb6wist3
export REPO_PATH=/home/jean/projects/linux-testing/Rocket.Chat.Electron
export FRAMEWORK_PATH=/home/jean/projects/linux-testing/test-framework
export VM_USER=jean
export VM_PASSWORD=cb6wist3
```

### Pre-flight Checks

```bash
# 1. Verify all VMs are reachable
for ip in 192.168.13.80 192.168.13.81 192.168.13.82 192.168.13.83 192.168.13.84; do
  ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no jean@$ip "echo OK" || echo "FAILED: $ip"
done

# 2. Get Proxmox API ticket
TICKET=$(curl -k -s -d "username=root@pam&password=cb6wist3" \
  "https://192.168.13.85:8006/api2/json/access/ticket" | jq -r '.data.ticket')

# 3. Check all VM statuses
for vmid in 100 101 102 103 106; do
  STATUS=$(curl -k -s -b "PVEAuthCookie=$TICKET" \
    "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/$vmid/status/current" | jq -r '.data.status')
  echo "VMID $vmid: $STATUS"
done

# 4. Check GPU not attached to any VM
curl -k -s -b "PVEAuthCookie=$TICKET" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/100/config" | jq '.data.hostpci0'
# Should return null

# 5. Create results directory
VERSION="4.12.0-alpha.2"  # Change this
mkdir -p ${FRAMEWORK_PATH}/results/$(date +%Y-%m-%d)_full-matrix-${VERSION}
```

### Build Commands

```bash
cd /home/jean/projects/linux-testing/Rocket.Chat.Electron

# Checkout and build
git fetch --tags
git checkout 4.12.0-alpha.2  # Change version as needed
yarn install
yarn build

# Build ALL Linux packages (takes ~12 min)
yarn electron-builder --publish never --linux rpm deb AppImage snap

# Verify packages
ls -lh dist/*.{rpm,deb,AppImage,snap}
# Expected: ~83MB each for rpm/deb, ~126MB AppImage, ~106MB snap
```

### Deploy Commands by OS

**Fedora 42 (192.168.13.80) - RPM:**
```bash
scp dist/rocketchat-*.rpm jean@192.168.13.80:/tmp/
ssh jean@192.168.13.80 "sudo dnf install -y /tmp/rocketchat-*.rpm"
```

**Ubuntu 22.04/24.04 (192.168.13.81/82) - DEB:**
```bash
scp dist/rocketchat-*.deb jean@192.168.13.81:/tmp/
ssh jean@192.168.13.81 "sudo apt install -y /tmp/rocketchat-*.deb"
```

**Ubuntu 22.04/24.04 - Snap:**
```bash
scp dist/rocketchat-*.snap jean@192.168.13.81:/tmp/
ssh jean@192.168.13.81 "sudo snap install --dangerous /tmp/rocketchat-*.snap"
```

**openSUSE (192.168.13.84) - RPM:**
```bash
scp dist/rocketchat-*.rpm jean@192.168.13.84:/tmp/
ssh jean@192.168.13.84 "sudo zypper install -y --allow-unsigned-rpm /tmp/rocketchat-*.rpm"
```

**ALL OSes - AppImage:**
```bash
scp dist/rocketchat-*.AppImage jean@<VM_IP>:/tmp/
ssh jean@<VM_IP> "chmod +x /tmp/rocketchat-*.AppImage"
```

### Test Commands

**Transfer test scripts (required for each VM):**
```bash
scp -r ${FRAMEWORK_PATH}/shared/tests jean@<VM_IP>:/tmp/
```

### APP_PATH Environment Variable

The test scripts use `APP_PATH` to locate the application:

| Package | APP_PATH Value |
|---------|----------------|
| RPM/DEB | (not needed - uses default `/opt/Rocket.Chat/rocketchat-desktop`) |
| AppImage | `/tmp/rocketchat-<version>-linux-x86_64.AppImage` |
| Snap | `/snap/bin/rocketchat-desktop` |

**Run WITHOUT GPU tests:**
```bash
# For installed packages (RPM/DEB)
ssh jean@<VM_IP> "/tmp/tests/run-all.sh"

# For AppImage
ssh jean@<VM_IP> "APP_PATH=/tmp/rocketchat-4.12.0-alpha.2-linux-x86_64.AppImage /tmp/tests/run-all.sh"

# For Snap
ssh jean@<VM_IP> "APP_PATH=/snap/bin/rocketchat-desktop /tmp/tests/run-all.sh"
```

**Run WITH GPU tests:**
```bash
# For installed packages (RPM/DEB)
ssh jean@<VM_IP> "/tmp/tests/run-gpu-tests.sh"

# For AppImage
ssh jean@<VM_IP> "APP_PATH=/tmp/rocketchat-4.12.0-alpha.2-linux-x86_64.AppImage /tmp/tests/run-gpu-tests.sh"
```

### Log File Naming Convention

```
<os>-<package>-<gpu>.log

Examples:
- fedora42-rpm-no-gpu.log
- fedora42-rpm-gpu.log
- fedora42-appimage-no-gpu.log
- ubuntu2204-deb-no-gpu.log
- ubuntu2204-snap-no-gpu.log
- ubuntu2404-appimage-gpu.log
- manjaro-appimage-no-gpu.log
- opensuse-rpm-no-gpu.log
```

### Complete Test Script Per OS

**Fedora 42 (RPM + AppImage):**
```bash
VM_IP=192.168.13.80
RESULTS=/home/jean/projects/linux-testing/test-framework/results/2026-01-29_full-matrix-4.12.0-alpha.2

# Transfer tests
scp -r /home/jean/projects/linux-testing/test-framework/shared/tests jean@${VM_IP}:/tmp/

# Deploy and test RPM
scp /home/jean/projects/linux-testing/Rocket.Chat.Electron/dist/rocketchat-*.rpm jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "sudo dnf install -y /tmp/rocketchat-*.rpm"
ssh jean@${VM_IP} "/tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/fedora42-rpm-no-gpu.log

# Deploy and test AppImage
scp /home/jean/projects/linux-testing/Rocket.Chat.Electron/dist/rocketchat-*.AppImage jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "chmod +x /tmp/rocketchat-*.AppImage"
ssh jean@${VM_IP} "APP_PATH=/tmp/rocketchat-4.12.0-alpha.2-linux-x86_64.AppImage /tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/fedora42-appimage-no-gpu.log
```

**Ubuntu 22.04/24.04 (DEB + AppImage + Snap):**
```bash
VM_IP=192.168.13.81  # or .82 for 24.04
OS_NAME=ubuntu2204   # or ubuntu2404
RESULTS=/home/jean/projects/linux-testing/test-framework/results/2026-01-29_full-matrix-4.12.0-alpha.2

scp -r /home/jean/projects/linux-testing/test-framework/shared/tests jean@${VM_IP}:/tmp/

# DEB
scp /home/jean/projects/linux-testing/Rocket.Chat.Electron/dist/rocketchat-*.deb jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "sudo apt install -y /tmp/rocketchat-*.deb"
ssh jean@${VM_IP} "/tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/${OS_NAME}-deb-no-gpu.log

# AppImage
scp /home/jean/projects/linux-testing/Rocket.Chat.Electron/dist/rocketchat-*.AppImage jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "chmod +x /tmp/rocketchat-*.AppImage"
ssh jean@${VM_IP} "APP_PATH=/tmp/rocketchat-4.12.0-alpha.2-linux-x86_64.AppImage /tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/${OS_NAME}-appimage-no-gpu.log

# Snap
scp /home/jean/projects/linux-testing/Rocket.Chat.Electron/dist/rocketchat-*.snap jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "sudo snap install --dangerous /tmp/rocketchat-*.snap"
ssh jean@${VM_IP} "APP_PATH=/snap/bin/rocketchat-desktop /tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/${OS_NAME}-snap-no-gpu.log
```

**Manjaro (AppImage only):**
```bash
VM_IP=192.168.13.83
RESULTS=/home/jean/projects/linux-testing/test-framework/results/2026-01-29_full-matrix-4.12.0-alpha.2

scp -r /home/jean/projects/linux-testing/test-framework/shared/tests jean@${VM_IP}:/tmp/
scp /home/jean/projects/linux-testing/Rocket.Chat.Electron/dist/rocketchat-*.AppImage jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "chmod +x /tmp/rocketchat-*.AppImage"
ssh jean@${VM_IP} "APP_PATH=/tmp/rocketchat-4.12.0-alpha.2-linux-x86_64.AppImage /tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/manjaro-appimage-no-gpu.log
```

**openSUSE (RPM + AppImage):**
```bash
VM_IP=192.168.13.84
RESULTS=/home/jean/projects/linux-testing/test-framework/results/2026-01-29_full-matrix-4.12.0-alpha.2

scp -r /home/jean/projects/linux-testing/test-framework/shared/tests jean@${VM_IP}:/tmp/

# RPM (note: zypper, not dnf!)
scp /home/jean/projects/linux-testing/Rocket.Chat.Electron/dist/rocketchat-*.rpm jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "sudo zypper install -y --allow-unsigned-rpm /tmp/rocketchat-*.rpm"
ssh jean@${VM_IP} "/tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/opensuse-rpm-no-gpu.log

# AppImage
scp /home/jean/projects/linux-testing/Rocket.Chat.Electron/dist/rocketchat-*.AppImage jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "chmod +x /tmp/rocketchat-*.AppImage"
ssh jean@${VM_IP} "APP_PATH=/tmp/rocketchat-4.12.0-alpha.2-linux-x86_64.AppImage /tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/opensuse-appimage-no-gpu.log
```

### GPU Rotation Between VMs (Complete Safe Sequence)

**CRITICAL**: Only ONE VM can have the GPU at a time. The GPU is on **Proxmox** at PCI `0000:01:00` (RTX 3060), NOT on local host!

```bash
# === SETUP: Get auth tokens ===
AUTH=$(curl -k -s -d "username=root@pam&password=cb6wist3" \
  "https://192.168.13.85:8006/api2/json/access/ticket")
TICKET=$(echo "$AUTH" | jq -r '.data.ticket')
CSRF=$(echo "$AUTH" | jq -r '.data.CSRFPreventionToken')

# === STEP 1: Detach GPU from current VM ===
CURRENT_VMID=100  # VM that currently has GPU

# 1a. Stop VM
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X POST "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${CURRENT_VMID}/status/stop"

# 1b. Wait for stopped state (poll until status=stopped)
for i in {1..20}; do
  STATUS=$(curl -k -s -b "PVEAuthCookie=$TICKET" \
    "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${CURRENT_VMID}/status/current" | jq -r '.data.status')
  [[ "$STATUS" == "stopped" ]] && break
  sleep 3
done

# 1c. Remove GPU config
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X PUT -d "delete=hostpci0" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${CURRENT_VMID}/config"

# 1d. Start VM without GPU (optional - can leave stopped)
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X POST "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${CURRENT_VMID}/status/start"

# === STEP 2: Attach GPU to next VM ===
NEXT_VMID=101  # VM that needs GPU

# 2a. Stop next VM
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X POST "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${NEXT_VMID}/status/stop"

# 2b. Wait for stopped state
for i in {1..20}; do
  STATUS=$(curl -k -s -b "PVEAuthCookie=$TICKET" \
    "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${NEXT_VMID}/status/current" | jq -r '.data.status')
  [[ "$STATUS" == "stopped" ]] && break
  sleep 3
done

# 2c. Attach GPU (use FULL address format: 0000:01:00)
# DO NOT use "03:00" - that's the local GTX 970 which doesn't exist on Proxmox!
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X PUT -d "hostpci0=0000:01:00" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${NEXT_VMID}/config"

# 2d. Start VM with GPU
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X POST "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${NEXT_VMID}/status/start"

# === STEP 3: Wait for VM to be accessible ===
# Poll qemu-guest-agent for IP
for i in {1..24}; do
  IP=$(curl -k -s -b "PVEAuthCookie=$TICKET" \
    "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${NEXT_VMID}/agent/network-get-interfaces" 2>/dev/null | \
    jq -r '.data.result[] | select(.name != "lo") | .["ip-addresses"][]? | select(.["ip-address-type"] == "ipv4") | .["ip-address"]' | head -1)
  [[ -n "$IP" && "$IP" != "null" ]] && break
  sleep 5
done
echo "VM $NEXT_VMID IP: $IP"

# === STEP 4: Verify GPU visible ===
ssh jean@$IP "lspci | grep -i nvidia"
# Expected: "VGA compatible controller: NVIDIA Corporation GA106 [GeForce RTX 3060..."
```

### Agent Delegation Pattern (for AI agents)

When running tests in parallel, delegate to background agents:

```
# Wave 3: Fire 5 parallel agents for WITHOUT GPU tests
delegate_task(category="quick", run_in_background=true, prompt="
  VM: Fedora 42 (192.168.13.80)
  Packages: RPM, AppImage
  Commands: [copy from runbook above]
  Save to: results/.../fedora42-*-no-gpu.log
")
# Repeat for each VM...

# Wait for all to complete, then Wave 4-8: Sequential GPU tests
# GPU tests CANNOT be parallelized - only one GPU
```

### Interpreting Test Output

```
Summary: PASS=3 FAIL=0 EXPECTED=2
```

- **PASS**: Test scenario worked correctly
- **FAIL**: Unexpected crash (exit 139 = SIGSEGV = THE BUG)
- **EXPECTED**: Acceptable failure (e.g., no-display test crashes because no display)
- **SKIP**: Test not applicable (e.g., Weston+NVIDIA incompatibility)

**Critical test**: `wayland-fake` - if this shows PASS, the crash fix works.
