# Feature Specification: Test Server Provisioning

**Feature Branch**: `002-test-server-provisioning`

**Created**: 2026-08-20

**Status**: Draft

**Input**: User description: "we need to be able to run the chat server that we need to
test in a way the tests can use it, could be in docker by the PR image or release, or
something else, discover"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent provisions a server matching a specific published image reference (Priority: P1)

As a Claude agent validating a Rocket.Chat.Electron change, I want a Rocket.Chat **server**
instance matching whatever image reference I specify — a PR, a release, a release
candidate, `develop`, or any other tag Rocket.Chat's CI publishes — to be available for the
desktop client to connect to, instead of relying on a fixed, drifting external server, so a
stale server version never produces a false "unsupported version" failure unrelated to the
actual code under test.

**Why this priority**: This exact failure mode was hit live this session — a real PR build
was correctly built, deployed, and launched, but the functional scenario failed because the
shared external test server (`rocketchat.jeanbrito.com`) was running an older server version
than the client build expected. Without this, every future agent-run validation risks the
same false negative.

**Independent Test**: Given an image reference (a PR number, a release/RC tag, or any other
published tag), the agent requests a matching server instance and receives back a URL a
desktop-client scenario can be pointed at; the server actually answers requests as the
expected Rocket.Chat version.

**Acceptance Scenarios**:

1. **Given** a reference with a published server image (a PR, release, RC, or other tag),
   **When** the agent requests a server for that reference, **Then** it receives a reachable
   URL, and the server identifies itself as running that reference's build once ready.
2. **Given** a request for a server that is still starting up, **When** the agent checks
   status before the server is actually ready, **Then** the result clearly distinguishes
   "still starting" from "ready" and from "failed to start" — the agent must never be told
   a not-yet-ready server is ready.
3. **Given** a server that never becomes ready within a reasonable startup window, **When**
   the agent checks status, **Then** it receives a clear failure with diagnostic detail
   (e.g., the last known state), not an indefinite wait.

---

### User Story 2 - Agent points tests at an already-running server instead of provisioning one (Priority: P1)

As a Claude agent, when a suitable server is already running — a local development server
the user is actively working against, or any other server the user names — I want to point
test scenarios at that server directly, without mosdat provisioning a redundant Docker
instance, so testing against a live dev setup doesn't require tearing it down and
duplicating it in a container.

**Why this priority**: Provisioning-by-image-reference (User Story 1) is not the only way a
suitable server comes to exist. Deciding whether a server is even needed, and which one to
use, is the calling agent's/user's call, informed by context mosdat itself doesn't have —
mosdat's job is to make whichever target reliably usable by a test scenario, not to guess.

**Independent Test**: Given a URL for an already-running server, the agent points a
scenario at it directly and the scenario connects successfully — no provisioning step, no
Docker involvement.

**Acceptance Scenarios**:

1. **Given** the user or agent names an already-running server by URL, **When** a scenario
   is run against it, **Then** the scenario connects to that exact server — mosdat does not
   provision a separate instance in parallel.
2. **Given** the named existing server is unreachable, **When** the agent checks it,
   **Then** the failure clearly identifies the server as unreachable, distinct from an
   image-provisioning failure (User Story 1).

---

### User Story 3 - Agent gets a clear, actionable failure when no matching server image exists (Priority: P2)

As a Claude agent, when the reference I'm asked to validate has no published server image —
most commonly a fork PR, which Rocket.Chat's CI never builds a server image for — I want a
clear, specific failure naming exactly what's missing, so I know that reference genuinely
cannot be tested this way rather than silently getting a different version substituted.

