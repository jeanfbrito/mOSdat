# openSUSE Leap Test Scripts

## VM Configuration

| Setting | Value |
|---------|-------|
| VM ID | 106 |
| Name | opensuse-leap |
| OS | openSUSE Leap 16.0 |
| Desktop | KDE Plasma (default) |
| User | jean |
| Password | cb6wist3 |
| RAM | 8 GB |
| CPU | 8 cores |
| Disk | 64 GB |

## Status: TEMPLATE - AWAITING ISO UPLOAD

ISO needs to be uploaded to Proxmox, then VM created and configured.

## Why openSUSE?

1. **SUSE Enterprise Coverage** - Leap is based on SUSE Linux Enterprise (SLE)
2. **KDE Plasma Desktop** - Tests KWin Wayland compositor (different from GNOME's Mutter)
3. **zypper Package Manager** - Different RPM ecosystem from Fedora's dnf
4. **European Enterprise Market** - Big in Germany, automotive, manufacturing

## Package Formats

| Format | Support |
|--------|---------|
| RPM | Native (zypper) |
| AppImage | Universal |
| Flatpak | Available |

## Prerequisites

After OS installation:

```bash
# 1. Install qemu-guest-agent and test dependencies
sudo zypper install -y qemu-guest-agent xvfb weston

# 2. Enable guest agent
sudo systemctl enable --now qemu-guest-agent

# 3. Passwordless sudo
echo "jean ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/jean

# 4. Enable SSH (usually enabled by default)
sudo systemctl enable --now sshd
```

## Scripts

| Script | Purpose |
|--------|---------|
| `build.sh` | Build Rocket.Chat from current git checkout |
| `deploy.sh` | Transfer and install RPM on VM |
| `test.sh` | Run Wayland/X11 crash tests |
| `gpu-control.sh` | Attach/detach GPU from VM |
| `full-test.sh` | Run complete test matrix |

## Usage

### Quick Test

```bash
./build.sh
./deploy.sh
./test.sh
```

### Test AppImage

```bash
scp /path/to/*.AppImage jean@<VM_IP>:/tmp/
ssh jean@<VM_IP> "APP_PATH=/tmp/*.AppImage /tmp/tests/run-all.sh"
```

## openSUSE-Specific Notes

- Uses `zypper` instead of `dnf` or `apt`
- KDE Plasma is the default desktop (KWin Wayland)
- YaST is the system configuration tool
- Different Wayland compositor behavior than GNOME
