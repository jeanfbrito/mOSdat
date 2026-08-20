# Quickstart: Validating Test Server Provisioning

Proves the feature works end-to-end using only the structured tool results — the same
bar as `specs/001-agent-desktop-testing/quickstart.md`.

## Prerequisites

- Docker + Docker Compose available on the mosdat host (already confirmed this session:
  `docker --version` / `docker compose version`).
- Network access from the mosdat host to `ghcr.io` (to pull server images).
- A real, currently-open Rocket.Chat.Electron PR with a published server image (a
  same-repo PR, not a fork) to use as the "happy path" reference — check via `gh pr list
  --repo RocketChat/Rocket.Chat` or similar, or just use `develop`.

## 1. Provision a server for a real reference (FR-001, User Story 1)

```
mosdat_server_provision({"ref": "develop"})
```
Expected: `ok: true`, `state` starts `"starting"` then (poll again after ~60-90s)
`"ready"`, with a real `url`. Manually confirm: `curl http://<url>/livez` returns 200.

## 2. Reuse instead of duplicating (FR-007/FR-004, User Story 4)

Call `mosdat_server_provision({"ref": "develop"})` again immediately. Expected: same
`url` as step 1, `elapsed_ms` reflects the ORIGINAL start time (not reset), and
`docker ps` shows no second `rocketchat`/`mongo` container pair for this reference.

## 3. No-image reference fails clearly, no substitution (FR-002, User Story 3)

```
mosdat_server_provision({"ref": "pr-1"})
```
(Assuming PR #1 predates this CI setup, or pick any known-nonexistent tag.) Expected:
`ok: false`, `error` names `"pr-1"` specifically and says no image exists — no `url`, no
silent fallback to `develop` or anything else.

## 4. Point a scenario at the provisioned server (FR-005, ties into `specs/001-.../quickstart.md`)

```
mosdat_run_functional({"vm": "ubuntu2404", "scenario": "rocketchat-smoke-linux", "server_url": "<url from step 1>"})
```
Expected: the scenario's `launch-rocketchat` routine pre-stages `servers.json` pointing
at the provisioned server (not the configured default), and the run doesn't hit the
"unsupported version of Rocket.Chat" gate this feature exists to eliminate.

## 5. Point at an already-running server without provisioning anything (FR-009, User Story 2)

Start any Rocket.Chat server manually (or reuse step 1's instance's URL as a stand-in for
"already running"). Call `mosdat_run_functional` with that `server_url` directly, having
never called `mosdat_server_provision` for it. Expected: the scenario connects
successfully; `docker ps`/`mosdat_server_list` shows no new mosdat-managed instance was
created as a side effect of this call.

## 6. List and tear down (FR-006/FR-007, User Story 5)

```
mosdat_server_list({})
```
Expected: shows the `develop` instance from step 1 (and NOT anything from step 5, since
that wasn't mosdat-provisioned).

```
mosdat_server_teardown({"ref": "develop"})
```
Expected: `ok: true, torn_down: true`. Follow-up `mosdat_server_list({})` no longer shows
it; `docker ps` confirms no leftover `mosdat-rc-develop` containers.

## Success

All six steps match their expected output using only the structured tool results — this
demonstrates SC-001 through SC-006 live, and specifically closes the exact failure mode
(stale external server vs. fresh client build) hit live during `specs/001-.../quickstart.md`.
