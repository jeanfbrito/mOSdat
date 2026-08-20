# Research: Test Server Provisioning

## Decision 1: Pull `ghcr.io/rocketchat/rocket.chat:<ref>` directly — bare tag is a real multi-arch manifest

**Decision**: Resolve a reference (PR/release/RC/tag) to `ghcr.io/rocketchat/rocket.chat:pr-<N>`
(or the equivalent bare tag for releases/RCs/`develop`) with no per-architecture suffix.

**Rationale**: Verified directly against `RocketChat/Rocket.Chat`'s
`.github/workflows/ci.yml`: the `build-gh-docker` job builds and tags per-arch images
(`pr-<N>-amd64`/`pr-<N>-arm64`), but the separate `build-gh-docker-publish` job's
"Create and push multi-arch manifests" step runs `docker buildx imagetools create --tag
"${IMAGE}" ... "${refs[@]}"` where `IMAGE` uses the bare, arch-suffix-free `DOCKER_TAG`.
That means `ghcr.io/rocketchat/rocket.chat:pr-<N>` is a real, directly-pullable manifest
list — Docker/containerd picks the right architecture automatically. No per-arch logic
needed in mosdat.

Publishing itself remains gated to same-repo PRs, releases, and `develop` (confirmed in
the prior research pass against the `publish-image` condition) — fork PRs get no image at
all, matching spec FR-002/User Story 3.

**Unresolved, deliberately deferred to implementation**: whether these GHCR packages are
publicly/anonymously pullable could not be confirmed via the GitHub API (insufficient
token scope). Rocket.Chat is public OSS so this is likely, but implementation must treat
an auth failure on `docker pull` as a distinct, clearly-reported failure mode — not
silently retry or assume.

**Alternatives considered**: Docker Hub (`rocketchat/rocket.chat`) — rejected as the PR-image
source; the workflow only pushes there on release/develop, never PRs, so it can't serve
the PR case at all.

## Decision 2: Reuse Rocket.Chat's own CI compose reference (`docker-compose-ci.yml`), trimmed to two services

**Decision**: Base the provisioned stack on `RocketChat/Rocket.Chat`'s own
`docker-compose-ci.yml` — the exact file their CI uses to run these exact images for
e2e tests — rather than the generic self-hosting docs, and trim it to just the two
services a single-workspace functional test needs: `rocketchat` (the app) and `mongo`.

**Rationale**: This is more authoritative than generic docs for this exact use case (it's
literally the config Rocket.Chat validates PR images against), and it resolves the two
things the spec flagged as needing precision:
- **MongoDB replica-set init is automatic**, not a separate init container/job: the mongo
  service (`mongodb/mongodb-community-server:8.0-ubi8`) uses a custom `entrypoint` that
  starts `mongod --replSet rs0 --bind_ip_all`, waits for the server to answer, then runs
  the replica-set initiation itself. mosdat does not need to orchestrate this — just
  start the container and wait.
- **A concrete readiness endpoint exists**: the compose file's own healthcheck is
  `wget --spider http://127.0.0.1:3000/livez`, with `start_period: 60s, interval: 2s,
  timeout: 5s, retries: 5`. This is the exact discriminator for FR-003/FR-004 — poll
  `GET http://<host>:<port>/livez` after start, treat the first 60s as expected
  "still starting," and only declare "failed" after a bounded window well past that.

The full reference file also includes RC's microservices split (authorization/presence/
ddp-streamer/queue-worker/nats) and Traefik — not needed for a single-workspace desktop
client test; a minimal two-service compose is the right scope here (YAGNI — mosdat isn't
testing RC's own microservice topology, just giving a desktop client something real to
talk to).

**Alternatives considered**: Generic self-hosting docker-compose docs from
docs.rocket.chat — not fetched; the CI compose file is strictly more authoritative and
complete for exactly this use case, and pulling in a second, possibly-divergent reference
would add confusion, not confidence.

## Decision 3: Docker Compose project-per-reference, ephemeral host port, label-based discovery — no separate state file

**Decision**: One Docker Compose project per provisioned reference, named
`mosdat-rc-<sanitized-ref>` (e.g. `mosdat-rc-pr-3464`). Bind the app container's port 3000
to an ephemeral host port (`"0:3000"` in the compose file) rather than a fixed/allocated
one, and discover the actual assigned port afterward via `docker port`. Discover
already-running instances by Compose project name / a `mosdat.managed=true` label — no
separate JSON/SQLite state file tracking what's running.

**Rationale**: Docker Compose already gives each project its own container/network
namespace for free — using the reference itself (sanitized) as the project name means
"is a server for this reference already running" is answerable directly from `docker ps`/
`docker compose ls`, with zero risk of a separate state file drifting from what's actually
running (exactly the class of bug a hand-rolled tracking file invites: orphaned entries
after a crash, stale state after a manual `docker rm`). Binding to an ephemeral host port
(`0:3000`) sidesteps FR-008's port-collision requirement entirely — Docker's own port
allocator already guarantees no two containers collide, so mosdat needs zero manual port
bookkeeping.

**Alternatives considered**: A JSON state file recording provisioned instances and their
assigned ports — rejected; it can drift from reality (a crash or manual `docker rm`
desyncs it) in exactly the way Docker's own container/label metadata cannot, since that
metadata IS the reality.