**Why this priority**: Rocket.Chat's own CI gates server-image publishing to same-repo PRs,
releases, and `develop` — fork PRs are a real, common case (verified directly against
`.github/workflows/ci.yml`'s `publish-image` condition), not an edge case, and this feature
must say so plainly rather than assume every reference resolves.

**Independent Test**: Given a reference with no published server image, the agent receives
a response naming that specific reference as having no image — no automatic substitution to
a different version, ever.

**Acceptance Scenarios**:

1. **Given** a PR from a fork (no published image), **When** the agent requests a server
   for that PR, **Then** the response clearly states no image is available for that PR. It
   does not silently substitute the nearest release or any other version.
2. **Given** a request for a release, RC, or other tag instead of a PR, **When** the agent
   requests it, **Then** the same provisioning path works using that reference's published
   image, with the same clear-failure behavior if no image exists for it either.

---

### User Story 4 - Agent reuses an already-running matching server instead of duplicating it (Priority: P2)

As a Claude agent, when a mosdat-provisioned server instance matching the reference I need
is already running (started by an earlier step in the same session, or by another agent), I
want to reuse it rather than starting a redundant duplicate, so I don't waste startup time or
exhaust host resources (ports, memory, disk) running multiple copies of the same thing.

**Why this priority**: Multiple test runs against the same reference within a session (e.g.,
a smoke check followed by a full functional scenario) are common; provisioning a fresh server
for each would multiply the ~60-second-plus startup cost every time for no benefit.

**Independent Test**: Given a server for a specific reference is already running and ready,
a second request for the same reference returns the existing instance's URL immediately,
without starting a new one.

**Acceptance Scenarios**:

1. **Given** a running, ready server for reference X, **When** the agent requests a server
   for reference X again, **Then** it gets the same URL back immediately, with no new
   provisioning delay.
2. **Given** a running server for reference X, **When** the agent requests a server for a
   *different* reference Y, **Then** a separate instance is provisioned without disturbing
   the reference-X instance already in use.

---

### User Story 5 - Agent tears down a server instance when it's no longer needed (Priority: P3)

As a Claude agent, once I'm done validating a reference, I want to be able to stop the
mosdat-provisioned server instance for it, so host resources (ports, memory, disk, running
containers) don't accumulate unbounded across many test sessions.

**Why this priority**: Lower priority than getting a working server in the first place, but
without an explicit teardown path, provisioned server instances become a silent resource
leak that eventually degrades or breaks the host.

**Independent Test**: Given a running server instance, the agent stops it, and it no longer
appears as an available/running instance afterward.

**Acceptance Scenarios**:

1. **Given** a running server instance, **When** the agent tears it down, **Then** its
   containers stop and are removed, and a subsequent status check reports it as not running.
   (Not applicable to an externally-named server from User Story 2 — mosdat never owns or
   tears down a server it didn't provision.)

---

### Edge Cases

- What happens when two agents concurrently request a server for the *same* reference at the
  same time (both racing to provision before either sees the other's instance)?
- What happens when the host runs out of resources (ports, memory, disk) to start another
  server instance?
- How does the system behave if MongoDB's replica-set initialization (a known Rocket.Chat
  server requirement) fails or hangs — is that surfaced distinctly from "the app container
  itself failed to start"?
- What happens when a previously-provisioned server instance is left running from a prior
  session (e.g., after a host reboot or an ungraceful session end) — can an agent discover
  and either reuse or clean up orphaned instances?
- What happens if an agent names an "already-running" server (User Story 2) that turns out to
  be a mosdat-provisioned instance for a different reference — should that be rejected, or
  is that an equally valid way to point at it?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The tool MUST let an agent provision a running Rocket.Chat server instance for
  a specified image reference — a PR number, a release/RC tag, `develop`, or any other tag
  Rocket.Chat's CI publishes — without the agent needing to run raw Docker/Compose commands
  itself.
- **FR-002**: The tool MUST resolve the specified reference to the correct published server
  image and MUST fail clearly, naming the reference, when no published image exists for it
  (e.g., a fork PR) — never silently substituting a different version. There is no
  fallback: if the requested reference has no image, that reference cannot be tested this
  way.
- **FR-003**: The tool MUST distinguish three server states at all times an agent can check
  them: still starting, ready, and failed — and MUST NOT report "ready" before the server
  genuinely answers as itself (not just "the container process started").
- **FR-004**: The tool MUST enforce a bounded startup timeout and report a clear failure
  with diagnostic detail if the server does not become ready within it, rather than waiting
  indefinitely.
- **FR-005**: The tool MUST return a URL/endpoint that existing mOSdat scenario/config
  mechanisms (e.g., the `servers.json` pre-staging already used by
  `shared/routines/launch-rocketchat.yaml`) can point a desktop-client scenario at — whether
  that URL comes from a mosdat-provisioned instance (FR-001) or an already-running server the
  agent named directly (FR-009).
- **FR-006**: The tool MUST let an agent stop and remove a mosdat-provisioned server instance
  it no longer needs. This does not apply to, and MUST NOT affect, an already-running server
  the agent only pointed at (FR-009) — mosdat never tears down something it didn't start.
- **FR-007**: The tool MUST let an agent discover already-running mosdat-provisioned server
  instances (by the image reference they match) and reuse a matching one instead of starting
  a duplicate.
- **FR-008**: The tool MUST avoid resource collisions (e.g., port conflicts) between
  concurrently-running server instances for different references, assigning non-conflicting
  endpoints automatically.
- **FR-009**: The tool MUST let an agent point a scenario at an already-running server by
  URL (e.g., a local development server) without provisioning anything — a distinct path from
  FR-001, with its own reachability check rather than the image-resolution logic in FR-002.
- **FR-010**: The tool MUST run mosdat-provisioned server instances (FR-001) on the mosdat
  host itself, network-reachable from the desktop-client VMs on the Proxmox LAN; deciding
  whether a server is needed at all, and which reference or existing server to use, is the
  calling agent's responsibility — this tool provisions/resolves whatever specific target is
  requested rather than inferring it.

### Key Entities

- **Server Instance**: A mosdat-provisioned, running Rocket.Chat server (app container + its
  MongoDB dependency, with the replica-set requirement satisfied) for a specific image
  reference; has an identity (the reference it matches), a state
  (starting/ready/failed), and a reachable URL once ready. Runs on the mosdat host.
- **External Server Reference**: An already-running server the agent points a scenario at
  directly, identified only by URL — not provisioned, not owned, and not torn down by mosdat.
- **Server Image Reference**: The resolved published Docker image for a given PR/release/RC/
  tag (e.g., a `pr-<N>`-tagged GHCR image for a PR); may be unresolvable (no image published)
  for a given reference — in which case there is no fallback (FR-002).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An agent requesting a server for a reference that has a published image can
  get a ready, reachable server instance without any manual Docker/Compose step, in a single
  request.
- **SC-002**: An agent never mistakes a still-starting server for a ready one or a failed
  one — verified against the actual server state for 100% of provisioning attempts in a
  validation sample.
- **SC-003**: A second request for a server matching a reference already running returns the
  existing instance immediately (no new startup wait), in 100% of validation-sample cases.
- **SC-004**: A request for a reference with no published server image never results in the
  agent silently testing against the wrong server version — the no-image case is always
  reported by name, with no substitution, in 100% of validation-sample cases.
- **SC-005**: Tearing down a server instance frees its resources such that a subsequent
  status check confirms it is no longer running, with no leftover process/container.
- **SC-006**: An agent can point a scenario at an already-running server by URL and have it
  connect successfully, with zero Docker/provisioning activity triggered by that request.
- **SC-007**: Provisioning two different references concurrently or back-to-back always
  results in two distinct, independently reachable URLs — never a port collision between
  them, in 100% of validation-sample cases.

## Assumptions

- Rocket.Chat's server CI (`.github/workflows/ci.yml`) publishes a `pr-<N>`-tagged image to
  GHCR for same-repo PRs only — verified directly against the workflow's
  `publish-image: ... github.event.pull_request.head.repo.full_name == github.repository ||
  release || develop` gate and `DOCKER_TAG="pr-${{ github.event.number }}"` — with
  equivalent tags for `develop`/release/RC builds. The exact tag format (e.g., whether a
  per-architecture suffix like `-amd64` is needed, or a multi-arch manifest is published
  under the bare `pr-<N>` tag) needs pinning down precisely during planning, not assumed
  here; this is the mechanism this feature resolves against, subject to change if upstream
  CI is restructured.
- Docker and Docker Compose are available on the mosdat host and are the mechanism used to
  run server instances (already confirmed present).
- MongoDB's replica-set initialization (`rs.initiate()` on first start) is a known,
  necessary step for a working Rocket.Chat server and is handled as part of "becoming
  ready," not left to the caller.
- Server instances are ephemeral by default (no expectation of preserved data across a
  teardown) unless a future feature explicitly asks for persistence — this feature does not
  need to solve durable, long-lived server state.
- Mosdat-provisioned instances run on the mosdat host itself rather than a dedicated
  Proxmox VM — simplest path given Docker is already available there. **Reachability from
  the Proxmox-hosted desktop-client VMs, in the actual direction this feature needs
  (VM → Mac), is confirmed live**: a real VM (`windows11`) successfully reached a
  Docker-exposed port on the mosdat host over the LAN. Two non-obvious things this check
  surfaced: (1) reachability specifically depends on going *through Docker's* own
  port-forwarding — a plain process bound to the same LAN IP was blocked by macOS's
  Application Firewall, so this is not "the firewall is generally open," it's "Docker's
  networking layer is already allow-listed"; (2) the Docker daemon was not actually
  running at the time it was first described as "available" — only the CLI binaries were
  present. Both are now verified working, but the daemon needs to be running (`docker
  info` succeeds) before this feature's tools can do anything, and this is not currently
  a task check anywhere.
- This feature provisions or references the SERVER only; the existing desktop-client
  build/deploy/run loop (`specs/001-agent-desktop-testing/`) is unchanged and simply gets
  pointed at whatever URL this feature returns.
- Deciding *whether* a test needs a dedicated server at all, and *which* reference or
  already-running server to use, is the calling agent's/user's judgment call based on context
  this tool doesn't have (e.g., whether a PR touches server-side code, or whether a local dev
  server is already up) — this feature supplies the mechanism, not the decision.
