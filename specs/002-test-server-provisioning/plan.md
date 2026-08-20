# Implementation Plan: Test Server Provisioning

**Branch**: `002-test-server-provisioning` | **Date**: 2026-08-20 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-test-server-provisioning/spec.md`

## Summary

mOSdat's desktop-client tests currently point at a fixed external Rocket.Chat server
(`rocketchat.jeanbrito.com`) that can silently drift out of version-sync with the client
build under test — the exact failure hit live in `specs/001-.../` validation. This
feature adds Docker-based provisioning of a real Rocket.Chat *server* matching a specific
PR/release/RC image, plus a lightweight way to point a scenario at any already-running
server URL without provisioning anything. Three new MCP tools
(`mosdat_server_provision`/`_teardown`/`_list`) plus one small extension to the existing
`mosdat_run_functional` (an optional `server_url` override). Grounded directly against
Rocket.Chat's own CI workflow and its own CI compose reference file — not generic docs.

## Technical Context

**Language/Version**: Python >=3.11 (`pyproject.toml`), consistent with the rest of
`automation/`.

**Primary Dependencies**: `docker` + `docker compose` CLI (subprocess calls, like every
other external tool mosdat already shells out to — `git`, `gh`, `yarn`). CLI binaries
confirmed present; the Docker **daemon** was found NOT running when actually exercised
(only the CLI was present) and had to be started (`open -a Docker`) before anything
Docker-dependent would work — a `docker info` precondition check belongs in the
provisioning path itself (see tasks.md), not just assumed. No new Python package
dependency.

**Storage**: None persistent. A provisioned instance's state IS its Docker Compose
project + container metadata (research.md Decision 3) — no JSON/SQLite tracking file to
keep in sync with reality.

**Testing**: pytest, matching `specs/001-.../plan.md`'s conventions — mocked
subprocess/HTTP calls for unit tests (`docker`, `docker compose`, the `/livez` poll), a
`-m live` integration test requiring real Docker + network access to `ghcr.io` for the
full quickstart.md flow.

**Target Platform**: Provisioned server containers run on the mosdat host (this Mac);
reachability from the Proxmox-hosted desktop-client VMs, in the actual direction this
feature needs (VM → Mac), is confirmed **live** (a real VM reached a Docker-exposed port
on the Mac over the `192.168.13.x` LAN) — not just inferred from the opposite-direction
SSH/VNC traffic mosdat already does elsewhere. Confirmed specifically *through Docker's*
port-forwarding — a plain non-Docker process on the same LAN IP was blocked by macOS's
Application Firewall, so this reachability is Docker-specific, not general (research.md
Decision 5). No new host/VM.

**Project Type**: Single existing Python project — new module in the existing
`automation/commands/` package, following the exact dual CLI+MCP pattern established by
`build.py` and `ssh_bootstrap.py`.

**Performance Goals**: A provisioned server typically becomes ready within the ~60s grace
period Rocket.Chat's own CI health check uses (research.md Decision 2); a bounded overall
startup timeout (FR-004) beyond that reports a clear failure rather than an indefinite
wait — exact timeout value is an implementation constant, not a hard spec requirement
(recommend 180s: 3x the CI's own grace period, generous for a cold image pull).

**Constraints**: Must never report `state: "ready"` before `/livez` genuinely answers
(FR-003) — a running container is not the same as a ready server. Must never substitute a
different image when the requested reference has no published one (FR-002) — a
"no image" result is terminal, not a trigger for fallback logic.

**Scale/Scope**: Single host, small number of concurrent instances (one per
reference/PR actively under test, not a fleet) — matches spec Assumptions (ephemeral,
no durable multi-tenant server farm). In scope: the 3 new tools + the 1 extension listed
above. Out of scope: RC's microservices/EE topology (research.md Decision 2), a
dedicated Proxmox VM for hosting (research.md Decision 5), persisting server data across
teardown.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is still the unfilled template (confirmed again this
pass) — gate skipped gracefully, same as `specs/001-.../plan.md`. Not a blocker.

## Project Structure

### Documentation (this feature)

```text
specs/002-test-server-provisioning/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/
│   └── server-tools.md  # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

Single existing Python project — no new top-level directory, following the exact
`build.py`/`ssh_bootstrap.py` dual CLI+MCP pattern:

```text
automation/
├── commands/
│   └── rc_server.py         # new — run_server_provision()/run_server_teardown()/
│                             #   run_server_list(), add_*_subparser(), the Docker
│                             #   Compose orchestration, /livez polling
├── mcp_tools.py               # extend — 3 new tool handlers (_server_provision,
│                             #   _server_teardown, _server_list) + server_url param
│                             #   threaded into the existing _run_functional
├── mcp_server.py              # extend — dispatch wiring for the 3 new tools
├── main.py                    # extend — CLI dispatch wiring
└── commands/parser.py         # extend — subparser wiring

automation/
└── commands/
    └── templates/
        └── rc-server-compose.yml  # checked-in Compose template (research.md Decision 2):
                                    #   rocketchat + mongo services, ${RC_IMAGE}
                                    #   substitution, ephemeral host port ("0:3000")

tests/
└── test_rc_server.py        # new — mocked docker/compose/HTTP-poll unit tests;
                              #   -m live test for the real quickstart.md flow
```

**Structure Decision**: Extend the existing single-package layout in place, exactly
mirroring how `specs/001-agent-desktop-testing/` added `build_on_windows_vm()` and
`ssh_bootstrap.py` — a new `automation/commands/*.py` module, MCP tool handlers added to
the existing `mcp_tools.py`/`mcp_server.py`, no new project or service boundary. The one
new artifact type is the checked-in Compose template (data, not code) — everything else
follows established conventions.

## Complexity Tracking

*No constitution violations to justify — gate skipped (unfilled template), and this
design introduces no new project, service, or Python dependency. The one new tool
(`docker`/`docker compose` CLI) is invoked via subprocess exactly like mosdat's existing
`git`/`gh`/`yarn` calls — not a new class of dependency for this codebase.*
