# Manjaro Linux Test Scripts

## VM Configuration

| Setting | Value |
|---------|-------|
| VM ID | 103 |
| Name | manjaro-linux |
| OS | Manjaro Linux KDE 26.0.1 |
| Desktop | KDE Plasma (KWin Wayland) |
| User | jean |
| Password | cb6wist3 |
| RAM | 8 GB |
| CPU | 8 cores |
| Disk | 64 GB |

## Status: AWAITING INSTALLATION

VM is configured with Manjaro KDE ISO. Install via Proxmox VNC console.

## Why Manjaro?

1. **Arch-based Rolling Release** - Catches bleeding-edge compatibility issues
2. **Latest Packages** - Tests with newest kernel, glibc, mesa versions
3. **KDE Plasma Desktop** - Tests KWin Wayland compositor (different from GNOME's Mutter)
4. **Developer Friendly** - Popular among developers, easy to install
5. **Covers Arch Linux** - Same package base (pacman, AUR)

## Package Format

Manjaro uses `pacman` with `.pkg.tar.zst` packages. Rocket.Chat doesn't build native Arch packages, so we test **AppImage only**.

| Format | Support |
|--------|---------|
| AppImage | Tested (universal format) |
| Flatpak | Available in repos |
| Native pkg | Not available |

## Installation (via VNC)

1. Access Proxmox: https://192.168.13.85:8006
2. Select VM 103 -> Console
3. Boot from ISO and follow Manjaro installer
4. Create user `jean` with password `cb6wist3`
5. After install, reboot and configure:

```bash
# Install test dependencies
sudo pacman -S qemu-guest-agent xorg-server-xvfb weston fuse2 openssh

# Enable services
sudo systemctl enable --now qemu-guest-agent sshd

# Passwordless sudo
echo "jean ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/jean
```

## Scripts

| Script | Purpose |
|--------|---------|
| `build.sh` | Build AppImage from current git checkout |
| `deploy.sh` | Transfer AppImage to VM |
| `gpu-control.sh` | Attach/detach GPU from VM |

## Usage

### Full Test Matrix (Python runner — canonical)

```bash
python -m automation.main run examples/rocketchat.toml --only manjaro
```

### Per-Step Primitives

```bash
./build.sh
./deploy.sh
```

## Manjaro-Specific Notes

- KDE Plasma uses KWin compositor (different Wayland implementation than GNOME)
- Rolling release: packages update frequently
- AppImage requires `fuse2` package (may already be installed)
- Has graphical installer - much easier than Arch

## Coverage Value

| Coverage Area | What It Validates |
|---------------|-------------------|
| **Arch Linux** | Same package base, AUR compatibility |
| **Rolling Release** | Bleeding-edge libraries and kernel |
| **KDE Plasma** | KWin Wayland compositor (vs GNOME's Mutter) |
| **Developer Workstations** | Popular among Linux developers |
