# Implementation Plan: Agent-Ready Desktop Testing

**Branch**: `001-agent-desktop-testing` | **Date**: 2026-08-19 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-agent-desktop-testing/spec.md`

## Summary

mOSdat already ships a partial MCP server (`automation/mcp_server.py` + `mcp_tools.py`,
`mosdat-mcp` entry point, 10 tools). This feature closes the gap between that existing
surface and what the spec requires: a `mosdat_readiness` tool (new), a completed
`mosdat_build` flow that wires through the full clone→build→deploy→verify-symbol sequence
(extend), a uniform structured result envelope across the six tools this feature touches
(list_vms, list_scenarios, build, deploy, run_functional, and the new readiness tool)
(extend), and per-VM cross-process locking on every build/deploy/run tool call reusing the
existing `_vm_lock()` primitive (extend). No new service, framework, or dependency — see
research.md for why the existing hand-rolled JSON-RPC/stdio server is kept rather than
migrated to the official MCP SDK or FastMCP.

## Technical Context

**Language/Version**: Python >=3.11 (`pyproject.toml`)

**Primary Dependencies**: Existing stack only — `pyyaml`, `requests`, `openai`. No new
dependency added (Decision 1, research.md): the hand-rolled JSON-RPC/stdio server in
`automation/mcp_server.py` is extended, not replaced by the official MCP SDK or FastMCP.
Tool I/O uses plain JSON-serializable dicts (matching the existing 10 tools), not pydantic
models — pydantic remains available in the stack for other subsystems but isn't
introduced into the MCP tool layer by this feature.

**Storage**: N/A for this feature's own state. Reuses existing on-disk state:
`automation/proxmox/vm.py`'s `fcntl.flock`-based per-VMID lock files (`/tmp/mosdat-vm-*.lock`
or `$MOSDAT_LOCK_DIR`), and the existing VLM SQLite cache (untouched).

**Testing**: pytest (existing `pyproject.toml` config: `timeout=30`, markers `live`,
`issue`). New unit tests for tool-handler response shape and lock-rejection behavior run
without a real VM; the quickstart.md end-to-end validation requires `-m live` against a
real Proxmox VM, consistent with the project's existing scoped-tests-first workflow
(`docs/test-strategy.md`).

**Target Platform**: The MCP server itself runs on the existing host (Ubuntu 24.04, per
AGENTS.md), spawned as a stdio subprocess by a Claude Code (or other MCP-client) session.
Target VMs remain Linux/Windows via Proxmox — unchanged.

**Project Type**: Single Python project (existing `automation/` package) — CLI + MCP
server sharing the same underlying command implementations (`automation/commands/*.py`).

**Performance Goals**: A readiness check (`mosdat_readiness`) must complete fast relative
to a full scenario run (SC-005) — the underlying `doctor`/`preflight` checks it wraps are
already live-SSH-based and typically complete in well under a minute; no new performance
target beyond preserving that.

**Constraints**: Must never silently proceed past a degraded/unavailable VLM backend
without flagging it (FR-007). Must never allow two operations to race against the same VM
(FR-010) — enforced via the existing cross-process `_vm_lock()`, not an in-process-only
primitive (see research.md Decision 1/4 for why this matters here specifically).

**Scale/Scope**: Single host, small number of concurrent agent/human sessions — not a
hosted multi-tenant service (per spec Assumptions). In scope: `mosdat_readiness` (new),
`mosdat_build`/`mosdat_deploy`/`mosdat_run_functional` (extended), response-envelope
alignment for `mosdat_list_vms`/`mosdat_list_scenarios` (existing, minor). Out of scope:
`mosdat_ssh`, `mosdat_vm_start`/`mosdat_vm_stop`/`mosdat_vm_status`, `mosdat_run_smoke`
(existing tools untouched by this feature's FRs), and any HTTP/SSE transport.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is still the unfilled template (no project constitution
has been ratified via `/speckit-constitution`). Per the constitution-check rule, this gate
is skipped gracefully — no principles to check against. Recommend running
`/speckit-constitution` at some point, but it is not a blocker for this feature.

## Project Structure

### Documentation (this feature)

```text
specs/001-agent-desktop-testing/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/
│   └── mcp-tools.md     # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

Single existing Python project — no new top-level directory. Changes land inside the
existing `automation/` package:

```text
automation/
├── mcp_server.py          # existing — JSON-RPC/stdio dispatch; register new mosdat_readiness tool
├── mcp_tools.py            # existing — extend: response-envelope helper, mosdat_build
│                            #   full-flow wiring, new mosdat_readiness tool, envelope
│                            #   alignment on 6 tools (list_vms, list_scenarios, build,
│                            #   deploy, run_functional, readiness)
├── proxmox/
│   └── vm.py               # existing — _vm_lock() reused as-is by build/deploy/run handlers
├── commands/
│   ├── build.py             # existing — run_build()/DeployResult reused by mosdat_build
│   ├── preflight.py          # existing — reused by mosdat_readiness
│   └── doctor.py             # existing — reused by mosdat_readiness
└── runners/
    └── functional.py         # existing — expected to be reused by mosdat_run_functional
                              #   (verify actual return type first — see tasks.md T009)

tests/
└── test_mcp_tools.py        # new/extended — unit tests for envelope shape, lock-rejection
                              #   behavior (mockable, no live VM); `-m live` tests for the
                              #   full quickstart.md flow against a real VM
```

**Structure Decision**: Extend the existing single-package layout in place
(`automation/mcp_tools.py` + `automation/mcp_server.py`). No new project, service, or
package boundary — this is additive/corrective work on an existing module, consistent
with "surgical changes only."

## Complexity Tracking

*No constitution violations to justify — the Constitution Check gate was skipped (unfilled
template) and this design introduces no new project, service, or dependency.*
