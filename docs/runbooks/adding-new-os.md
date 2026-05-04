# Adding a New OS to mOSdat

← Back to [AGENTS.md](../../AGENTS.md)

This runbook walks through provisioning a new Linux distribution and integrating it into the mOSdat test matrix. Plan for approximately 2-3 hours end-to-end (VM creation, test iteration, matrix integration).

## Prerequisites

- Proxmox VE host access (192.168.13.85)
- ISO already uploaded and visible in Proxmox `template/iso/` (see [vm-setup.md](vm-setup.md) ISO Storage section)
- Approximately 30GB free disk on the Proxmox host
- Local SSH access to configure the VM after provisioning

## Step 1: Provision the VM

Follow [vm-setup.md](vm-setup.md) "Adding New OS" section:

1. Decide on VMID (check existing IDs in `examples/rocketchat.toml` to avoid collisions)
2. Reserve a static IP in the range `192.168.13.x` (e.g., 192.168.13.90)
3. Use Proxmox UI or API to create:
   - 4 CPU cores
   - 8GB RAM
   - 30GB disk
   - Attached to ISO
4. Boot VM, complete distro install, create user `jean` with SSH access
5. Configure passwordless sudo: `echo "jean ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/jean`
6. Install test dependencies:
   ```bash
   # Fedora/RHEL/openSUSE
   sudo dnf/zypper install -y xorg-x11-server-Xvfb weston

   # Ubuntu/Debian
   sudo apt install -y xvfb weston

   # Arch/Manjaro
   sudo pacman -S xorg-server-xvfb weston
   ```

## Step 2: Create OS Configuration Directory

Create the OS-specific folder with required scripts:

```bash
mkdir -p os/<distro>-<version>
cd os/<distro>-<version>
```

### Create `config.sh`

Template (adapt VMID, IP, package format, package manager):

```bash
#!/bin/bash
OS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${OS_SCRIPT_DIR}/../../shared/config.sh"
source "${OS_SCRIPT_DIR}/../../shared/proxmox-api.sh"

export VMID="<YOUR_VMID>"
export VM_NAME="<distro>-<version>-gpu-test"
export VM_IP="192.168.13.x"
export VM_USER="jean"
export PACKAGE_FORMAT="<rpm|deb|appimage>"
export PACKAGE_MANAGER="<dnf|apt|zypper|pacman>"
```

Examples by distro:
- **Fedora 42**: `PACKAGE_FORMAT="rpm"`, `PACKAGE_MANAGER="dnf"`
- **Ubuntu 22.04/24.04**: `PACKAGE_FORMAT="deb"`, `PACKAGE_MANAGER="apt"`
- **openSUSE**: `PACKAGE_FORMAT="rpm"`, `PACKAGE_MANAGER="zypper"`
- **Manjaro**: `PACKAGE_FORMAT="appimage"`, `PACKAGE_MANAGER="pacman"`

## Step 3: Write or Copy a Smoke Scenario

Create `os/<distro>-<version>/scenarios/rocketchat-smoke.yaml` by copying the Linux template:

```bash
cp shared/scenarios/functional/rocketchat-smoke-linux.yaml \
   os/<distro>-<version>/scenarios/rocketchat-smoke.yaml
```

This YAML defines the automated test steps: cleanup, launch, login, post a message. It's distro-agnostic (same steps work across GNOME, KDE, XFCE). Keep it as-is unless you discover a distro-specific quirk (e.g., KDE launcher keyboard bindings differ).

## Step 4: Validate VNC Framebuffer Display

**Critical step**: VNC console must capture the desktop correctly.

### Check Configuration

SSH into the VM and verify display server:

```bash
# Check Proxmox config for display type
curl -k -s -b "PVEAuthCookie=$TICKET" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/VMID/config" | \
  jq '.data | {vga, serial0, serial1}'

# Should show: "vga": "std" (NOT "virtio")
```

### Why This Matters

- `vga=std`: VNC framebuffer works, VLM can capture screenshots
- `vga=virtio`: VNC framebuffer blank, VLM cannot verify visuals
- See `.knowledge/infrastructure/mosdat-rocket-chat-window-exists-in-x-but-not-in-vnc-framebuffer-gnome-composito.md` for the full story

### If VNC Framebuffer is Blank

1. Edit Proxmox VM config:
   ```bash
   TICKET=$(curl -k -s -d "username=root@pam&password=<pass>" \
     "https://192.168.13.85:8006/api2/json/access/ticket" | jq -r '.data.ticket')
   curl -k -X PUT -b "PVEAuthCookie=$TICKET" \
     "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/VMID/config" \
     -d "vga=std"
   ```

2. Reboot the VM:
   ```bash
   curl -k -X POST -b "PVEAuthCookie=$TICKET" \
     "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/VMID/status/reboot"
   ```

3. Re-check Proxmox console — desktop should now be visible.

## Step 5: Check OS-Specific Quirks

Before running tests, verify distro-specific paths and behaviors:

