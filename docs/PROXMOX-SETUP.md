> Last verified: 2026-04-23

# Proxmox Setup Guide

## Server Information

| Setting | Value |
|---------|-------|
| URL | https://192.168.13.85:8006/ |
| User | root |
| Password | (see .env file) |
| Node | pve |

## Hardware

| Component | Spec |
|-----------|------|
| CPU | Intel i7-12700H (14 cores / 20 threads) |
| RAM | 32 GB |
| Storage | 1 TB NVMe (XPG GAMMIX S11 Pro) |
| GPU | NVIDIA RTX 3060 LHR (for passthrough) |

## GPU Details

- **Device**: NVIDIA GeForce RTX 3060 Lite Hash Rate
- **PCI Address**: 0000:01:00
- **IOMMU Group**: 12 (clean - only GPU + Audio)
- **PCI IDs**: 10de:2504 (VGA), 10de:228e (Audio)

## Network Storage (ISOs)

- **Server**: 192.168.13.11 (mushu)
- **Protocol**: SMB/CIFS (anonymous)
- **Mount Point**: mushu-isos
- **Note**: ISOs must be in `template/iso/` subdirectory

## Available ISOs

| ISO | Location |
|-----|----------|
| Fedora-Workstation-Live-42-1.1.x86_64.iso | mushu-isos |
| ubuntu-24.04.3-desktop-amd64.iso | mushu-isos |
| ubuntu-22.04.2-desktop-amd64.iso | mushu-isos |
| Win10_22H2_EnglishInternational_x64v1.iso | mushu-isos |
| virtio-win-0.1.190-1.iso | mushu-isos |

## VMs Created

| VMID | Name | OS | MAC | Purpose |
|------|------|-----|-----|---------|
| 100 | fedora42-gpu-test | Fedora 42 | BC:24:11:B0:40:27 | Linux testing |
| 101 | ubuntu-22.04 | Ubuntu 22.04 | BC:24:11:07:8D:48 | Linux testing |
| 102 | ubuntu-24.04 | Ubuntu 24.04 | BC:24:11:8C:1A:6F | Linux testing |
| 103 | arch-linux | Arch Linux | BC:24:11:73:EF:4E | Linux testing |
| 104 | windows-10 | Windows 10 | BC:24:11:A3:04:A5 | Windows testing |
| 105 | windows-11 | Windows 11 | BC:24:11:4C:58:AD | Windows testing |

## VFIO/GPU Passthrough Setup

### 1. Enable IOMMU

Edit `/etc/default/grub`:
```
GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt"
```

Update GRUB:
```bash
update-grub
reboot
```

### 2. Load VFIO Modules

Create `/etc/modules-load.d/vfio.conf`:
```
vfio
vfio_iommu_type1
vfio_pci
```

### 3. Bind GPU to VFIO

Find PCI IDs:
```bash
lspci -nn | grep NVIDIA
# 01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GA106 [10de:2504]
# 01:00.1 Audio device [0403]: NVIDIA Corporation GA106 [10de:228e]
```

Create `/etc/modprobe.d/vfio.conf`:
```
options vfio-pci ids=10de:2504,10de:228e
softdep nouveau pre: vfio-pci
softdep nvidia pre: vfio-pci
```

Blacklist nouveau and nvidia:
```bash
cat > /etc/modprobe.d/blacklist-gpu.conf << EOF
blacklist nouveau
blacklist nvidiafb
EOF
update-initramfs -u
reboot
```

### 4. Verify

```bash
lspci -nnk -s 01:00
# Should show: Kernel driver in use: vfio-pci
```

## VM Creation

### Create VM (without GPU first)

