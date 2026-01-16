# Ubuntu 24.04 Test Scripts

## VM Configuration

| Setting | Value |
|---------|-------|
| VM ID | 102 |
| Name | ubuntu-24.04 |
| OS | Ubuntu 24.04.3 LTS |
| Desktop | GNOME 46 / Wayland |
| User | jean |
| Password | cb6wist3 |

## Status: TEMPLATE - NOT YET CONFIGURED

This VM needs to be installed and configured before running tests.

## Prerequisites

Same as Ubuntu 22.04, with these differences:

- GNOME 46 (newer Wayland implementation)
- May have different default Wayland behavior
- Uses same GDM3 configuration path

### Installation Steps

1. Boot VM from Ubuntu 24.04 ISO via Proxmox noVNC
2. Install with user `jean`, password `cb6wist3`
3. Enable auto-login in GDM3
4. Install qemu-guest-agent
5. Enable SSH

```bash
sudo apt update
sudo apt install -y qemu-guest-agent openssh-server
sudo systemctl enable --now qemu-guest-agent ssh

sudo nano /etc/gdm3/custom.conf
# Add: AutomaticLoginEnable=true and AutomaticLogin=jean
```

## Scripts

Copy and modify from `ubuntu-22-04/` - main difference is VM ID (102).

```bash
cp -r ../ubuntu-22-04/*.sh .
# Edit config.sh to change VMID to 102
```
