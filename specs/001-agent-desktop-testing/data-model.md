# Data Model: Agent-Ready Desktop Testing

These are response/request shapes for the MCP tool surface, grounded in the existing
implementation types found in `automation/`. No new persistent storage is introduced;
these are transient structures serialized into MCP tool call results.

## Test Scenario

Maps to the existing scenario YAML + `FunctionalStep` (`automation/runners/scenario_loader.py`).

| Field | Type | Notes |
|---|---|---|
| `name` | string | Scenario identifier, resolved via `resolve_test_path()` |
| `path` | string | Relative path under `shared/scenarios/...` |
| `platform` | string | `linux` \| `windows` — which VM class it targets |
| `step_count` | int | Number of expanded steps (post-import/routine expansion) |

Discovered via the existing `mosdat_list_scenarios` tool (`mcp_tools.py::_list_scenarios`);
no schema change needed, confirm response includes `platform` (add if missing).

## Target Environment (VM)

Maps to `VMConfig` (`automation/config.py`).

| Field | Type | Notes |
|---|---|---|
| `name` | string | VM alias, e.g. `ubuntu2404` |
| `vmid` | int | Proxmox VMID — the key used by `_vm_lock()` |
| `os_type` | string | `linux` \| `windows` |
| `reachable` | bool | Live SSH/connectivity check result (readiness) |
| `deployed_version` | string \| null | Currently installed app version/build identity, if known |
| `busy` | bool | Whether `_vm_lock(vmid)` is currently held by another operation |

`reachable`/`deployed_version` are the readiness-check fields (new); `busy` is new,
derived from attempting a non-blocking probe of the existing lock file.

## Run Result

Maps to the ordinary (non-`confirm`) per-VM path used by `cmd_functional` and
`mosdat_run_functional`: `FunctionalRunner.run_test()` in
`automation/runners/functional_lifecycle.py`, which returns
`tuple[bool, str]` — `(passed, summary_log)`. That return type is **not**
`BugConfirmationResult`. `BugConfirmationResult` (`automation/runners/functional.py`)
is produced only by `_run_bug_confirmation_scenario` for issue-confirm runs and
does not apply to ordinary functional scenarios.

The MCP handler wraps that `(passed, log)` pair in the uniform result envelope
from research.md Decision 2, and reconstructs per-step outcomes from
`events.jsonl` (`step_end` events) plus files in `screenshot_dir`.

| Field | Type | Notes |
|---|---|---|
| `ok` | bool | Overall envelope success (the call itself completed without error). A test `fail` is still `ok: true` — the tool finished and produced a verdict |
| `error` | string \| null | Set when `ok` is false (tool/environment error, not a scenario assertion failure) |
| `degraded` | array[string] | e.g. `["vlm_unavailable"]` — present even when `ok` is true |
| `verdict` | string | `"pass"` \| `"fail"` \| `"error"` — `passed is True` → `pass`; `passed is False` → `fail`; an exception during the run → `error` |
| `steps` | array[StepOutcome] | One entry per executed step, from `events.jsonl` `step_end` records when present; otherwise synthesized from `passed` + step count |
| `artifacts` | array[string] | Screenshot/log file paths collected from the run's `screenshot_dir` |
| `elapsed_ms` | int | Wall time of the handler's `run_test` invocation, measured by the MCP layer |

### StepOutcome (nested)

| Field | Type | Notes |
|---|---|---|
| `index` | int | Step position in the scenario |
| `outcome` | string | `"pass"` \| `"fail"` \| `"skipped"` |
| `reason` | string \| null | Failure detail, from existing `step_failures` entries |
| `artifact` | string \| null | Screenshot path for this step, if captured |

## Build/Artifact

Maps to `BuildTarget` + `DeployResult` (`automation/commands/build.py`).

| Field | Type | Notes |
|---|---|---|
| `pr` | int \| null | PR number, if building from a PR |
| `target` | string | `deb` \| `rpm` \| `appimage` \| `exe` |
| `build_ok` | bool | Whether the artifact built successfully |
| `deploy` | DeployOutcome \| null | Present once deploy is attempted |

### DeployOutcome (nested, maps to `DeployResult`)

| Field | Type | Notes |
|---|---|---|
| `vm` | string | Target VM name |
| `scp_ok` | bool | From `DeployResult.scp_ok` |
| `install_ok` | bool | From `DeployResult.install_ok` |
| `installed_version` | string | From `DeployResult.installed_version` |
| `missing_symbols` | array[string] | From `DeployResult.missing_symbols` — non-empty means `--verify-symbol` failed |
| `error` | string | From `DeployResult.error` |

## Relationships

- A **Run Result** references exactly one **Test Scenario** and one **Target Environment**.
- A **Build/Artifact** targets one **Target Environment** via its `DeployOutcome`; a
  subsequent **Run Result** against that same environment should show
  `deployed_version` (Target Environment) matching the just-built artifact's version
  (this is the "no silent stale-binary false negative" check from spec User Story 1,
  Acceptance Scenario 4).
- **Concurrency**: only one in-flight operation (build, deploy, or run) may hold the
  `_vm_lock()` for a given Target Environment's `vmid` at a time (FR-010); a conflicting
  request observes `busy: true` on that Target Environment and receives a fast, clear
  rejection rather than blocking or racing.
