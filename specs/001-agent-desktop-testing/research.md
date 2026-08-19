# Research: Agent-Ready Desktop Testing

## Existing state (discovered, not assumed)

Before evaluating options, investigation of the codebase found mOSdat already ships a
**partial MCP server**: `automation/mcp_server.py` (JSON-RPC 2.0 over stdio, hand-rolled —
no `mcp`/`fastmcp` SDK dependency) + `automation/mcp_tools.py`, wired via the
`mosdat-mcp` console entry point in `pyproject.toml`. It exposes 10 tools today:
`mosdat_list_vms`, `mosdat_vm_start/stop/status`, `mosdat_build`, `mosdat_deploy`,
`mosdat_run_smoke`, `mosdat_run_functional`, `mosdat_list_scenarios`, `mosdat_ssh`.

This changes the shape of the feature from "build an MCP server" to "close the gap between
the existing MCP surface and what the spec's FRs require." Concretely, against spec FRs:

| FR | Covered today? | Gap |
|----|----|----|
| FR-001 run scenario → structured verdict | Partial — `mosdat_run_functional` exists | Verify/standardize the verdict shape it returns |
| FR-002 per-step results | Partial — `FunctionalRunner`/`BugConfirmationResult` has `step_failures` internally | Not confirmed to surface through the tool response today |
| FR-003 readiness check via the agent interface | **Missing** | `preflight`/`doctor` are CLI-only; no `mosdat_preflight`/`mosdat_doctor`-equivalent tool |
| FR-004 discover scenarios/VMs | Covered — `mosdat_list_vms`, `mosdat_list_scenarios` | — |
| FR-005 reject invalid combos clearly | Unconfirmed | Needs a pass over existing tool handlers |
| FR-006 artifact references in result | Unconfirmed | Screenshot/log paths exist on disk; not confirmed to be returned |
| FR-007 flag degraded VLM backend | Partial — VLM warmup/probe logs warnings, proceeds | Not confirmed to surface as a distinct signal in the tool result |
| FR-008 autonomous build/deploy | Partial — `mosdat_build` tool exists but per the finder is "not yet full `run_build()` integration" (host-side yarn build only, not the clone→build→deploy→verify-symbol flow the CLI's `mosdat build --pr --deploy --verify-symbol` performs) | Wire the full flow through |
| FR-009 dedicated tool/API interface | **Already largely satisfied** by the existing MCP server | Extend, don't rebuild |
| FR-010 concurrency safety per target | Partial — `automation/proxmox/vm.py::_vm_lock()` (fcntl.flock, per-VMID, cross-process) exists and guards `snapshot()`/`rollback()`/`reset_vm()` | Not confirmed to wrap build/deploy/run tool handlers |

This reframes Phase 1 design as an extension of `mcp_server.py`/`mcp_tools.py`, not a new
service.

## Decision 1: Keep the existing hand-rolled JSON-RPC/stdio server; do not migrate to the official MCP Python SDK or FastMCP

**Decision**: Extend `automation/mcp_server.py` / `mcp_tools.py` in place.

**Rationale**:
- It already works, is entry-point wired (`mosdat-mcp`), and has 10 tools in production
  use — a migration is a rewrite of working infrastructure the spec does not ask for.
- Both the official `modelcontextprotocol/python-sdk` v2 and FastMCP would add a new
  runtime dependency for capabilities this feature doesn't need yet (see Decision 3 on
  progress reporting).
- Per-VM concurrency safety (FR-010) requires a **cross-process** lock, because the human
  CLI (`mosdat build`, `mosdat preflight`, etc.) and the MCP server can run as separate,
  simultaneous processes against the same VM. mOSdat's existing `_vm_lock()` (an
  `fcntl.flock`-based advisory lock keyed by VMID) is already cross-process-safe. The
  SDK-recommended `asyncio.Semaphore` pattern from the research pass is **in-process only**
  and would *not* protect against a concurrent human CLI invocation — it is the wrong tool
  here, not merely a style preference.
- Adopting an SDK is a legitimate future option (better schema validation ergonomics,
  built-in progress streaming) but is a separable improvement with its own migration risk;
  bundling it into this feature would violate the "surgical changes only" principle.