Via API:
```bash
AUTH=$(curl -k -s -d "username=root@pam&password=$PROXMOX_PASSWORD" https://192.168.13.85:8006/api2/json/access/ticket)
TICKET=$(echo "$AUTH" | jq -r '.data.ticket')
CSRF=$(echo "$AUTH" | jq -r '.data.CSRFPreventionToken')

curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X POST "https://192.168.13.85:8006/api2/json/nodes/pve/qemu" \
  --data-urlencode "vmid=10X" \
  --data-urlencode "name=distro-name" \
  --data-urlencode "memory=8192" \
  --data-urlencode "cores=8" \
  --data-urlencode "cpu=host" \
  --data-urlencode "machine=q35" \
  --data-urlencode "bios=ovmf" \
  --data-urlencode "efidisk0=local-lvm:1,efitype=4m,pre-enrolled-keys=0" \
  --data-urlencode "scsi0=local-lvm:64,iothread=1" \
  --data-urlencode "scsihw=virtio-scsi-single" \
  --data-urlencode "net0=virtio,bridge=vmbr0" \
  --data-urlencode "ide2=mushu-isos:iso/YOUR-ISO.iso,media=cdrom" \
  --data-urlencode "boot=order=ide2;scsi0" \
  --data-urlencode "ostype=l26" \
  --data-urlencode "agent=1" \
  --data-urlencode "vga=virtio"
```

### Linux VMs (via CLI)

```bash
# Create VM with UEFI
qm create 100 \
  --name fedora42-gpu-test \
  --memory 8192 \
  --cores 8 \
  --cpu host \
  --bios ovmf \
  --machine q35 \
  --net0 virtio,bridge=vmbr0 \
  --scsihw virtio-scsi-single \
  --agent 1

# Add EFI disk
qm set 100 --efidisk0 local-lvm:1,efitype=4m,pre-enrolled-keys=0

# Add main disk
qm set 100 --scsi0 local-lvm:64,iothread=1

# Attach ISO
qm set 100 --ide2 mushu-isos:iso/fedora.iso,media=cdrom

# Set boot order
qm set 100 --boot order=ide2;scsi0
```

### Windows VMs

Same as Linux, plus:
```bash
# Add TPM for Windows 11
qm set 105 --tpmstate0 local-lvm:1,version=v2.0

# Attach VirtIO drivers ISO
qm set 104 --ide3 mushu-isos:iso/virtio-win.iso,media=cdrom
```

### Post-Install Configuration (via SSH)

#### SSH Helper (no local sshpass)
```bash
docker run --rm alpine sh -c "apk add --no-cache openssh-client sshpass >/dev/null 2>&1 && \
  sshpass -p 'cb6wist3' ssh -o StrictHostKeyChecking=no jean@VM_IP 'COMMANDS'"
```

#### Enable SSH + Guest Agent
```bash
# Fedora
sudo systemctl enable --now sshd
sudo dnf install -y qemu-guest-agent && sudo systemctl enable --now qemu-guest-agent

# Ubuntu
sudo systemctl enable --now ssh
sudo apt install -y qemu-guest-agent && sudo systemctl enable --now qemu-guest-agent

# Arch
sudo systemctl enable --now sshd
sudo pacman -S --noconfirm qemu-guest-agent && sudo systemctl enable --now qemu-guest-agent
```

#### Enable Auto-Login (GDM - Fedora/Ubuntu GNOME)
```bash
echo cb6wist3 | sudo -S sh -c 'cat > /etc/gdm/custom.conf << EOF
[daemon]
AutomaticLoginEnable=True
AutomaticLogin=jean
[security]
[xdmcp]
[chooser]
[debug]
EOF'
```

#### Enable Auto-Login (SDDM - KDE/LXQt)
```bash
echo cb6wist3 | sudo -S sh -c 'cat > /etc/sddm.conf.d/autologin.conf << EOF
[Autologin]
User=jean
Session=plasma
EOF'
```

### Change Boot Order (after install)
```bash
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X PUT "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/10X/config" \
  --data-urlencode "boot=order=scsi0" \
  --data-urlencode "ide2=none,media=cdrom"
```

### Get VM IP (via guest agent)
```bash
curl -k -s -b "PVEAuthCookie=$TICKET" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/10X/agent/network-get-interfaces" | \
  jq -r '.data.result[] | select(."ip-addresses") | ."ip-addresses"[] | select(."ip-address-type"=="ipv4" and (."ip-address" | startswith("192.168"))) | ."ip-address"'
```

