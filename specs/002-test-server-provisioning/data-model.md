# Data Model: Test Server Provisioning

Response/request shapes for the new server-provisioning tools, grounded in
research.md's decisions. No persistent state file — a running instance's own Docker
Compose project + container metadata IS the state.

## Server Reference

The input identifying what to provision — a PR number, release tag, RC tag, `develop`, or
any other tag Rocket.Chat's CI publishes.

| Field | Type | Notes |
|---|---|---|
| `ref` | string | e.g. `"pr-3464"`, `"6.15.0"`, `"develop"` — normalized to the exact GHCR tag suffix (see contracts) |
| `image` | string | Resolved `ghcr.io/rocketchat/rocket.chat:<ref>` — derived, not user-supplied |

## Server Instance

A mosdat-provisioned, running (or attempting-to-run) server for a given reference.

| Field | Type | Notes |
|---|---|---|
| `ref` | string | The reference this instance matches |
| `project` | string | Compose project name, `mosdat-rc-<sanitized-ref>` — also the discovery key |
| `state` | string | `"starting"` \| `"ready"` \| `"failed"` — never reported `"ready"` before `/livez` answers 200 (research.md Decision 2) |
| `url` | string \| null | `http://<mosdat-host>:<ephemeral-port>` once the port is known (available as soon as the container starts, even before `state: "ready"`) |
| `error` | string \| null | Set when `state == "failed"`: no-image (FR-002), startup-timeout (FR-004), or a Docker/Compose error |
| `elapsed_ms` | int | Time since this instance's containers were started (or since the request, if reused) |

## External Server Reference

Not provisioned, not tracked as a Server Instance — just a URL an agent already has and
wants a scenario to use for one call (research.md Decision 4). No dedicated entity/tool;
expressed as an optional `server_url` argument on `mosdat_run_functional`.

## Relationships

- A **Server Instance** is uniquely identified by its `ref` — requesting the same `ref`
  twice returns the same instance (spec User Story 4); requesting a different `ref`
  provisions a separate instance with its own Compose project and port, never colliding
  with another instance's containers or ports (Docker's own project/network namespacing
  + ephemeral port allocation, per research.md Decision 3 — no manual bookkeeping).
- A **Server Reference** with no published image (FR-002) never produces a Server
  Instance — the request fails immediately, naming the reference; there is no partial or
  substitute instance to represent.
- `mosdat_run_functional`'s scenario run points at either a **Server Instance**'s `url`
  (FR-001 path) or a directly-supplied **External Server Reference** URL (FR-009 path) —
  both flow into the same `workspace_url` template-var seam
  (`automation/commands/functional_cmd.py`), so the scenario itself doesn't need to know
  which case it's in.
