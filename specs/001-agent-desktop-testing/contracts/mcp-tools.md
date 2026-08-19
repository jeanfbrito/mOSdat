# Contract: mOSdat MCP Tool Surface

Transport: stdio JSON-RPC 2.0 (existing `mosdat-mcp` server, `automation/mcp_server.py`).
Every tool result follows the uniform envelope from `data-model.md` / research.md Decision 2:
`{"ok": bool, "error": string|null, "degraded": string[], ...tool fields}`.

Tools marked **(existing)** already exist in `automation/mcp_tools.py` and need their
response shape brought in line with this contract. Tools marked **(new)** must be added.
Tools marked **(extend)** exist but need their underlying implementation completed to match
this contract.

---

## `mosdat_list_vms` (existing — align response shape)

**Input**: `{}`

**Output**:
```json
{
  "ok": true,
  "error": null,
  "degraded": [],
  "vms": [
    {"name": "ubuntu2404", "vmid": 201, "os_type": "linux", "status": "running"}
  ]
}
```

## `mosdat_list_scenarios` (existing — align response shape)

**Input**: `{"platform": "linux" | "windows" | null}`

**Output**:
```json
{
  "ok": true,
  "error": null,
  "degraded": [],
  "scenarios": [
    {"name": "rocketchat-smoke-linux", "path": "shared/scenarios/smoke-linux/rocketchat-smoke-linux.yaml", "platform": "linux", "step_count": 12}
  ]
}
```

## `mosdat_readiness` (new — satisfies FR-003)

Wraps the existing `preflight`/`doctor` checks (`automation/commands/preflight.py`,
`automation/commands/doctor.py`) behind a single agent-facing tool with a go/no-go answer.

**Input**:
```json
{"vm": "ubuntu2404", "expect_pr": 4821, "expect_symbol": null}
```
`expect_pr`/`expect_symbol` are optional — omit to check general VM health only.

**Output**:
```json
{
  "ok": true,
  "error": null,
  "degraded": [],
  "ready": false,
  "vm": {"name": "ubuntu2404", "vmid": 201, "reachable": true, "deployed_version": "4.9.2-pr4820", "busy": false},
  "checks": [
    {"label": "ssh_reachable", "status": "PASS", "detail": ""},
    {"label": "deployed_build_matches_expected", "status": "FAIL", "detail": "expected PR #4821, found PR #4820"}
  ]
}
```
`ready` is `true` only when every check in `checks` is `PASS`. `ok` stays `true` even when
`ready` is `false` — a not-ready environment is a valid, successfully-determined answer,
not a tool error (this is the FR-003 "environment problem" vs "tool failure" distinction).

## `mosdat_build` (extend — satisfies FR-008, currently host-side-only)

Wires the full `run_build()` flow (`automation/commands/build.py`) — clone, build, deploy,
verify-symbol — through to the MCP surface, matching what `mosdat build --pr --deploy
--verify-symbol` already does on the CLI.

**Input**:
```json
{"pr": 4821, "target": "deb", "deploy_to": "ubuntu2404", "verify_symbol": "MyNewFeatureFlag"}
```
`deploy_to`/`verify_symbol` optional — omit `deploy_to` to build without deploying.

**Output**:
```json
{
  "ok": true,
  "error": null,
  "degraded": [],
  "pr": 4821,
  "target": "deb",
  "build_ok": true,
  "deploy": {
    "vm": "ubuntu2404",
    "scp_ok": true,
    "install_ok": true,
    "installed_version": "4.9.2-pr4821",
    "missing_symbols": [],
    "error": ""
  }
}
```
On a build failure, `ok: false`, `error` names the build stage, and `deploy` is `null` (no
deploy attempted against a nonexistent artifact — spec User Story 1, Acceptance Scenario 2).
On a deploy failure, `ok: false`, `build_ok: true`, and `deploy.error` carries the deploy
failure, distinct from a build failure (Acceptance Scenario 3). If the target VM is
currently locked by another operation, `ok: false`, `error: "vm busy: <vmid> held by
another operation"`, no build is even attempted.

## `mosdat_deploy` (existing — align response shape, add lock)

**Input**: `{"vm": "ubuntu2404", "artifact_path": "/path/to/app.deb", "target": "deb"}`

**Output**: same `DeployOutcome` shape as `mosdat_build`'s `deploy` field, wrapped in the
standard envelope. Must acquire the same per-VM lock as `mosdat_build`.

## `mosdat_run_functional` (extend — satisfies FR-001/FR-002/FR-006/FR-007)

**Input**: `{"vm": "ubuntu2404", "scenario": "rocketchat-smoke-linux", "timeout": 900}`

**Output**:
```json
{
  "ok": true,
  "error": null,
  "degraded": [],
  "verdict": "pass",
  "steps": [
    {"index": 0, "outcome": "pass", "reason": null, "artifact": "results/.../step-0.png"},
    {"index": 1, "outcome": "fail", "reason": "element not found: send button", "artifact": "results/.../step-1.png"}
  ],
  "artifacts": ["results/.../step-0.png", "results/.../step-1.png", "results/.../final.png"],
  "elapsed_ms": 45230
}
```
If the VLM verification backend was unavailable/degraded during the run,
`degraded: ["vlm_unavailable"]` is set even on an otherwise-normal completion (FR-007) — the
agent must be able to tell "this passed, but visual verification was skipped" apart from a
clean pass. If the target VM/scenario combination is invalid, `ok: false`,
`error: "unknown scenario 'X' for platform Y — available: [...]"` listing valid
alternatives (FR-005), and no run is attempted.

If the VM/scenario combination is valid but the environment is not currently
runnable (SSH unreachable, or required tool dependencies such as `wmctrl` missing),
the tool returns **without** invoking the runner. This is FR-003 / User Story 2
Acceptance Scenario 3 — an environment problem, not a test failure:

```json
{
  "ok": false,
  "error": "environment not ready: VM ubuntu2404 is unreachable (ssh timeout)",
  "degraded": [],
  "env_not_ready": true,
  "verdict": "error",
  "steps": [],
  "artifacts": [],
  "elapsed_ms": 12,
  "vm": "ubuntu2404",
  "scenario": "rocketchat-smoke-linux"
}
```

Rules:
- `verdict` is `"error"`, **never** `"fail"`. `fail` is reserved for an executed
  scenario that asserted unsuccessfully (`ok: true`, `verdict: "fail"`).
- `env_not_ready: true` is the discriminator. An exception raised *during* the
  run also uses `verdict: "error"` but omits `env_not_ready`.
- `error` is always prefixed `environment not ready:`.
- The pre-check reuses `doctor.check_ssh` / `doctor.check_deps` (Linux deps
  skipped on Windows). It does not run the full doctor/preflight suite.

## `mosdat_ssh` (existing — no contract change; out of scope for this feature's FRs)

Left as-is; not part of the spec's discovery/readiness/build/run/result surface.

---

## Cross-cutting rules

- **FR-010 concurrency**: `mosdat_build`, `mosdat_deploy`, and `mosdat_run_functional` each
  acquire `automation.proxmox.vm._vm_lock(vmid)` for the target VM before proceeding. A
  non-blocking acquire attempt that fails returns `{"ok": false, "error": "vm busy: ...", ...}`
  immediately rather than queuing indefinitely — satisfies SC-006 (no silent racing).
- **FR-005 invalid combinations**: any tool receiving an unknown `vm`/`scenario`/`target`
  value returns `ok: false` with an `error` string that both names the invalid value and
  lists valid alternatives, before attempting any side-effecting operation.
