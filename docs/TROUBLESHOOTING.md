# Troubleshooting Guide

## Common Issues

### Cannot Get VM IP Address

**Symptom**: `vm_get_ip` returns empty

**Causes**:
1. VM not running
2. qemu-guest-agent not installed/running
3. Network not configured

**Solutions**:
```bash
# Check VM status
./gpu-control.sh --status

# Inside VM, check guest agent
systemctl status qemu-guest-agent

# Install if missing
# Fedora: sudo dnf install qemu-guest-agent
# Ubuntu: sudo apt install qemu-guest-agent
```

### SSH Connection Fails

**Symptom**: `sshpass` times out or permission denied

**Causes**:
1. SSH server not running
2. Firewall blocking
3. Wrong credentials

**Solutions**:
```bash
# Inside VM, enable SSH
# Fedora: sudo systemctl enable --now sshd
# Ubuntu: sudo systemctl enable --now ssh

# Check firewall
sudo firewall-cmd --list-all  # Fedora
sudo ufw status               # Ubuntu
```

### XAUTHORITY Not Found

**Symptom**: X11 fallback fails with "cannot open display"

**Cause**: XAUTHORITY path changes on each boot

**Solution**: Scripts detect XAUTHORITY dynamically from XWayland process:
```bash
get_xauth() {
    local xwayland_cmd=$(pgrep -a Xwayland | head -1)
    echo "$xwayland_cmd" | grep -oP -- "-auth \K[^ ]+"
}
```

### Tests Pass but X11 Fallback Fails

**Symptom**: wayland-fake test SEGFAULTS even with fixed version

**Cause**: DISPLAY not set for X11 fallback

**Solution**: Ensure DISPLAY=:0 is exported:
```bash
export DISPLAY=":0"
export XAUTHORITY="$(get_xauth)"
```

### GPU Not Visible in VM

**Symptom**: `lspci | grep NVIDIA` shows nothing

**Causes**:
1. GPU not attached
2. VM needs restart after attaching
3. IOMMU not enabled

**Solutions**:
```bash
# Check if GPU attached (from host)
qm config 100 | grep hostpci

# Attach GPU
./gpu-control.sh --attach

# Verify IOMMU (from host)
dmesg | grep -i iommu
```

### Build Fails - Electron Fuses

**Symptom**: `flipFuses` error during build

**Cause**: electron/fuses version mismatch

**Solution**: Ensure node_modules is up to date:
```bash
rm -rf node_modules
yarn install
```

### Wayland Session Not Starting

**Symptom**: VM boots to X11 instead of Wayland

**Causes**:
1. Auto-login not configured
2. GDM defaulting to X11
3. Wayland disabled

**Solutions**:
```bash
# Check current session type (inside VM)
echo $XDG_SESSION_TYPE

# Force Wayland in GDM
sudo nano /etc/gdm/custom.conf  # or /etc/gdm3/custom.conf
# Comment out: #WaylandEnable=false

# Ensure auto-login
[daemon]
AutomaticLoginEnable=True
AutomaticLogin=jean
```

### Package Installation Fails

**Symptom**: `dnf install` or `dpkg -i` fails

**Causes**:
1. Sudo password prompt
2. Dependency issues
3. Package already installed (different version)

**Solutions**:
```bash
# Use sudo with password piped
echo password | sudo -S dnf install -y package.rpm

# Force reinstall
sudo dnf install -y --allowerasing package.rpm
sudo dpkg -i --force-overwrite package.deb
```

## Debug Mode

### Trace Wrapper Script

```bash
bash -x /opt/Rocket.Chat/rocketchat-desktop 2>&1 | head -30
```

### Check Wrapper Detection Logic

```bash
export XDG_SESSION_TYPE=wayland
export WAYLAND_DISPLAY=wayland-fake
export XDG_RUNTIME_DIR=/run/user/1000

# Manually run detection
should_force_x11() {
    [[ "${XDG_SESSION_TYPE:-}" != "wayland" ]] && return 0
    [[ -z "${WAYLAND_DISPLAY:-}" ]] && return 0
    local socket="$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY"
    [[ ! -S "$socket" ]] && return 0
    return 1
}

if should_force_x11; then
    echo "Would force X11"
else
    echo "Would use Wayland"
fi
```

### Verbose Test Output

```bash
# Run app directly with output
timeout 10 /opt/Rocket.Chat/rocketchat-desktop 2>&1

# Check for specific messages
timeout 10 /opt/Rocket.Chat/rocketchat-desktop 2>&1 | grep -i "wayland\|x11\|ozone"
```

## Log Locations

| What | Where |
|------|-------|
| Build logs | `test-framework/logs/` |
| Test results | `test-framework/results/<timestamp>/` |
| Electron logs | `~/.config/Rocket.Chat/logs/` |
| System journal | `journalctl -f` |
| Xorg logs | `/var/log/Xorg.0.log` |
