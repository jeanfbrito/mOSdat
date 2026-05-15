# AI Agent Instructions

> **Single source of truth.** This file is the canonical agent entry point.
> `CLAUDE.md` redirects here. All agents — Claude, Hermes, Codex, Cline, Copilot — must load this file for project context.

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

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **mOSdat** (5191 symbols, 8832 relationships, 173 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/mOSdat/context` | Codebase overview, check index freshness |
| `gitnexus://repo/mOSdat/clusters` | All functional areas |
| `gitnexus://repo/mOSdat/processes` | All execution flows |
| `gitnexus://repo/mOSdat/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
