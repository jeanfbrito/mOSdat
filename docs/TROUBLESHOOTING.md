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
mosdat list-vms examples/rocketchat.toml

# Inside VM, check guest agent
systemctl status qemu-guest-agent

# Install if missing
# Fedora: sudo dnf install qemu-guest-agent
# Ubuntu: sudo apt install qemu-guest-agent
```

### Polling for VM IP After Restart

After starting a VM, its IP isn't immediately available. Poll the qemu-guest-agent:

```bash
# Poll until IP appears (max 2 minutes)
VMID=100
for i in {1..24}; do
  IP=$(curl -k -s -b "PVEAuthCookie=$TICKET" \
    "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${VMID}/agent/network-get-interfaces" 2>/dev/null | \
    jq -r '.data.result[] | select(.name != "lo") | .["ip-addresses"][]? | select(.["ip-address-type"] == "ipv4") | .["ip-address"]' | head -1)
  
  if [[ -n "$IP" && "$IP" != "null" ]]; then
    echo "VM $VMID IP: $IP"
    break
  fi
  echo "Waiting for VM $VMID IP... ($i/24)"
  sleep 5
done

# Verify SSH accessible
ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no jean@$IP "echo OK"
```

**Common causes of no IP:**
1. VM still booting (wait longer)
2. qemu-guest-agent not installed/running
3. Network not configured in VM
4. VM crashed during boot

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

### Test Scripts Not Found on VM

**Symptom**: `/tmp/tests/run-all.sh: No such file or directory`

**Cause**: Test scripts must be transferred to each VM before testing.

**Solution** (required before EVERY test session):
```bash
# Transfer test scripts to VM (REQUIRED before running tests)
scp -r /home/jean/projects/linux-testing/mOSdat/shared/scenarios/smoke-linux jean@<VM_IP>:/tmp/tests

# Verify
ssh jean@<VM_IP> "ls -la /tmp/tests/"
```

**Note**: Scripts are in /tmp and lost on VM reboot. Transfer again after each VM restart.

---

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

> **CRITICAL: Two-Machine Architecture**
> 
> Proxmox (192.168.13.85) is a SEPARATE machine from the local test host.
> Commands like `qm`, `pvesh`, and Proxmox's `dmesg` must be run either:
> - Via the Proxmox REST API (recommended for automation)
> - By SSH-ing to 192.168.13.85 directly
> 
> **NEVER** run `lspci` on the local host expecting to see the test GPU (RTX 3060).
> The local host has a GTX 970 which is NOT used for testing.

**Symptom**: `lspci | grep NVIDIA` shows nothing in VM

**Causes**:
1. GPU not attached to VM
2. VM needs restart after attaching
3. Wrong PCI address used

**Solutions**:
```bash
# 1. Check if GPU attached (via Proxmox API - NOT local CLI!)
AUTH=$(curl -k -s -d "username=root@pam&password=$PROXMOX_PASSWORD" \
  "https://192.168.13.85:8006/api2/json/access/ticket")
TICKET=$(echo "$AUTH" | jq -r '.data.ticket')
curl -k -s -b "PVEAuthCookie=$TICKET" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/100/config" | jq '.data.hostpci0'
# Returns null if no GPU, "0000:01:00,pcie=1" if attached

# 2. Attach GPU via API (must stop VM first!)
CSRF=$(curl -k -s -d "username=root@pam&password=$PROXMOX_PASSWORD" \
  "https://192.168.13.85:8006/api2/json/access/ticket" | jq -r '.data.CSRFPreventionToken')
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X PUT -d "hostpci0=0000:01:00,pcie=1" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/100/config"

# 3. Inside VM after GPU attached - verify GPU visible:
ssh jean@<VM_IP> "lspci | grep -i nvidia"
# Expected: "VGA compatible controller: NVIDIA Corporation GA106 [GeForce RTX 3060..."
```

**Common mistake**: Using wrong PCI address. The RTX 3060 on Proxmox is at `0000:01:00`, NOT `03:00`.

### Snap Package Crashes with GPU (Known Limitation)

**Symptom**: Snap package crashes with SIGSEGV on `gpu-wayland` and `gpu-x11` tests

**Affected**: Ubuntu 24.04 with Snap package + GPU passthrough

**Cause**: Snap's sandbox restricts direct GPU access. The Snap confinement prevents the app from accessing GPU resources properly.

**This is NOT a regression** - the `wayland-fake` test still passes, confirming the crash fix works.

**Workaround**: None currently. Document as known limitation for Snap users with GPU.

**Verification**: If `wayland-fake` passes but `gpu-wayland`/`gpu-x11` fail on Snap, this is the sandbox issue, not the crash fix.

---

### gpu-wayland Test Skips on X11 Sessions

**Symptom**: `RESULT:gpu-wayland:FAIL:NO_WAYLAND_SOCKET`

**Affected**: Ubuntu 22.04, openSUSE Leap (default to X11 sessions)

**Cause**: These OSes boot into X11 sessions, so there's no Wayland socket at `/run/user/1000/wayland-0`.

**This is expected behavior**, not a failure. The `wayland-fake` and `wayland-fallback` tests still validate the crash fix on these systems.

---

### gpu-x11 Test Fails with AppImage

**Symptom**: AppImage crashes with SIGSEGV on `gpu-x11` test but passes `wayland-fake`

**Affected**: Fedora 42, openSUSE Leap with AppImage + GPU

**Cause**: XAUTHORITY file location varies. AppImage may not find the X authority file needed for GPU-accelerated X11 rendering.

**This is NOT a regression** - the crash fix (`wayland-fake`) still works.

**Investigation**: Check if XAUTHORITY is properly set:
```bash
# Inside VM
echo $XAUTHORITY
ls -la ~/.Xauthority
ls -la /run/user/$(id -u)/gdm/Xauthority
```

---

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
| Build logs | `mOSdat/logs/` |
| Test results | `mOSdat/results/<timestamp>/` |
| Electron logs | `~/.config/Rocket.Chat/logs/` |
| System journal | `journalctl -f` |
| Xorg logs | `/var/log/Xorg.0.log` |