**Alternatives considered**:
- *Official `modelcontextprotocol/python-sdk` v2*: gives typed tool schemas via Python type
  hints and a documented `is_error` result convention "for free." Rejected for now — the
  existing hand-rolled server can adopt the same `ok`/`error` response-shape convention
  without the dependency and rewrite risk.
- *FastMCP*: adds SSE-based progress/resumability well-suited to minutes-long operations.
  Rejected for now (see Decision 3) since the spec doesn't require live progress, only a
  clear final verdict.

## Decision 2: Adopt a uniform structured-result convention across the six tools this feature touches

**Decision**: Every MCP tool response this feature touches (list_vms, list_scenarios,
build, deploy, run_functional, and the new readiness tool) returns a JSON object with at least
`{"ok": bool, "error": str | null, ...tool-specific fields}`. `ok: false` always carries a
non-null, human-and-agent-readable `error` string; partial/degraded conditions (e.g., VLM
backend unavailable) are surfaced as a distinct field (e.g., `"degraded": ["vlm_unavailable"]`)
rather than folded into `ok`/`error`, so an agent can tell "this succeeded, but with a
caveat" apart from "this failed."

**Rationale**: Matches the convention the SDK research surfaced as idiomatic (structured
`is_error` rather than raised exceptions) without requiring the SDK itself. Directly
satisfies FR-001/FR-002/FR-005/FR-007 (distinct, parseable outcomes) with a single
consistent shape an agent only has to learn once.

**Alternatives considered**: Per-tool bespoke result shapes (status quo) — rejected, this
is exactly the ambiguity the spec's User Story 1/2 acceptance scenarios rule out.

## Decision 3: No mid-call progress streaming; tool calls block until completion

**Decision**: Build/deploy/run tools remain synchronous — the MCP call blocks until the
operation finishes (potentially several minutes) and returns the final structured result.
No job-queue/poll-status pattern, no SSE progress streaming.

**Rationale**: The research pass confirmed neither MCP SDK offers built-in
job-id-plus-polling; it would have to be hand-built either way. The spec's success criteria
(SC-001–SC-006) require a correct final verdict, not incremental progress — adding a job
queue is complexity the spec doesn't ask for (YAGNI). If long blocking calls prove to be a
problem in practice (e.g., client-side timeouts), a follow-up feature can add polling
without changing the tool contracts' final-result shape.

**Alternatives considered**: FastMCP's `ctx.report_progress()` + SSE resumability —
rejected for now per the above; noted as a future option if blocking calls prove painful.

## Decision 4: Reuse `_vm_lock()` for build/deploy/run tool handlers; introduce no new locking primitive

**Decision**: Wrap the MCP tool handlers for build, deploy, and scenario-run with the
existing `automation.proxmox.vm._vm_lock(vmid)` context manager, the same one already used
by snapshot/rollback/reset. A conflicting concurrent request against the same VM fails fast
with a clear "target busy" error (satisfying FR-010) rather than blocking indefinitely or
racing.

**Rationale**: Reuses a proven, cross-process-safe primitive already in the codebase
instead of introducing a second locking mechanism (e.g., a DB- or Redis-backed lock) that
the research pass's generic guidance suggested for multi-host MCP deployments — overkill
for mOSdat's single-host Proxmox setup (per spec Assumptions: not a hosted multi-tenant
service).

**Alternatives considered**: New `asyncio.Semaphore`-per-VM (in-process only, doesn't
protect against the CLI — rejected, see Decision 1); a shared-DB lock (unnecessary
complexity for a single-host tool — rejected).

## Decision 5: Transport stays stdio

**Decision**: Keep the existing stdio transport for `mosdat-mcp`; do not add an HTTP/SSE
listener.

**Rationale**: Matches both the SDK research's own recommendation for local, single-host,
single-client usage (Claude Code spawning the server as a subprocess) and the spec's
Assumptions (not a hosted multi-tenant service).

**Alternatives considered**: HTTP+SSE transport for remote/multi-agent access — explicitly
out of scope per the spec's assumptions; would be its own future feature if that need
arises.
