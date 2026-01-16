# Fedora 42 Test Scripts

## VM Configuration

| Setting | Value |
|---------|-------|
| VM ID | 100 |
| Name | fedora42-gpu-test |
| OS | Fedora 42 Workstation |
| Desktop | GNOME 47 / Wayland |
| User | jean |
| Password | cb6wist3 |
| RAM | 8 GB |
| CPU | 8 cores |
| Disk | 64 GB |

## Prerequisites

1. Fedora 42 installed with GNOME desktop
2. Auto-login enabled for user `jean`
3. qemu-guest-agent installed and running
4. SSH server enabled

### Enable Auto-login (GDM)

```bash
sudo nano /etc/gdm/custom.conf
```

Add under `[daemon]`:
```ini
[daemon]
AutomaticLoginEnable=True
AutomaticLogin=jean
```

### Install Guest Agent

```bash
sudo dnf install -y qemu-guest-agent
sudo systemctl enable --now qemu-guest-agent
```

### Enable SSH

```bash
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

### Quick Test (Current Branch)

```bash
./build.sh
./deploy.sh
./test.sh
```

### Full Comparison Test

```bash
./full-test.sh
```

### Test Specific Scenario

```bash
./test.sh --test wayland-fake
```

### Toggle GPU

```bash
./gpu-control.sh --status
./gpu-control.sh --attach
./gpu-control.sh --detach
```

## Test Results (2026-01-16)

| Test | Old 4.11.0 | New 4.11.1 |
|------|------------|------------|
| wayland-real | PASS | PASS |
| wayland-fake | SEGFAULT | PASS |
| wayland-nodisp | SEGFAULT | PASS |
| x11 | SEGFAULT | PASS |

## Notes

- Fedora 42 uses Wayland by default with GNOME
- XWayland is available on `:0` for X11 fallback
- XAUTHORITY path changes on each boot, scripts detect it dynamically
- GPU passthrough works in compute mode (VNC still accessible)
