# AI Agent Instructions

## TOP RULE: NEVER BLOCK ON WAITS

**NEVER use long `sleep` commands or blocking waits while the user sits idle.**

When waiting for VMs, builds, or any async operation:
1. Fire background agents to monitor state
2. Continue with useful work in parallel
3. Check results when needed, don't block

```bash
# WRONG - blocks user
sleep 45 && check_status

# RIGHT - use background monitoring
background_task(agent="explore", prompt="Monitor VM 103 until IP appears...")
# Continue other work immediately
```

**Time is precious. Blocking waits waste user time.**

---

## Project Overview

Test framework for Rocket.Chat Electron Linux builds, specifically the Wayland/X11 crash fix (PR #3171).

## CRITICAL: Proxmox is a Separate Machine

**Proxmox runs on a DIFFERENT machine (192.168.13.85). All hardware info must be queried via API.**

```bash
# Query Proxmox GPU via API (NEVER use local lspci)
TICKET=$(curl -k -s -d "username=root@pam&password=cb6wist3" \
  "https://192.168.13.85:8006/api2/json/access/ticket" | jq -r '.data.ticket')
curl -k -s -b "PVEAuthCookie=$TICKET" \
  "https://192.168.13.85:8006/api2/json/nodes/pve/hardware/pci" | \
  jq '.data[] | select(.vendor_name | test("NVIDIA"))'
```

**Proxmox GPU:** RTX 3060 at PCI `0000:01:00`

## Architecture

```
Host (Ubuntu 24.04)          Proxmox (192.168.13.85)
┌─────────────────┐          ┌─────────────────────┐
│ mOSdat/ │  ──API── │ VMs (Fedora/Ubuntu) │
│ build/deploy/   │  ──SSH── │ + RTX 3060 GPU      │
└─────────────────┘          └─────────────────────┘
```

See [docs/HARDWARE.md](docs/HARDWARE.md) for full specs.

## Key Files

| File | Purpose |
|------|---------|
| `shared/config.sh` | Config with env var defaults |
| `shared/proxmox-api.sh` | REST API helpers |
| `os/*/config.sh` | OS-specific (VMID, package format) |
| `os/*/build.sh` | Build RPM/DEB/AppImage |
| `os/*/deploy.sh` | Transfer + install on VM |
| `os/*/gpu-control.sh` | Attach/detach GPU |
| `automation/main.py` | Python runner — canonical orchestrator |

## Code Style

- `set -euo pipefail`
- `log()`, `log_error()`, `log_success()`
- Exit: 0=success, 1=fail, 2=unknown

## Don't

- Hardcode credentials
- Commit binaries (.rpm, .deb)
- Modify `shared/` for OS-specific changes

---

## Runbooks

For task-specific procedures, see `docs/runbooks/`:

- [matrix-run.md](docs/runbooks/matrix-run.md) — full test matrix execution, copy-paste runbook, environment variables, build/deploy/test commands per OS.
- [gpu-rotation.md](docs/runbooks/gpu-rotation.md) — GPU passthrough lifecycle, rotation safe sequence, troubleshooting.
- [completion-criteria.md](docs/runbooks/completion-criteria.md) — what "done" means: package formats, GPU configs, display scenarios, the full matrix per OS.
- [vm-setup.md](docs/runbooks/vm-setup.md) — VM provisioning, ISO storage, adding a new OS.
- [triage.md](docs/runbooks/triage.md) — exit code interpretation, output reading, agent delegation patterns.
