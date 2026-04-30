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
python -m automation.main run examples/rocketchat.toml

# Resume after interruption:
python -m automation.main run examples/rocketchat.toml --resume

# Skip build phase (use pre-built packages):
python -m automation.main run examples/rocketchat.toml --skip-build

# Test a single VM:
python -m automation.main run examples/rocketchat.toml --only fedora42

# Quick test with a pre-built package:
python -m automation.main test examples/rocketchat.toml /path/to/package.rpm --vms fedora42

# Per-OS bash adapters (build/deploy/gpu primitives only):
./os/fedora-42/build.sh
./os/fedora-42/deploy.sh
./os/fedora-42/gpu-control.sh --attach
./os/fedora-42/gpu-control.sh --detach
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
export FRAMEWORK_PATH=/home/jean/projects/linux-testing/mOSdat
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
cd ${REPO_PATH}

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
scp -r ${FRAMEWORK_PATH}/shared/scenarios/smoke-linux jean@<VM_IP>:/tmp/tests
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
RESULTS=${FRAMEWORK_PATH}/results/2026-01-29_full-matrix-4.12.0-alpha.2

# Transfer tests
scp -r ${FRAMEWORK_PATH}/shared/scenarios/smoke-linux jean@${VM_IP}:/tmp/tests

# Deploy and test RPM
scp ${REPO_PATH}/dist/rocketchat-*.rpm jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "sudo dnf install -y /tmp/rocketchat-*.rpm"
ssh jean@${VM_IP} "/tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/fedora42-rpm-no-gpu.log

# Deploy and test AppImage
scp ${REPO_PATH}/dist/rocketchat-*.AppImage jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "chmod +x /tmp/rocketchat-*.AppImage"
ssh jean@${VM_IP} "APP_PATH=/tmp/rocketchat-4.12.0-alpha.2-linux-x86_64.AppImage /tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/fedora42-appimage-no-gpu.log
```

**Ubuntu 22.04/24.04 (DEB + AppImage + Snap):**
```bash
VM_IP=192.168.13.81  # or .82 for 24.04
OS_NAME=ubuntu2204   # or ubuntu2404
RESULTS=${FRAMEWORK_PATH}/results/2026-01-29_full-matrix-4.12.0-alpha.2

scp -r ${FRAMEWORK_PATH}/shared/scenarios/smoke-linux jean@${VM_IP}:/tmp/tests

# DEB
scp ${REPO_PATH}/dist/rocketchat-*.deb jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "sudo apt install -y /tmp/rocketchat-*.deb"
ssh jean@${VM_IP} "/tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/${OS_NAME}-deb-no-gpu.log

# AppImage
scp ${REPO_PATH}/dist/rocketchat-*.AppImage jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "chmod +x /tmp/rocketchat-*.AppImage"
ssh jean@${VM_IP} "APP_PATH=/tmp/rocketchat-4.12.0-alpha.2-linux-x86_64.AppImage /tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/${OS_NAME}-appimage-no-gpu.log

# Snap
scp ${REPO_PATH}/dist/rocketchat-*.snap jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "sudo snap install --dangerous /tmp/rocketchat-*.snap"
ssh jean@${VM_IP} "APP_PATH=/snap/bin/rocketchat-desktop /tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/${OS_NAME}-snap-no-gpu.log
```

**Manjaro (AppImage only):**
```bash
VM_IP=192.168.13.83
RESULTS=${FRAMEWORK_PATH}/results/2026-01-29_full-matrix-4.12.0-alpha.2

scp -r ${FRAMEWORK_PATH}/shared/scenarios/smoke-linux jean@${VM_IP}:/tmp/tests
scp ${REPO_PATH}/dist/rocketchat-*.AppImage jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "chmod +x /tmp/rocketchat-*.AppImage"
ssh jean@${VM_IP} "APP_PATH=/tmp/rocketchat-4.12.0-alpha.2-linux-x86_64.AppImage /tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/manjaro-appimage-no-gpu.log
```

**openSUSE (RPM + AppImage):**
```bash
VM_IP=192.168.13.84
RESULTS=${FRAMEWORK_PATH}/results/2026-01-29_full-matrix-4.12.0-alpha.2

scp -r ${FRAMEWORK_PATH}/shared/scenarios/smoke-linux jean@${VM_IP}:/tmp/tests

# RPM (note: zypper, not dnf!)
scp ${REPO_PATH}/dist/rocketchat-*.rpm jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "sudo zypper install -y --allow-unsigned-rpm /tmp/rocketchat-*.rpm"
ssh jean@${VM_IP} "/tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/opensuse-rpm-no-gpu.log

# AppImage
scp ${REPO_PATH}/dist/rocketchat-*.AppImage jean@${VM_IP}:/tmp/
ssh jean@${VM_IP} "chmod +x /tmp/rocketchat-*.AppImage"
ssh jean@${VM_IP} "APP_PATH=/tmp/rocketchat-4.12.0-alpha.2-linux-x86_64.AppImage /tmp/tests/run-all.sh" 2>&1 | tee ${RESULTS}/opensuse-appimage-no-gpu.log
```
