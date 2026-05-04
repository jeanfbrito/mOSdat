# GPU Rotation

← Back to [AGENTS.md](../../AGENTS.md)

## GPU Constraint

**CRITICAL:** Only ONE VM can have the GPU attached at a time.

Use `mosdat run` for normal matrix execution. The Python Proxmox layer handles
VM state and GPU config; do not use the old per-OS shell helpers.

## GPU Passthrough Workflow

```bash
mosdat validate examples/rocketchat.toml
mosdat list-vms examples/rocketchat.toml
mosdat run examples/rocketchat.toml --only fedora42
```

## GPU Rotation Between VMs (Complete Safe Sequence)

**CRITICAL**: Only ONE VM can have the GPU at a time. The GPU is on **Proxmox** at PCI `0000:01:00` (RTX 3060), NOT on local host!

For manual recovery, use the Proxmox UI or a short Python script that imports
`automation.proxmox.api.ProxmoxAPI` and `automation.config.load_config`.
Credentials belong in local config/environment, not in this runbook.

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
   Use `mosdat list-vms examples/rocketchat.toml` for configured VM inventory,
   then inspect the VM config in Proxmox if GPU attachment looks wrong.

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
