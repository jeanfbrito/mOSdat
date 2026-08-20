---
description: "Task list for Test Server Provisioning"
---

# Tasks: Test Server Provisioning

**Input**: Design documents from `/specs/002-test-server-provisioning/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/server-tools.md, quickstart.md

**Tests**: Not explicitly requested as TDD; unit tests are still included per story as the
verifiable Definition of Done for safety-relevant behavior (no-fallback-on-missing-image,
reuse-not-duplicate), consistent with `specs/001-agent-desktop-testing/tasks.md`'s
precedent and this project's verification standards.

**Organization**: Tasks are grouped by user story (spec.md). Most implementation work
lands in the new `automation/commands/rc_server.py` module plus small additions to the
existing `mcp_tools.py`/`mcp_server.py` — `[P]` is used only where tasks genuinely touch
different files with no ordering dependency.

## Path Conventions

Single existing Python project. All paths are relative to the repo root
(`/Users/jean/Github/mOSdat`). No new top-level directories.

---

## Phase 1: Setup

**Purpose**: Test harness + the one new non-code artifact (the Compose template).

- [X] T001 Create test scaffold `tests/test_rc_server.py` with pytest fixtures for mocking
  `docker`/`docker compose` subprocess calls and the `/livez` HTTP poll (no real Docker or
  network required for unit tests)
- [X] T002 [P] Create the checked-in Compose template
  `automation/commands/templates/rc-server-compose.yml` — `rocketchat` + `mongo` services
  only (research.md Decision 2, trimmed from Rocket.Chat's own `docker-compose-ci.yml`),
  `${RC_IMAGE}` substitution for the app image, `mongo` on the internal compose network
  only (no host port), app container bound to an ephemeral host port (`"0:3000"`)

**Checkpoint**: Test harness + Compose template exist; no story work has started yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared Docker/HTTP primitives every user story's tools depend on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Implement `normalize_ref(ref) -> str` (e.g. bare `"3464"` → `"pr-3464"`) and
  `resolve_image(ref) -> str` (→ `ghcr.io/rocketchat/rocket.chat:<ref>`, research.md
  Decision 1) in `automation/commands/rc_server.py`
- [X] T004 Implement `project_name(ref) -> str` — sanitize `ref` into a valid Docker
  Compose project name, `mosdat-rc-<sanitized-ref>` (research.md Decision 3), in
  `automation/commands/rc_server.py` — depends on T003 for the normalized ref
- [X] T005 Implement the Docker subprocess primitives in `automation/commands/rc_server.py`:
  `_docker_daemon_running() -> bool` (`docker info`, exit code only — the daemon was found
  NOT running when this was actually exercised live during `/speckit-analyze`, only the
  CLI binaries were present; every entry point below must check this FIRST and fail with
  a clear "Docker daemon is not running — start Docker Desktop first" rather than an
  opaque subprocess error), `_docker_pull(image) -> (ok, error)`,
  `_compose_up(project, compose_file, env) -> (ok, error)`,
  `_compose_down(project, compose_file) -> (ok, error)`,
  `_discover_host_port(project, service, container_port) -> Optional[int]` (via
  `docker port`/`docker compose port`), `_list_managed_projects() -> list[str]` (via
  `docker ps`/`docker compose ls` filtered by the `mosdat-rc-` naming convention from T004)
- [X] T006 Implement `probe_ready(url, timeout=5) -> bool` — a single `GET <url>/livez`
  check (research.md Decision 2) in `automation/commands/rc_server.py` — depends on T003
  for URL construction conventions
- [X] T007 Add unit tests for T003-T006 (mocked subprocess/HTTP; no real Docker) in
  `tests/test_rc_server.py` — depends on T003-T006

**Checkpoint**: Foundation ready — user story implementation can now begin.

---

## Phase 3: User Story 1 - Agent provisions a server matching a specific published image reference (Priority: P1) 🎯 MVP

**Goal**: An agent can request a server for a PR/release/RC/tag and get back a URL that
actually answers as that reference's Rocket.Chat build, once ready.

**Independent Test**: Given a reference with a published image (e.g. `develop`), the agent
calls `mosdat_server_provision({ref})` and, polling until `state: "ready"`, confirms the
returned `url` answers `/livez`.

### Implementation for User Story 1

- [X] T008 [US1] Implement `provision_server(ref, *, dry_run) -> BootstrapResult`-style
  result (check `_docker_daemon_running()` first and fail clearly if it's down; pull via
  T005, compose up via T005, poll via T006 with a bounded ~180s startup timeout, `state`
  starting/ready/failed per FR-003/FR-004) in `automation/commands/rc_server.py` —
  depends on T003-T006
- [X] T009 [US1] Implement `run_server_provision(args) -> int` CLI entry point and
  `add_server_provision_subparser(sub)` (`mosdat server-provision <ref>`) in
  `automation/commands/rc_server.py`, wired into `automation/commands/parser.py` and
  `automation/main.py` (same pattern as `build`/`ssh-bootstrap`) — depends on T008
- [X] T010 [US1] Implement the `mosdat_server_provision` MCP tool handler
  (`_server_provision(args) -> dict`, uniform envelope) in `automation/mcp_tools.py`,
  registered in `TOOL_DEFINITIONS` and `automation/mcp_server.py`'s dispatch table —
  depends on T008
- [X] T011 [US1] Add unit tests for `provision_server`: happy path (mocked pull+compose+
  poll all succeed → `ready`), still-starting (poll not yet 200 → `starting`),
  startup-timeout (never 200 within the bound → `failed`), daemon-not-running (mocked
  `_docker_daemon_running()` returns False → clear error, no pull/compose attempted), and
  **two concurrent provisions for different references get distinct ports** (SC-007 —
  mock `_discover_host_port` to return different values per call and assert both
  resulting `url`s differ) in `tests/test_rc_server.py` — depends on T008
- [X] T012 [US1] Add a `-m live` integration test provisioning a real reference (e.g.
  `develop`) end-to-end, polling until `ready`, asserting the URL's `/livez` truly answers,
  per `quickstart.md` Step 1, in `tests/test_rc_server.py` — depends on T008-T010

**Checkpoint**: User Story 1 is fully functional and independently testable — an agent can
get a real, matching, ready server from one call.

---

## Phase 4: User Story 2 - Agent points tests at an already-running server instead of provisioning one (Priority: P1)

**Goal**: An agent can run a scenario against any already-running server by URL, with zero
Docker/provisioning activity triggered.

**Independent Test**: Call `mosdat_run_functional` with a `server_url` pointing at any
reachable Rocket.Chat server (including, but not limited to, a User-Story-1-provisioned
one) and confirm the scenario connects to that exact server, with no new container started
as a side effect.

**Note**: Independent of User Story 1's Docker orchestration — this story only touches the
existing `mosdat_run_functional` path. Can be built in parallel with Phase 3, though both
touch `automation/mcp_tools.py` (different functions) and `TOOL_DEFINITIONS` — coordinate
if run concurrently by different builders on a shared tree, or isolate with a worktree.

### Implementation for User Story 2

- [X] T013 [US2] Add an optional `server_url` argument to the `mosdat_run_functional`
  handler, threading it through `automation/mcp_tools.py::_invoke_functional_runner` (the
  actual MCP-side implementation — NOT `functional_cmd.py`'s CLI-only seam, a different
  code path MCP never touches; see research.md Decision 4): into `load_test_yaml`'s
  `cli_vars` argument (for `{{ double_brace }}` scenarios) and into the `vars_` dict passed
  to `runner.run_test` (for `{single_brace}` scenarios, the syntax used by the real
  smoke tests) so the override takes precedence when supplied, and today's default
  behavior is unchanged when it's omitted
- [X] T014 [US2] Add unit tests confirming `server_url` overrides the configured
  `workspace_url` in the vars passed to the runner, and that omitting it preserves
  existing behavior (no regression), in `tests/test_mcp_tools.py` — depends on T013

**Checkpoint**: User Stories 1 and 2 both independently functional.

---

## Phase 5: User Story 3 - Agent gets a clear, actionable failure when no matching server image exists (Priority: P2)

**Goal**: Requesting a reference with no published server image (most commonly a fork PR)
always fails by name, with zero silent substitution.

**Independent Test**: Request a reference known to have no published image; confirm the
error names that exact reference and that no container was started.

### Implementation for User Story 3

- [X] T015 [US3] In `provision_server`'s pull step (T008), distinguish a "no image
  published for this reference" pull failure (e.g. `docker pull`'s "manifest unknown"/
  "not found" error text) from other pull failures (network, auth) with a specific,
  reference-naming error message — and ensure no `compose up` is attempted when the image
  doesn't exist, in `automation/commands/rc_server.py` — depends on T008
- [X] T016 [US3] Add unit tests: a mocked "manifest unknown"-style pull error produces the
  FR-002 no-image error naming the reference with no compose-up call attempted; a
  different pull error (e.g. auth failure) produces a distinct message, in
  `tests/test_rc_server.py` — depends on T015

**Checkpoint**: User Stories 1, 2, and 3 all independently functional.

---

## Phase 6: User Story 4 - Agent reuses an already-running matching server instead of duplicating it (Priority: P2)

**Goal**: A second request for a reference that's already running and ready returns the
existing instance immediately — no new provisioning, no duplicate containers.

**Independent Test**: Provision a reference, then request it again; confirm the same URL
comes back immediately and no second `compose up` occurred.

### Implementation for User Story 4

- [X] T017 [US4] Add a reuse check to `provision_server` (or the `mosdat_server_provision`
  handler): before pulling/composing, call `_list_managed_projects()` (T005) for an
  existing project matching this reference; if found, poll `/livez` once for current state
  and return it instead of provisioning a new instance, in
  `automation/commands/rc_server.py` — depends on T005, T008
- [X] T018 [US4] Add unit tests: a second `provision_server` call for the same reference
  returns the existing instance without a new mocked `compose up` call; a different
  reference still provisions separately (no cross-reference interference), in
  `tests/test_rc_server.py` — depends on T017

**Checkpoint**: User Stories 1-4 all independently functional.

---

## Phase 7: User Story 5 - Agent tears down a server instance when it's no longer needed (Priority: P3)

**Goal**: An agent can stop and remove a provisioned instance, and discover what's
currently running.

**Independent Test**: Provision a reference, tear it down, and confirm a subsequent list/
status check shows it's no longer running with no leftover containers.

### Implementation for User Story 5

- [X] T019 [US5] Implement `teardown_server(ref) -> TeardownResult` (compose down `-v` via
  T005's `_compose_down`, idempotent — no instance found is not an error) in
  `automation/commands/rc_server.py` — depends on T004, T005
- [X] T020 [US5] Implement `run_server_teardown(args)`/`run_server_list(args)` CLI entries
  and `add_server_teardown_subparser`/`add_server_list_subparser` (`mosdat
  server-teardown <ref>`, `mosdat server-list`) in `automation/commands/rc_server.py`,
  wired into `parser.py`/`main.py` — depends on T019
- [X] T021 [US5] Implement the `mosdat_server_teardown` and `mosdat_server_list` MCP tool
  handlers in `automation/mcp_tools.py` (uniform envelope), registered in
  `TOOL_DEFINITIONS` and `automation/mcp_server.py`'s dispatch table — depends on T019
- [X] T022 [US5] Add unit tests: teardown of an existing instance reports `torn_down: true`
  and removes its containers (mocked); teardown of a non-existent reference reports
  `torn_down: false` without error; list reports zero/one/multiple instances correctly, in
  `tests/test_rc_server.py` — depends on T019-T021

**Checkpoint**: All five user stories independently functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Documentation and verification per this project's own conventions.

- [X] T023 [P] Update `AGENTS.md`'s "Agent-facing MCP tools" table (12 → 15 tools) and
  `skills/mosdat-testing/SKILL.md`'s tool reference with `mosdat_server_provision`/
  `_teardown`/`_list` and the `mosdat_run_functional` `server_url` addition
- [X] T024 Run `pytest -q tests/test_rc_server.py tests/test_mcp_tools.py` (non-live
  subset) and confirm all green — depends on T001-T022
- [X] T025 Run the full `quickstart.md` validation against real Docker + `develop` —
  depends on T024. **Steps 1/2/3/6 verified live** (provision → ready with a real LAN
  `url`, `/livez` 200; reuse returns the same URL near-instantly with no duplicate
  containers; `pr-1` fails by name with no fallback; list then teardown correctly reports
  `torn_down: true` and leaves no containers). **Steps 4/5 UNVERIFIED** — deferred by user
  choice, since they require a real VM running a desktop-client scenario (slow, VNC/VLM),
  not a Docker/network availability blocker. Live validation surfaced and fixed three real
  bugs no unit test caught (all mocked `_compose_up`/`_compose_down`/Docker entirely):
  (1) the trimmed Compose template dropped Rocket.Chat's own custom `entrypoint:` override
  that actually runs `rs.initiate()` — mongo never got a primary, rocketchat crashed with
  `MongoServerSelectionError`; (2) `ROOT_URL`/port pre-allocation (see amended research.md
  Decision 3); (3) `_compose_down` never passed `RC_IMAGE`/`ROOT_URL`/`HOST_PORT`, so real
  `docker compose down` failed compose-file validation before removing anything — fixed
  with placeholder env values (teardown doesn't need the original values, only needs the
  file to parse) and covered by a new regression test.
- [X] T026 Run GitNexus `detect_changes()` to confirm only the expected symbols/flows in
  `automation/commands/rc_server.py`, `automation/mcp_tools.py`, and
  `automation/mcp_server.py` changed, per this project's mandatory pre-commit check
  (`AGENTS.md`) — depends on T025. `detect_changes({scope: "compare", base_ref: "main"})`:
  33 changed symbols / 8 files, risk "high" driven by hub-function fan-out
  (`handle_tools_call`/`build_parser`/`main`), not unexpected coupling — every symbol
  traces to this feature's intended wiring.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup (T002's Compose template) — BLOCKS all user
  stories.
- **User Story 1 (Phase 3)**: Depends on Foundational only.
- **User Story 2 (Phase 4)**: Depends on Foundational only (does NOT depend on User Story
  1's `rc_server.py` work — it only touches the existing `mosdat_run_functional` path).
  Fully independent of US1; can run in parallel with it (see coordination note in Phase 4).
- **User Story 3 (Phase 5)**: Depends on User Story 1's T008 (extends the pull-failure
  handling inside `provision_server`) — NOT independent of US1, unlike US2/US4's framing in
  the spec; the no-image behavior lives inside the same function US1 built.
- **User Story 4 (Phase 6)**: Depends on User Story 1's T008 and Foundational's T005 (the
  reuse check wraps `provision_server`). Also not independent of US1's implementation,
  though it is independently *testable* once US1 exists.
- **User Story 5 (Phase 7)**: Depends on Foundational (T004, T005) only — independent of
  US1/US2/US3/US4's own logic, though testing it meaningfully requires an instance from US1
  to tear down.
- **Polish (Phase 8)**: Depends on all five user stories being complete.

### Within Each User Story

Most tasks are sequential edits to `automation/commands/rc_server.py` — dependencies are
noted per-task above rather than assumed.

### Parallel Opportunities

- T002 (Compose template) can be written in parallel with T001 (test scaffold) — different
  files, no shared dependency.
- Once Foundational (Phase 2) is done, User Story 2 (Phase 4) and User Story 5 (Phase 7,
  minus its dependency on a real US1 instance for meaningful testing) can proceed in
  parallel with User Story 1 (Phase 3) — genuinely different code paths. User Story 3
  (Phase 5) and User Story 4 (Phase 6), despite being separate spec user stories, are
  implementation-wise extensions of US1's `provision_server` (T008) and should follow it
  sequentially, not run in parallel with it.
- T023 (docs) in Polish is the only task genuinely parallel with other Polish tasks
  (different files).

---

## Parallel Example: After Foundational Completes

```bash
# These can proceed concurrently once T003-T007 are done:
Task: "US1 — implement provision_server() (T008)"
Task: "US2 — add server_url param to mosdat_run_functional (T013)"
Task: "US5 — implement teardown_server()/CLI/MCP wiring (T019-T021, testable once US1 exists)"

# US3 (T015) and US4 (T017) must wait for US1's T008 to land — they extend it, not run
# alongside it.
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (Setup) + Phase 2 (Foundational).
2. Complete Phase 3 (User Story 1): T008-T012.
3. **STOP and VALIDATE**: run `quickstart.md` Step 1 (provision `develop`, confirm
   `/livez` answers) against real Docker.
4. This alone delivers the core value: an agent can get a real, version-matched server
   without any manual Docker step — the exact gap that caused a false test failure in
   `specs/001-.../` validation.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. US1 → validate independently (quickstart.md Step 1) → this is the MVP.
3. US2 → validate independently (quickstart.md Step 5) — can land before or after US1 in
   practice, since it's code-independent, but is only *meaningfully* demonstrable end-to-end
   once a scenario run is available to point somewhere.
4. US3 → validate independently (quickstart.md Step 3).
5. US4 → validate independently (quickstart.md Step 2).
6. US5 → validate independently (quickstart.md Step 6).
7. Polish (T023-T026) → docs, full test run, live quickstart, `detect_changes()`.

Total: 26 tasks — Setup (2), Foundational (5), US1 (5), US2 (2), US3 (2), US4 (2), US5 (4), Polish (4).
