# Quickstart: Validating Agent-Ready Desktop Testing

Proves the feature works end-to-end: an agent (or a human standing in for one, via a raw
JSON-RPC call) can discover, check readiness, build+deploy, run, and interpret a result —
without reading source, logs, or screenshots directly.

## Prerequisites

- mOSdat installed (`pip install -e .`) with `mosdat-mcp` on `PATH`.
- At least one configured VM reachable from the host (see `examples/rocketchat.toml`).
- `.env` populated per `AGENTS.md` (Proxmox credentials, VLM backend config).

## 1. Start the MCP server

```bash
mosdat-mcp
```
Runs over stdio — drive it either via a Claude Code MCP client config pointed at this
command, or manually by piping JSON-RPC requests to its stdin for this validation pass.

## 2. Discover what's runnable (FR-004 / User Story 4)

Call `mosdat_list_vms` and `mosdat_list_scenarios`. Expected: both return `ok: true` with
non-empty lists, and every scenario's `platform` matches an existing VM's `os_type`.

Negative check: call `mosdat_run_functional` with a scenario name that doesn't exist.
Expected: `ok: false`, `error` names the bad scenario and lists valid alternatives (FR-005)
— no run is attempted.

## 3. Check readiness before doing anything expensive (FR-003 / User Story 3)

Call `mosdat_readiness` for a chosen VM, optionally with `expect_pr` set to a PR you know is
*not* currently deployed. Expected: `ok: true`, `ready: false`, and a `checks` entry naming
the version mismatch — this is the "environment problem, not a test failure" signal
(spec Acceptance Scenario, User Story 3.2 / SC-003).

## 4. Build, deploy, and run end-to-end (FR-008 / User Story 1)

Call `mosdat_build` with a real PR number, `deploy_to` set to the VM from step 3, and
(optionally) `verify_symbol` set to a string you expect in that PR's build.

Expected on success: `build_ok: true`, `deploy.install_ok: true`,
`deploy.installed_version` reflecting the new build.

Then re-run the step-3 readiness check: `ready` should now be `true` and
`deployed_version` should match what `mosdat_build` just installed (no stale-binary false
negative — Acceptance Scenario 4).

Then call `mosdat_run_functional` against that VM with a real scenario. Expected: `ok: true`,
`verdict` one of `pass`/`fail`/`error`, `steps` populated with one entry per executed step,
`artifacts` containing real, openable file paths.

## 5. Prove concurrency safety (FR-010 / SC-006)

While step 4's `mosdat_run_functional` call is still in flight against a VM, issue a second
`mosdat_build` or `mosdat_deploy` call targeting the *same* VM from a second client/process.
Expected: the second call returns immediately with `ok: false`,
`error: "vm busy: ..."` — it does not block indefinitely, and it does not race with the
in-flight run.

## 6. Prove degraded-backend signaling (FR-007)

With the VLM backend intentionally misconfigured or unreachable (e.g., unset `VLM_API_KEY`
in a scratch `.env` for this check only), re-run step 4's scenario run. Expected: the tool
does not silently report a clean pass — `degraded` includes `"vlm_unavailable"` (or the
run fails outright with a clear `error`, per how the scenario's steps depend on visual
verification), not an unqualified `ok: true, verdict: "pass"`.

## Success

All six checks above match their expected output using only the structured tool
results — no scrolling logs, no opening screenshots to determine pass/fail. That is the
feature's Success Criteria (SC-001 through SC-006) demonstrated live.