**Amendment (2026-08-19)**: the host port is now pre-allocated by mosdat itself — a
bind-probe (`socket.bind(("", 0))`, read `getsockname()[1]`, close) run just before
`compose up` — rather than left entirely to Docker's `0:3000` ephemeral mapping. This was
required because `ROOT_URL` must be passed into the container at startup for the LAN
client to load a working UI (FR-010; a mismatched `ROOT_URL` breaks client asset loading
per Rocket.Chat's own troubleshooting docs), and the actual port was previously only
knowable *after* `compose up` via `docker port`. Docker still picks the port in every
practical sense — mosdat just learns the OS's choice a moment earlier via the same
ephemeral-port mechanism, and releases the socket immediately so Docker can bind it; the
resulting probe-then-bind race window is accepted at this project's documented scale
(small number of concurrent instances, not a fleet — plan.md Scale/Scope). Path B (an
already-running project discovered by label) is unaffected — it still learns the port via
`docker port` since `ROOT_URL` was already set when that instance was first provisioned.

## Decision 4: "Point at an already-running server" (FR-009) needs no new provisioning tool — just a per-call override on the existing run tool

**Decision**: Satisfy FR-009 (User Story 2 — point a scenario at an already-running/local
server) by adding one optional parameter to the *existing* `mosdat_run_functional` tool
(`server_url`, overriding the configured `workspace_url` for that call only) rather than
building a separate "register an external server" tool or provisioning mechanism.

**Rationale**: Traced the *actual* runtime path for `mosdat_run_functional` (an MCP tool
call, not the CLI): it does NOT go through `automation/commands/functional_cmd.py` at all.
`functional_cmd.py`'s `vars_.setdefault("workspace_url", config.functional.workspace_url)`
is a real, already-working seam, but it's exclusively the CLI's (`cmd_functional`'s) — MCP
never touches it. The actual override point is
`automation/mcp_tools.py::_invoke_functional_runner`, called from `_run_functional` (the
`mosdat_run_functional` handler). It has its own, separate implementation with two
templating paths to satisfy: (1) `load_test_yaml(scenario_path, cfg, platform=...,
cli_vars=...)` — its `cli_vars` kwarg (already designed for exactly this: "CLI values win
on conflict", per its own docstring) is the override point for scenarios using
`{{ double_brace }}` vars resolved at load time; (2) the `vars_` dict built from scratch
and passed to `runner.run_test(vars=vars_, ...)` — the override point for scenarios using
`{single_brace}` vars resolved at `resolve_vars`/`run_test` time (the syntax used by the
real desktop-client smoke scenarios, e.g. `shared/scenarios/functional/linux/rocketchat-
smoke*.yaml`). Before this feature, `_invoke_functional_runner` discarded `load_test_yaml`'s
own vars entirely and never included `workspace_url` in `vars_` at all — a pre-existing bug
fixed alongside the override (any MCP-invoked run of a single-brace scenario literally typed
the literal string `{workspace_url}` into the app's server-URL field, since `resolve_vars`
leaves unmatched keys untouched). Adding the optional `server_url` argument therefore touches
both `_invoke_functional_runner` call sites — not just "thread one new optional param" into
an existing working seam. This also directly satisfies FR-005 (one URL-shaped interface
regardless of whether the URL came from FR-001's provisioning or FR-009's direct pointer).
`functional_cmd.py`'s CLI-only seam is untouched by this feature — it was already correct
for its own (non-MCP) entry point.

**Alternatives considered**: A `mosdat_server_register(url)` tool that stores an external
server as if it were a managed instance — rejected; it would need fake state (a "this
server is externally managed, never touch it" flag) for something that's really just "use
this other URL for this one call," which the existing per-call `vars` override already
expresses more simply and without inventing a new concept.

## Decision 5: Provision on the mosdat host itself, using the Docker already confirmed present

**Decision**: Run provisioned server instances on the mosdat host (this Mac), per the
spec's resolved Assumption — Docker and Docker Compose are already confirmed present and
working there (used nowhere else in mosdat today, so this is the first real Docker
consumer in the codebase, but no new host/VM to provision).

**Rationale**: The desktop-client VMs are on the same Proxmox LAN (`192.168.13.x`).
Reachability in the direction this feature actually needs — a VM connecting **in** to a
port on the Mac — is NOT the same fact as the Mac reaching out to VMs (which every
existing SSH/VNC call already proves); that direction was verified live and separately
during `/speckit-analyze`: a real VM (`windows11`) successfully reached a Docker-exposed
port (a throwaway `nginx:alpine` container) on the Mac over the LAN. Two things this
check surfaced: (1) the Docker daemon was not actually running when first described as
"available" — only the CLI was present, and had to be started (`open -a Docker`) before
this test could even run; (2) a raw, non-Docker process bound to the same LAN IP (`python
-m http.server`) was blocked by macOS's Application Firewall, while the Docker-exposed
port was not — meaning this reachability is specifically a property of Docker's own
port-forwarding layer, not of the host's firewall being open in general. Provisioning on
a *new* dedicated VM would add a VM to provision and maintain for no reachability benefit
the host doesn't already have.

**Alternatives considered**: A dedicated Proxmox VM — rejected per the spec's resolved
assumption; revisit only if a genuine reachability problem surfaces in practice.