### Cleanup Script Paths

The smoke scenario cleans keyrings and caches. Verify paths match your distro:

```bash
# SSH into VM as jean
ssh jean@192.168.13.x

# Check these paths exist and cleanup doesn't fail
ls -la ~/.config/Rocket.Chat*     # Should list Rocket.Chat config dirs
ls -la ~/.local/share/Rocket.Chat # May be empty before first run
ls -la ~/.cache/Rocket.Chat*      # May be empty before first run

# Verify gnome-keyring-d or equivalent exists
which gnome-keyring-daemon  # GNOME
which kwalletd              # KDE
```

### Package Install Command

Verify the install command in `examples/rocketchat.toml` matches your distro:

```bash
# Test locally on VM (without actual file):
sudo dnf install --help     # For RPM distros
sudo apt install --help     # For DEB distros
sudo pacman -S --help       # For Arch/Manjaro
```

### GPU Passthrough Config (if testing with GPU)

Ensure the Proxmox VM config accepts GPU device:

```bash
curl -k -s -b "PVEAuthCookie=$TICKET" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/VMID/config" | \
  jq '.data.hostpci0'
# Should return null initially (GPU not attached yet)
```

The test framework will attach/detach GPU automatically. See [docs/runbooks/gpu-rotation.md](gpu-rotation.md) for details.

## Step 6: Run Iterative Smoke Tests

Use the Python runner with `--until-step` to debug incrementally:

```bash
# Create a minimal test config pointing to your new VM
cat > test-new-os.toml << 'EOF'
[app]
name = "rocketchat"
binary = "/opt/Rocket.Chat/rocketchat-desktop"

[[vm]]
name = "mynewos"
vmid = <YOUR_VMID>
ip = "192.168.13.x"
desktop = "<GNOME|KDE|XFCE> <Wayland|X11>"

[[vm.package]]
format = "<rpm|deb|appimage>"
install = "<install-command>"
app_path = "/opt/Rocket.Chat/rocketchat-desktop"
file_glob = "<glob-pattern>"
EOF

# Run step-by-step
	mosdat functional test-new-os.toml --vms mynewos --until-step 1  # Cleanup + launch
	mosdat functional test-new-os.toml --vms mynewos --until-step 2  # Verify RC window
	mosdat functional test-new-os.toml --vms mynewos --until-step 3  # Login
	mosdat functional test-new-os.toml --vms mynewos --until-step 4  # Full smoke
```

### Common Failures

- **Step 1 fails** (cleanup): Check cleanup script paths (see Step 5)
- **Step 2 fails** (window not visible): VNC framebuffer issue (see Step 4)
- **Step 3 fails** (login fails): Check SSH access, passwordless sudo
- **Step 4 fails** (message send times out): App crash or network issue — check logs

For more, see [docs/TROUBLESHOOTING.md](../TROUBLESHOOTING.md).

## Step 7: Register in Test Matrix

Once smoke tests pass, add the VM to `examples/rocketchat.toml`:

```toml
[[vm]]
name = "<distro><version>"
vmid = <YOUR_VMID>
ip = "192.168.13.x"
desktop = "<GNOME|KDE|XFCE> <Wayland|X11>"

[[vm.package]]
format = "<rpm|deb|appimage>"
install = "sudo <pm> install -y /tmp/{file}"
uninstall = "sudo <pm> remove -y {app_name} 2>/dev/null || true"
app_path = "/opt/Rocket.Chat/rocketchat-desktop"
file_glob = "<glob>"
```

See `examples/rocketchat.toml` for reference entries (Fedora, Ubuntu, openSUSE, Manjaro).

## Running Your New OS in the Matrix

Once registered, run the full test suite:

```bash
# Test without GPU (faster)
mosdat run examples/rocketchat.toml --only <your-os-name> --skip-gpu

# Test with GPU (requires rotation)
mosdat run examples/rocketchat.toml --only <your-os-name>

# Test single package format
mosdat run examples/rocketchat.toml --only <your-os-name> --package rpm

# Parallel test all VMs without GPU
mosdat run examples/rocketchat.toml --skip-gpu
```

Results saved to `results/smoke/<date>_full-matrix-<version>/`.

## Checklist

- [ ] VM provisioned and accessible via SSH
- [ ] Passwordless sudo configured
- [ ] Test dependencies installed (Xvfb, weston, etc.)
- [ ] `os/<distro>/config.sh` created with correct VMID, IP, package format
- [ ] Smoke scenario YAML copied and verified
- [ ] VNC framebuffer confirmed working (`vga=std`)
- [ ] Cleanup script paths verified on VM
- [ ] Package install command tested
- [ ] `--until-step` tests pass all steps
- [ ] OS registered in `examples/rocketchat.toml`
- [ ] Full matrix run succeeds (at least `--skip-gpu`)

## Next: Run Full Matrix

See [docs/runbooks/matrix-run.md](matrix-run.md) for commands to test your new OS alongside existing ones.