## GPU Attachment

### GPU Modes

| Mode | Config | Display | Use Case |
|------|--------|---------|----------|
| **No GPU** | No hostpci0 | VNC works | OS install, basic testing |
| **GPU Compute** | `hostpci0=0000:01:00,pcie=1` | VNC works | GPU available, display on virtio |
| **GPU Primary** | `hostpci0=0000:01:00,pcie=1,x-vga=1` | VNC broken | Full GPU, need physical monitor |

**For testing: Use GPU Compute mode** - GPU available but VNC still works.

### Attach GPU to VM (compute mode)

```bash
# Stop VM first
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X POST "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/10X/status/stop"

sleep 5

# Add GPU (no x-vga = display stays on virtio)
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X PUT "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/10X/config" \
  --data-urlencode "hostpci0=0000:01:00,pcie=1"

# Start VM
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X POST "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/10X/status/start"
```

### Detach GPU

```bash
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X PUT "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/10X/config" \
  --data-urlencode "delete=hostpci0"
```

**Note: Only ONE VM can have GPU at a time!**

## SMB Helper (move ISOs)

```bash
# List ISOs on mushu
docker run --rm alpine sh -c "apk add --no-cache samba-client >/dev/null 2>&1 && \
  smbclient //192.168.13.11/isos -N -c 'cd template/iso; ls'"

# Move ISO to correct location
docker run --rm alpine sh -c "apk add --no-cache samba-client >/dev/null 2>&1 && \
  smbclient //192.168.13.11/isos -N -c 'rename ISONAME.iso template/iso/ISONAME.iso'"
```

## SSH Helper Function

Add to your shell:
```bash
vssh() {
  local ip=$1; shift
  docker run --rm alpine sh -c "apk add --no-cache openssh-client sshpass >/dev/null 2>&1 && \
    sshpass -p 'cb6wist3' ssh -o StrictHostKeyChecking=no jean@$ip '$*'"
}

# Usage: vssh 192.168.13.138 'uname -a'
```

## API Access

### Get Authentication Token

```bash
AUTH=$(curl -k -s -d "username=root@pam&password=$PROXMOX_PASSWORD" \
  https://192.168.13.85:8006/api2/json/access/ticket)
TICKET=$(echo "$AUTH" | jq -r '.data.ticket')
CSRF=$(echo "$AUTH" | jq -r '.data.CSRFPreventionToken')
```

### API Examples

```bash
# List VMs
curl -k -s -b "PVEAuthCookie=$TICKET" \
  https://192.168.13.85:8006/api2/json/nodes/pve/qemu

# Get VM status
curl -k -s -b "PVEAuthCookie=$TICKET" \
  https://192.168.13.85:8006/api2/json/nodes/pve/qemu/100/status/current

# Start VM
curl -k -s -b "PVEAuthCookie=$TICKET" \
  -H "CSRFPreventionToken: $CSRF" \
  -X POST \
  https://192.168.13.85:8006/api2/json/nodes/pve/qemu/100/status/start

# Get VM IP (via guest agent)
curl -k -s -b "PVEAuthCookie=$TICKET" \
  https://192.168.13.85:8006/api2/json/nodes/pve/qemu/100/agent/network-get-interfaces
```

## Test Results

### Fedora 42 - VM 100

| Test | Result |
|------|--------|
| Real Wayland | ✅ PASS - "Using Wayland platform" |
| Fake WAYLAND_DISPLAY | ✅ PASS - Exit 0 (no crash) |
| GPU detected | ✅ RTX 3060 visible in VM |

**Fix verified**: PR #3171 wrapper script correctly detects invalid Wayland socket and falls back to X11.

## Current Status

- [x] Proxmox configured with VFIO
- [x] GPU bound to vfio-pci
- [x] 6 VMs created (Fedora, Ubuntu 22.04, Ubuntu 24.04, Arch, Win10, Win11)
- [x] VM 100 (Fedora) fully configured with auto-login
- [x] Rocket.Chat fix verified on Fedora 42
- [ ] Test remaining Linux distros
- [ ] Test Windows VMs
