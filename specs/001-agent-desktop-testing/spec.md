# Feature Specification: Agent-Ready Desktop Testing

**Feature Branch**: `001-agent-desktop-testing`

**Created**: 2026-08-19

**Status**: Draft

**Input**: User description: "make mosdat ready as a tool for desktop testing and validations from claude agents"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent validates a candidate build end-to-end (Priority: P1)

As a Claude agent asked to check whether a code change (e.g., a PR) actually works, I want
to trigger the build, deploy it to a target environment, run a test scenario against it,
and get a clear verdict — all myself, without a human performing the build/deploy step
first — so I can close the loop from "here's a change" to "here's whether it works" in one
autonomous pass.

**Why this priority**: This is the primary reason mOSdat exists (validating desktop app
changes against real environments) and the core value of making it agent-operable — an
agent that can only run scenarios against builds a human already deployed still depends on
a human for the majority of the workflow.

**Independent Test**: Give the agent a target environment and a build/PR reference with no
build pre-deployed; the agent triggers build + deploy + a scenario run and reports a
verdict, without any human performing build or deploy steps.

**Acceptance Scenarios**:

1. **Given** a target environment and a build/PR reference, **When** the agent triggers the
   build-deploy-run sequence (as separate build+deploy and run calls), **Then** each call
   returns its own structured result (build/deploy outcome, then run verdict), and the
   agent can combine them into a single overall pass/fail/error judgment without needing to
   read logs or screenshots itself.
2. **Given** the build stage fails, **When** the agent inspects the result, **Then** the
   result identifies the build failure distinctly from a deploy or test failure, and no
   scenario run is attempted against a non-existent artifact.
3. **Given** the deploy stage fails (e.g., VM unreachable), **When** the agent inspects the
   result, **Then** the result identifies the deploy failure distinctly from a build or test
   failure.
4. **Given** a successful build and deploy, **When** the agent runs the scenario, **Then**
   the result additionally confirms the deployed artifact matches what was just built (no
   silent stale-binary false negative).

---

### User Story 2 - Agent runs a scenario against an already-deployed build (Priority: P1)

As a Claude agent, I want to run an existing test scenario against a build that is already
deployed and receive a clear, machine-readable pass/fail verdict with supporting evidence,
so I can re-validate or run additional scenarios without repeating a build/deploy I don't
need.

**Why this priority**: A trustworthy, self-contained verdict is the minimum viable
capability everything else depends on, and this path (skip build/deploy when unnecessary)
is used every time an agent wants a second scenario against the same build.

**Independent Test**: Point the agent at an existing scenario and a reachable VM that
already has a build deployed; the agent invokes the run and, from the output alone (no
human log-reading), correctly states pass/fail and why.

**Acceptance Scenarios**:

1. **Given** a valid scenario and reachable VM with a build already deployed, **When** the
   agent runs the scenario, **Then** the agent receives a structured result indicating
   overall status (pass/fail/error) plus per-step outcomes.
2. **Given** a scenario step fails, **When** the agent inspects the result, **Then** the
   result includes enough diagnostic detail (failing step, reason, artifact reference) for
   the agent to explain the failure without opening raw log files.
3. **Given** the VM or environment is not ready (e.g., unreachable, missing dependency),
   **When** the agent runs the scenario, **Then** the result clearly distinguishes
   "environment problem" from "application/test failure."

---

### User Story 3 - Agent verifies environment readiness before spending time on a run (Priority: P2)

As a Claude agent, before running an expensive test (or triggering a build/deploy), I want
to check that the target VM/environment is ready (reachable, dependencies present, current
deployed build/version), so I don't waste time or infrastructure resources chasing false
failures caused by environment drift or an unexpected existing deployment.

**Why this priority**: AGENTS.md already flags stale binaries as a known false-negative
source; an agent without an easy readiness check will misdiagnose environment problems as
application bugs, and now that agents can also trigger builds/deploys autonomously, an
unchecked run risks colliding with existing VM state.

**Independent Test**: Agent runs a readiness check against a target VM and expected
build/PR and gets a go/no-go answer before attempting a build/deploy or scenario run.

**Acceptance Scenarios**:

1. **Given** a target VM and an expected build/PR, **When** the agent runs the readiness
   check, **Then** it reports whether the deployed build matches what's expected.
2. **Given** a missing dependency on the VM, **When** the agent runs the readiness check,
   **Then** it reports the specific missing dependency rather than failing opaquely later
   in the run.

---

### User Story 4 - Agent discovers what it can run without prior human briefing (Priority: P3)

As a Claude agent new to a session, I want to discover available test scenarios, target
VMs, and commands directly from the tool, so I can operate mOSdat correctly without a human
first explaining the project's conventions.

**Why this priority**: Reduces onboarding friction and repeated mistakes (e.g., wrong
command shape) each session; valuable, but lower priority than actually running and
validating tests.

**Independent Test**: Agent queries the tool for available scenarios/VMs/commands and
successfully picks a valid combination to run, with no prior conversation context about the
project.

**Acceptance Scenarios**:

1. **Given** no prior context, **When** the agent asks the tool "what can I run," **Then**
   it receives a list of available scenarios and target environments.
2. **Given** the agent picks a combination that isn't in that discovered list, **When** it
   cross-checks against the discovery result, **Then** it can tell locally that the
   combination is invalid before attempting to run it. (Run-time rejection of an invalid
   combination — for an agent that skips discovery and guesses — is covered by User Story 2
   / FR-005, not by this story alone.)

