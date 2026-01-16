# Arch Linux Test Scripts

## VM Configuration

| Setting | Value |
|---------|-------|
| VM ID | 103 |
| Name | arch-linux |
| OS | Arch Linux (rolling) |
| Desktop | GNOME / Wayland |
| User | jean |
| Password | cb6wist3 |

## Status: TEMPLATE - NOT YET CONFIGURED

This VM needs to be installed and configured before running tests.

## Prerequisites

Arch Linux requires manual installation. After base install:

### Install GNOME Desktop

```bash
pacman -S gnome gnome-extra gdm
systemctl enable gdm
```

### Enable Auto-login

```bash
nano /etc/gdm/custom.conf
# Add under [daemon]:
# AutomaticLoginEnable=True
# AutomaticLogin=jean
```

### Install Guest Agent

```bash
pacman -S qemu-guest-agent
systemctl enable --now qemu-guest-agent
```

### Enable SSH

```bash
pacman -S openssh
systemctl enable --now sshd
```

## Package Format

Arch uses `pacman` with `.pkg.tar.zst` packages. However, Rocket.Chat Electron
doesn't build Arch packages by default. Options:

1. **Use AppImage** (recommended for testing)
2. Build from AUR
3. Extract and run from tarball

### Using AppImage

```bash
# Build AppImage instead of .pkg
yarn electron-builder --publish never --linux AppImage
```

## Scripts

Scripts need modification from other Linux distros:
- Change package format to AppImage
- Adjust installation method (chmod +x, no package manager)
