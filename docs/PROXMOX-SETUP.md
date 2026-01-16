# Proxmox Setup Guide

## Server Information

| Setting | Value |
|---------|-------|
| URL | https://192.168.13.85:8006/ |
| User | root |
| Password | (see .env file) |
| Node | pve |

## Hardware

- CPU: Intel i7-12700H (20 threads)
- RAM: 32 GB
- Storage: 1 TB NVMe
- GPU: NVIDIA RTX 3060

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

Blacklist nouveau:
```bash
echo "blacklist nouveau" >> /etc/modprobe.d/blacklist.conf
update-initramfs -u
reboot
```

### 4. Verify

```bash
lspci -nnk -s 01:00
# Should show: Kernel driver in use: vfio-pci
```

## VM Creation

### Linux VMs

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
qm set 100 --ide2 local:iso/fedora.iso,media=cdrom

# Set boot order
qm set 100 --boot order=ide2;scsi0
```

### Windows VMs

Same as Linux, plus:
```bash
# Add TPM for Windows 11
qm set 105 --tpmstate0 local-lvm:1,version=v2.0

# Attach VirtIO drivers ISO
qm set 104 --ide3 local:iso/virtio-win.iso,media=cdrom
```

## Attach GPU to VM

```bash
# Compute mode (VNC works)
qm set 100 --hostpci0 0000:01:00,pcie=1

# Primary mode (VNC blank)
qm set 100 --hostpci0 0000:01:00,pcie=1,x-vga=1
```

## Detach GPU

```bash
qm set 100 --delete hostpci0
```

## Network Storage (ISOs)

SMB share configured:
- Server: 192.168.13.11 (mushu)
- Share: public
- Mount: mushu-isos

ISOs available:
- Fedora-Workstation-Live-42-1.1.x86_64.iso
- ubuntu-22.04.2-desktop-amd64.iso
- ubuntu-24.04.3-desktop-amd64.iso
- Win10_22H2_EnglishInternational_x64v1.iso
- virtio-win-0.1.190-1.iso

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
