---
description: "Task list for Agent-Ready Desktop Testing"
---

# Tasks: Agent-Ready Desktop Testing

**Input**: Design documents from `/specs/001-agent-desktop-testing/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/mcp-tools.md, quickstart.md

**Tests**: Not explicitly requested in the spec as TDD; unit tests are still included per
story as the verifiable Definition of Done for safety-relevant behavior (lock rejection,
error-path correctness), consistent with this project's verification standards
(`AGENTS.md`, `docs/test-strategy.md`). They are written alongside implementation, not
strictly test-first.

**Organization**: Tasks are grouped by user story (spec.md). Nearly all implementation work
lands in `automation/mcp_tools.py` (a single file), so `[P]` is used sparingly — only where
tasks genuinely touch different files with no ordering dependency.

## Path Conventions

Single existing Python project. All paths are relative to the repo root
(`/Users/jean/Github/mOSdat`). No new top-level directories.

---

## Phase 1: Setup

**Purpose**: Test harness for the work that follows.

- [X] T001 Create test scaffold `tests/test_mcp_tools.py` with pytest fixtures for calling
  `automation/mcp_tools.py` handler functions directly (mock VM registry / mock
  `run_build`/`FunctionalRunner`, no live VM or stdio transport required)

**Checkpoint**: Test harness exists; no story work has started yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared helpers every user story's tools depend on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Implement the uniform response-envelope helper
  `_envelope(ok, error=None, degraded=None, **fields)` in `automation/mcp_tools.py`
  (research.md Decision 2)
- [X] T003 Implement `_resolve_vm(name)` and `_resolve_scenario(name, platform=None)`
  lookup helpers in `automation/mcp_tools.py` that return either the resolved
  `VMConfig`/scenario metadata or an error naming the invalid value plus valid
  alternatives (FR-005) — depends on T002 for the error shape
- [X] T004 Implement `_vm_busy(vmid)`, a non-blocking probe of
  `automation.proxmox.vm._vm_lock(vmid)` that reports lock contention without blocking, in
  `automation/mcp_tools.py` — depends on T002
- [X] T005 Add unit tests for `_envelope`, `_resolve_vm`, `_resolve_scenario`, and
  `_vm_busy` (found/not-found/alternatives-listed/busy cases) in `tests/test_mcp_tools.py`
  — depends on T002-T004

**Checkpoint**: Foundation ready — user story implementation can now begin.

---

## Phase 3: User Story 1 - Agent validates a candidate build end-to-end (Priority: P1) 🎯 MVP

**Goal**: An agent can trigger build + deploy + a scenario run against a target
environment with no build pre-deployed, and get a correct overall verdict (by combining
the build/deploy and run call results) — no human performs build or deploy first.

**Independent Test**: Give the agent a target environment and a build/PR reference with no
build pre-deployed; the agent calls `mosdat_build` (with `deploy_to` set) then
`mosdat_run_functional`, and reports a correct verdict using only tool output.

### Implementation for User Story 1

- [X] T006 [US1] Wire the full `run_build()` clone→build→deploy→verify-symbol flow
  (`automation/commands/build.py`) into the `mosdat_build` tool handler in
  `automation/mcp_tools.py`, using `_envelope`/`_resolve_vm` from Phase 2 (FR-008)
- [X] T007 [US1] Wrap the `mosdat_build` and `mosdat_deploy` handlers with a non-blocking
  `_vm_lock(vmid)` acquire in `automation/mcp_tools.py`; on contention return
  `{"ok": false, "error": "vm busy: <vmid> held by another operation"}` immediately rather
  than blocking (FR-010) — depends on T006
- [X] T008 [US1] Ensure `mosdat_build`'s response distinguishes a build-stage failure
  (`deploy: null`, no deploy attempted) from a deploy-stage failure (`build_ok: true`,
  `deploy.error` set) in `automation/mcp_tools.py` — depends on T006 (spec Acceptance
  Scenarios 2 & 3)
- [X] T009 [US1] First confirm the actual return type of `cmd_functional`'s per-VM
  execution path in `automation/commands/functional_cmd.py` /
  `automation/runners/functional.py` — do not assume `BugConfirmationResult` applies to
  ordinary (non-`confirm`) scenarios; correct `data-model.md` if it differs. Then extend
  the `mosdat_run_functional` handler to return a normalized three-state `verdict`
  (`pass`/`fail`/`error`), a per-step `steps` array, and an `artifacts` path list, in
  `automation/mcp_tools.py` (FR-001, FR-002, FR-006)
- [X] T010 [US1] Surface `degraded: ["vlm_unavailable"]` in `mosdat_run_functional`'s
  response when the VLM warmup/probe indicated the visual verification backend was
  unavailable or degraded during the run, in `automation/mcp_tools.py` (FR-007) — depends
  on T009
- [X] T011 [US1] Add unit tests (mocking `run_build`, `_vm_lock`, and `FunctionalRunner`)
  covering build-vs-deploy failure distinction and lock-rejection, in
  `tests/test_mcp_tools.py` — depends on T006-T010
- [X] T012 [US1] Add a `-m live` integration test exercising the full
  build→deploy→run sequence against a real VM, asserting the deployed artifact's version
  matches what was just built (no stale-binary false negative), in
  `tests/test_mcp_tools.py`, per `quickstart.md` Step 4 — depends on T006-T010

**Checkpoint**: User Story 1 is fully functional and independently testable — an agent can
close the build→deploy→run→verdict loop with no human step.

---

## Phase 4: User Story 2 - Agent runs a scenario against an already-deployed build (Priority: P1)

**Goal**: An agent can run a scenario against a build that's already deployed and get a
correct, self-contained verdict, without repeating an unnecessary build/deploy.

**Independent Test**: Point the agent at an existing scenario and a VM that already has a
build deployed; the agent calls `mosdat_run_functional` directly and reports pass/fail/why
from the tool output alone.

**Note**: This story reuses the `mosdat_run_functional` extensions built in User Story 1
(T009/T010) — it does not require US1's build/deploy wiring (T006-T008), only the
run/verdict mechanics. If implementing US1 and US2 out of order, complete T009 first.

### Implementation for User Story 2

- [X] T013 [US2] Add pre-run validation to `mosdat_run_functional` using
  `_resolve_vm`/`_resolve_scenario` (Phase 2), rejecting an unknown VM/scenario combination
  with an error naming valid alternatives before attempting any run, in
  `automation/mcp_tools.py` (FR-005)
- [X] T014 [US2] Add a readiness pre-check inside `mosdat_run_functional` (VM reachable,
  required dependencies present) so an unready environment is reported as a distinct
  "environment not ready" condition rather than a test failure, in
  `automation/mcp_tools.py` (spec Acceptance Scenario, User Story 2.3) — depends on T009
- [X] T015 [US2] Add unit tests for invalid-combination rejection and
  environment-not-ready-vs-test-failure distinction in `tests/test_mcp_tools.py` — depends
  on T013-T014

**Checkpoint**: User Stories 1 and 2 both independently functional.

---

## Phase 5: User Story 3 - Agent verifies environment readiness before spending time on a run (Priority: P2)

**Goal**: An agent can check whether a target VM is ready (reachable, dependencies present,
expected build deployed) before committing to a build/deploy or run.

**Independent Test**: Agent calls the new readiness tool against a target VM and expected
build/PR and gets a go/no-go answer with a named reason before attempting anything else.

### Implementation for User Story 3

- [X] T016 [US3] Implement the new `mosdat_readiness` tool handler wrapping the existing
  `preflight.py`/`doctor.py` checks (connectivity, tool dependencies, disk space) in
  `automation/mcp_tools.py` (FR-003)
- [X] T017 [US3] Add `expect_pr`/`expect_symbol` deployed-build-matches-expected checking to
  `mosdat_readiness`, reusing `_verify_symbols_on_vm()`/installed-version detection from
  `automation/commands/build.py`, in `automation/mcp_tools.py` — depends on T016
- [X] T018 [US3] Add a `busy` field to `mosdat_readiness`'s VM info using the `_vm_busy()`
  helper from T004, in `automation/mcp_tools.py` — depends on T016
- [X] T019 [US3] Register `mosdat_readiness` in the tool dispatch table in
  `automation/mcp_server.py` — depends on T016
- [X] T020 [US3] Add unit tests for `mosdat_readiness` covering ready / not-ready /
  missing-dependency / busy cases in `tests/test_mcp_tools.py` — depends on T016-T019

**Checkpoint**: User Stories 1, 2, and 3 all independently functional.

---

## Phase 6: User Story 4 - Agent discovers what it can run without prior human briefing (Priority: P3)

**Goal**: An agent with no prior context can enumerate runnable scenarios and configured
VMs, and gets corrective errors on an invalid pick.

**Independent Test**: Agent calls `mosdat_list_vms`/`mosdat_list_scenarios` with no prior
context, picks a combination, and (for an invalid pick) receives a corrective error listing
valid alternatives.

### Implementation for User Story 4

- [X] T021 [US4] Align the `mosdat_list_vms` response to the uniform envelope
  (`ok`/`error`/`degraded`) using `_envelope()` from Phase 2, in `automation/mcp_tools.py`
- [X] T022 [US4] Align the `mosdat_list_scenarios` response to the uniform envelope and
  confirm/add a `platform` field per scenario entry, in `automation/mcp_tools.py`
- [X] T023 [US4] Add unit tests for the discovery tools' envelope shape in
  `tests/test_mcp_tools.py` — depends on T021-T022

**Checkpoint**: All four user stories independently functional. Note: run-time rejection of
an invalid scenario/VM combination (FR-005) is implemented in User Story 2's T013, not
here — User Story 4 only covers discovering the valid set upfront (spec.md User Story 4,
Acceptance Scenario 2).

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Wrap-up and verification per this project's own AGENTS.md conventions.

- [X] T024 [P] Add a short "Agent-facing MCP tools" section to `AGENTS.md` pointing agents
  at `mosdat-mcp`, the full tool list, and `specs/001-agent-desktop-testing/quickstart.md`
- [X] T025 Run `pytest -q tests/test_mcp_tools.py` (non-live subset) and confirm all green
  — 40 passed, 1 deselected (live)
- [X] T026 Run the full `quickstart.md` validation against a real VM (`windows10`,
  192.168.13.87) — depends on T006-T023 all complete. **PARTIAL, with real live evidence**:
  - `mosdat_readiness` (Step 3): proven live — correctly reported `ready: false` /
    `ssh_reachable: FAIL` while the VM was off, then `ready: true` once it was up and SSH
    was authorized.
  - `mosdat_build` (Step 4, PR #3464, target `exe`): initially proven only as a clean
    failure (`build_ok: false`, `deploy: null`, no deploy attempted — spec Acceptance
    Scenario 2), since this Mac has no Wine, which `electron-builder` needs for a
    Windows-target cross-build. **Since then, fixed properly**: `run_build()` now builds
    natively on the target Windows VM over SSH instead of cross-compiling
    (`build_on_windows_vm()` in `automation/commands/build.py`) — no Wine needed anywhere.
    Re-ran the real `mosdat_build` tool (not manual steps) against PR #3464 targeting
    `windows10`: `ok: true`, `build_ok: true`, `deploy.install_ok: true`,
    `deploy.installed_version: "4.17.0-alpha.1"`. Along the way this also caught and fixed a
    real integration bug: `mcp_tools.py`'s `_call_run_build` only monkey-patched
    `deploy_to_vm`/`deploy_to_windows_vm` to capture their `DeployResult`, not the new
    `build_on_windows_vm` — so the first live run of the fixed code silently returned an
    empty `installed_version` despite a successful install. Fixed and covered by a
    regression test (`test_build_captures_build_on_windows_vm_result`).
  - `mosdat_deploy`: not exercised via the tool this session (the Windows artifact was
    installed via `mosdat_build`'s remote-build path directly, which installs in place —
    no separate SCP+deploy step applies to the Windows-native-build case).
  - `mosdat_run_functional` (Step 4 cont'd): proven live and in full — ran
    `tel-qa-001-settings-discovery` against the freshly-built, freshly-installed PR #3464
    exe; returned `ok: true`, `verdict: "fail"` (a real scenario step timeout at "wait for
    kebab button", step index 7 of 20 — unrelated to PR #3464's actual change, which is a
    notification quick-reply fix; this scenario was picked only to exercise the tool, not
    to validate the PR's feature), with correct per-step `steps` and `artifacts`
    (screenshot + `events.jsonl`). This is the core FR-001/FR-002 contract, proven for real.
  - Step 5 (concurrency) and Step 6 (degraded-VLM signal) of quickstart.md: not exercised
    this session.
- [X] T027 Run GitNexus `detect_changes()` to confirm only the expected symbols/flows in
  `automation/mcp_tools.py` and `automation/mcp_server.py` changed, per this project's
  mandatory pre-commit check (`AGENTS.md`) — risk_level: medium, 2 affected processes
  (both pre-existing `handle_tools_call` traces), no HIGH/CRITICAL findings

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational only.
- **User Story 2 (Phase 4)**: Depends on Foundational; additionally depends on US1's T009
  (`mosdat_run_functional` verdict/step extension) — does NOT depend on US1's build/deploy
  tasks (T006-T008, T012).
- **User Story 3 (Phase 5)**: Depends on Foundational only (T004 for the `busy` field).
  Fully independent of US1/US2.
- **User Story 4 (Phase 6)**: Depends on Foundational only (T002 for envelope, T003 for
  alternatives-listing behavior it exercises). Fully independent of US1/US2/US3.
- **Polish (Phase 7)**: Depends on all four user stories being complete.

### Within Each User Story

Most tasks in this feature are sequential edits to the same file
(`automation/mcp_tools.py`) — dependencies are noted per-task above rather than assumed.

### Parallel Opportunities

- T005 (Foundational tests) can be written alongside starting US3/US4 work once T002-T004
  land, since neither US3 nor US4 depends on T005 itself.
- Once Foundational (Phase 2) is done, US3 (Phase 5) and US4 (Phase 6) can proceed fully in
  parallel with US1 (Phase 3) — they touch different tool handlers with no shared
  dependency beyond Phase 2. US2 (Phase 4) must wait for US1's T009.
- T024 (docs) in Polish is the only task genuinely parallel with other Polish tasks (different file).

---

## Parallel Example: After Foundational Completes

```bash
# These can proceed concurrently once T002-T004 are done:
Task: "US1 — wire full run_build() flow into mosdat_build (T006)"
Task: "US3 — implement mosdat_readiness tool handler (T016)"
Task: "US4 — align mosdat_list_vms response to envelope (T021)"

# US2 (T013+) must wait until US1's T009 lands.
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (Setup) + Phase 2 (Foundational).
2. Complete Phase 3 (User Story 1): T006-T012.
3. **STOP and VALIDATE**: run `quickstart.md` Step 4 (build→deploy→run) against a real VM.
4. This alone delivers the core value: an agent can close the build-to-verdict loop with no
   human step.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. US1 → validate independently (quickstart.md Step 4) → this is the MVP.
3. US2 → validate independently (quickstart.md Step 4's second half, run-only path).
4. US3 → validate independently (quickstart.md Step 3).
5. US4 → validate independently (quickstart.md Step 2).
6. Polish (T024-T027) → full `quickstart.md` run, `detect_changes()` check, close out.

Total: 27 tasks — Setup (1), Foundational (4), US1 (7), US2 (3), US3 (5), US4 (3), Polish (4).