---

### Edge Cases

- What happens when an agent runs a scenario against a VM that is mid-deploy or already in
  use by another concurrent run?
- How does the system respond when an agent supplies a scenario name that doesn't exist, or
  a VM alias that isn't configured?
- What happens when a run is interrupted (agent's process killed, network drop) partway
  through — is partial state left behind, and can the agent tell?
- How does the tool behave when the visual verification backend (credentials, model) is
  unavailable — does it fail clearly rather than silently skipping visual verification?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The tool MUST let an agent run a named test scenario against a named target
  environment and receive back a structured, unambiguous overall verdict (pass / fail /
  error).
- **FR-002**: The tool MUST provide per-step results within a run (not just an aggregate
  verdict), including which step failed and why, sufficient for an agent to explain a
  failure without reading raw logs or screenshots itself.
- **FR-003**: The tool MUST let an agent check environment/target readiness (connectivity,
  required dependencies, deployed build/version) before committing to a full run, and MUST
  distinguish "environment not ready" from "test failed."
- **FR-004**: The tool MUST let an agent discover the set of runnable scenarios and
  configured target environments without requiring prior out-of-band knowledge.
- **FR-005**: The tool MUST reject invalid scenario/environment combinations with an error
  that names the problem and lists valid alternatives, rather than failing deep into
  execution.
- **FR-006**: The tool MUST record artifacts (e.g., screenshots, logs) from a run so an
  agent can reference them by path/identifier from the structured result, for optional
  deeper inspection or inclusion in a report to the user.
- **FR-007**: The tool MUST make clear when a result depends on an external verification
  backend (e.g., visual/VLM verification) that was unavailable or degraded, rather than
  reporting a false pass or fail.
- **FR-008**: The tool MUST allow an agent to autonomously trigger build and deploy
  operations (e.g., building a PR's artifact and deploying it to a target VM) as part of
  its own run, without requiring a human to perform that step first.
- **FR-009**: The tool MUST expose its operations (discovery, readiness check, build/deploy,
  scenario run, result retrieval) as a dedicated tool/API interface (e.g., an MCP server)
  that a Claude agent can call directly, rather than requiring the agent to shell out to and
  parse the existing CLI's output.
- **FR-010**: The tool MUST prevent or safely serialize conflicting concurrent operations
  against the same target environment (e.g., two agents deploying to or running scenarios
  on the same VM at once), rejecting or queuing the conflicting request with a clear reason
  rather than allowing both to race.

### Key Entities

- **Test Scenario**: A named, versioned description of a UI test to run against a desktop
  application; has steps, target OS/environment requirements, and an expected outcome.
- **Target Environment (VM)**: A named desktop environment (OS + display server + GPU
  configuration) a scenario can run against; has a readiness state (reachable, dependencies
  present, deployed build/version).
- **Run Result**: The structured outcome of executing a scenario against an environment;
  has an overall verdict, per-step outcomes, referenced artifacts, and environment-readiness
  context.
- **Build/Artifact**: The application build (e.g., tied to a specific PR) deployed to a
  target environment; has an identity/version an agent can verify against what a scenario
  expects.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An agent with no prior session context can discover, select, and successfully
  run at least one valid test scenario end-to-end on the first attempt, without human
  intervention.
- **SC-002**: For a completed run, an agent can correctly state pass/fail/error and the
  reason using only the tool's structured result, verified against the actual outcome for
  100% of runs in a validation sample.
- **SC-003**: When the target environment is not ready (stale build, unreachable VM,
  missing dependency), the agent correctly identifies it as an environment problem rather
  than misreporting it as an application defect, in 100% of validation-sample cases.
- **SC-004**: Agents supplying an invalid scenario/environment combination receive a
  corrective response identifying valid alternatives, eliminating trial-and-error guessing
  in practice.
- **SC-005**: A readiness check completes in under 60 seconds — well under the minutes a
  full scenario run takes — so agents routinely check readiness before every run rather
  than skipping it.
- **SC-006**: Concurrent conflicting operations against the same target environment are
  never both allowed to proceed silently — in a validation sample, 100% of such conflicts
  are rejected or queued with a clear reason rather than producing corrupted or
  misattributed results.

## Assumptions

- Existing scenario authoring, build/deploy, and visual (VLM) verification capabilities
  remain the underlying mechanism; this feature is about making them consumable and
  self-explanatory for an AI agent operator, not replacing them.
- "Claude agents" means AI coding-agent sessions (e.g., Claude Code) operating mOSdat
  through a dedicated tool/API interface (e.g., an MCP server) exposing mOSdat's
  operations, on the existing host/Proxmox setup described in AGENTS.md — not a hosted
  multi-tenant service for arbitrary third parties. The underlying CLI remains available
  for human use; the new interface wraps the same operations rather than replacing them.
- Because agents can autonomously trigger build/deploy, the tool is responsible for
  preventing unsafe concurrent use of the same target VM (e.g., two operations racing to
  deploy/test against it) — see FR-010.
- Human operators remain the fallback for infrastructure-level concerns (Proxmox
  credentials, GPU passthrough issues, VM provisioning) that fall outside a single
  build/test/validation loop.
- The existing `mosdat-pr-preflight`, `mosdat-authoring`, and `mosdat-insight` skills are
  relevant prior art for what "agent readiness" partially already covers; this feature
  builds on and closes gaps in them rather than starting from zero.
