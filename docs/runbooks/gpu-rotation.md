# GPU Rotation

← Back to [AGENTS.md](../../AGENTS.md)

## GPU Constraint

**CRITICAL:** Only ONE VM can have the GPU attached at a time.

```bash
# Before attaching to new VM, always detach from current
./os/<current-os>/gpu-control.sh --detach
./os/<new-os>/gpu-control.sh --attach
```

## GPU Passthrough Workflow

```bash
# 1. Attach GPU to VM
./os/<os>/gpu-control.sh --attach

# 2. Wait for VM to boot into desktop session
# 3. Run GPU tests via SSH
ssh jean@<VM_IP> '/tmp/tests/run-gpu-tests.sh'

# 4. Detach GPU for next VM
./os/<os>/gpu-control.sh --detach
```

## GPU Rotation Between VMs (Complete Safe Sequence)

**CRITICAL**: Only ONE VM can have the GPU at a time. The GPU is on **Proxmox** at PCI `0000:01:00` (RTX 3060), NOT on local host!

```bash
# === SETUP: Get auth tokens ===
AUTH=$(curl -k -s -d "username=root@pam&password=cb6wist3" \
  "https://192.168.13.85:8006/api2/json/access/ticket")
TICKET=$(echo "$AUTH" | jq -r '.data.ticket')
CSRF=$(echo "$AUTH" | jq -r '.data.CSRFPreventionToken')

# === STEP 1: Detach GPU from current VM ===
CURRENT_VMID=100  # VM that currently has GPU

# 1a. Stop VM
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X POST "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${CURRENT_VMID}/status/stop"

# 1b. Wait for stopped state (poll until status=stopped)
for i in {1..20}; do
  STATUS=$(curl -k -s -b "PVEAuthCookie=$TICKET" \
    "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${CURRENT_VMID}/status/current" | jq -r '.data.status')
  [[ "$STATUS" == "stopped" ]] && break
  sleep 3
done

# 1c. Remove GPU config
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X PUT -d "delete=hostpci0" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${CURRENT_VMID}/config"

# 1d. Start VM without GPU (optional - can leave stopped)
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X POST "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${CURRENT_VMID}/status/start"

# === STEP 2: Attach GPU to next VM ===
NEXT_VMID=101  # VM that needs GPU

# 2a. Stop next VM
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X POST "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${NEXT_VMID}/status/stop"

# 2b. Wait for stopped state
for i in {1..20}; do
  STATUS=$(curl -k -s -b "PVEAuthCookie=$TICKET" \
    "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${NEXT_VMID}/status/current" | jq -r '.data.status')
  [[ "$STATUS" == "stopped" ]] && break
  sleep 3
done

# 2c. Attach GPU (use FULL address format: 0000:01:00)
# DO NOT use "03:00" - that's the local GTX 970 which doesn't exist on Proxmox!
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X PUT -d "hostpci0=0000:01:00" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${NEXT_VMID}/config"

# 2d. Start VM with GPU
curl -k -s -b "PVEAuthCookie=$TICKET" -H "CSRFPreventionToken: $CSRF" \
  -X POST "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${NEXT_VMID}/status/start"

# === STEP 3: Wait for VM to be accessible ===
# Poll qemu-guest-agent for IP
for i in {1..24}; do
  IP=$(curl -k -s -b "PVEAuthCookie=$TICKET" \
    "https://192.168.13.85:8006/api2/json/nodes/pve/qemu/${NEXT_VMID}/agent/network-get-interfaces" 2>/dev/null | \
    jq -r '.data.result[] | select(.name != "lo") | .["ip-addresses"][]? | select(.["ip-address-type"] == "ipv4") | .["ip-address"]' | head -1)
  [[ -n "$IP" && "$IP" != "null" ]] && break
  sleep 5
done
echo "VM $NEXT_VMID IP: $IP"

# === STEP 4: Verify GPU visible ===
ssh jean@$IP "lspci | grep -i nvidia"
# Expected: "VGA compatible controller: NVIDIA Corporation GA106 [GeForce RTX 3060..."
```

## GPU Passthrough Troubleshooting

If GPU tests fail with "No NVIDIA GPU visible":

1. **Check GPU PCI address on Proxmox host:**
   ```bash
   lspci | grep -i nvidia
   # Example output: 03:00.0 VGA compatible controller: NVIDIA...
   ```

2. **Verify IOMMU enabled:**
   ```bash
   dmesg | grep -i iommu
   cat /proc/cmdline  # Should contain intel_iommu=on or amd_iommu=on
   ```

3. **Check vfio-pci driver binding:**
   ```bash
   lspci -nnk -s 03:00  # Should show: Kernel driver in use: vfio-pci
   ```

4. **Update GPU_PCI_ADDRESS in shared/config.sh if needed:**
   ```bash
   export GPU_PCI_ADDRESS="0000:03:00"  # Match your actual GPU address
   ```

5. **Verify VM config in Proxmox:**
   ```bash
   # Check if hostpci0 is set
   curl -k -s -b "PVEAuthCookie=$TICKET" \
     "https://$PROXMOX_HOST:8006/api2/json/nodes/pve/qemu/$VMID/config" | jq '.data.hostpci0'
   ```

## GPU Test Result Interpretation (CRITICAL)

**Not all GPU test failures indicate a problem with the crash fix.**

| Test | What It Validates | Failure Meaning |
|------|-------------------|-----------------|
| `wayland-fake` | **THE BUG** - crash fix | FAIL = regression, MUST investigate |
| `wayland-fallback` | X11 fallback mechanism | FAIL = regression, investigate |
| `gpu-wayland` | Real Wayland + GPU | FAIL = likely GPU/driver issue, not the fix |
| `gpu-x11` | Real X11 + GPU | FAIL = likely XAUTHORITY/sandbox issue |

**Known Non-Blocking Failures:**

| Scenario | Affected | Cause | Action |
|----------|----------|-------|--------|
| Snap + GPU crashes | Ubuntu 24.04 Snap | Snap sandbox blocks GPU access | Document as known limitation |
| gpu-wayland SKIP | Ubuntu 22.04, openSUSE | X11 session, no Wayland socket | Expected - these distros default to X11 |
| gpu-x11 FAIL on AppImage | Fedora, openSUSE | XAUTHORITY not found | Investigate, but not crash fix related |

**Decision Rule:**
- `wayland-fake` FAIL on ANY package → **BLOCKER** (the crash fix is broken)
- Other GPU test FAIL → Document, but not a release blocker if `wayland-fake` passes
