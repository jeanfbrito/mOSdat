# Ubuntu 22.04 Test Scripts

## VM Configuration

| Setting | Value |
|---------|-------|
| VM ID | 101 |
| Name | ubuntu-22.04 |
| OS | Ubuntu 22.04.2 LTS |
| Desktop | GNOME 42 / Wayland |
| User | jean |
| Password | cb6wist3 |
| RAM | 8 GB |
| CPU | 8 cores |
| Disk | 64 GB |

## Prerequisites

1. Ubuntu 22.04 installed with "Ubuntu" (Wayland) session
2. Auto-login enabled for user `jean`
3. qemu-guest-agent installed and running
4. SSH server enabled

### Enable Auto-login (GDM)

```bash
sudo nano /etc/gdm3/custom.conf
```

Add under `[daemon]`:
```ini
[daemon]
AutomaticLoginEnable=true
AutomaticLogin=jean
```

### Install Guest Agent

```bash
sudo apt update
sudo apt install -y qemu-guest-agent
sudo systemctl enable --now qemu-guest-agent
```

### Enable SSH

```bash
sudo apt install -y openssh-server
sudo systemctl enable --now ssh
```

### Ensure Wayland Session

Ubuntu 22.04 may default to X11 on some hardware. To force Wayland:

```bash
sudo nano /etc/gdm3/custom.conf
```

Comment out:
```ini
#WaylandEnable=false
```

## Scripts

| Script | Purpose |
|--------|---------|
| `build.sh` | Build Rocket.Chat from current git checkout |
| `deploy.sh` | Transfer and install DEB on VM |
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

### Full Comparison

```bash
./full-test.sh
```

## Ubuntu-Specific Notes

- Ubuntu 22.04 uses `apt` / `dpkg` for package management
- Default display manager path: `/etc/gdm3/custom.conf`
- Wayland socket: `/run/user/1000/wayland-0`
- XWayland typically on `:0` or `:1`
- May need to install `gnome-session-wayland` if missing

## Differences from Fedora 42

| Aspect | Fedora 42 | Ubuntu 22.04 |
|--------|-----------|--------------|
| Package Format | RPM | DEB |
| Package Manager | dnf | apt/dpkg |
| GDM Config | /etc/gdm/custom.conf | /etc/gdm3/custom.conf |
| Guest Agent | qemu-guest-agent | qemu-guest-agent |
| GNOME Version | 47 | 42 |
