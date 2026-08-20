# Contract: Test Server Provisioning MCP Tools

Transport: same stdio JSON-RPC `mosdat-mcp` server as `specs/001-agent-desktop-testing/`.
Every result follows the same uniform envelope: `{"ok": bool, "error": string|null,
"degraded": string[], ...tool fields}`.

Three new tools, plus one extended existing tool.

---

## `mosdat_server_provision` (new)

Idempotent: if a matching instance is already running, returns its current state
immediately (no new provisioning) — satisfies both "provision" and "status check"
(spec User Story 1 and User Story 4).

**Input**:
```json
{"ref": "pr-3464"}
```
`ref` accepts a PR (`pr-<N>` or bare `<N>`, normalized to `pr-<N>`), a release/RC tag, or
`develop` — whatever Rocket.Chat's CI publishes.

**Output — ready**:
```json
{
  "ok": true,
  "error": null,
  "degraded": [],
  "ref": "pr-3464",
  "state": "ready",
  "url": "http://192.168.13.20:54217",
  "elapsed_ms": 42150
}
```

**Output — still starting** (agent should poll again, not treat as failure):
```json
{
  "ok": true,
  "error": null,
  "degraded": [],
  "ref": "pr-3464",
  "state": "starting",
  "url": "http://192.168.13.20:54217",
  "elapsed_ms": 8000
}
```
`url` is present as soon as the container is up and the port is known — even before
`/livez` answers — since the caller may want to know where it'll be.

**Output — no published image (FR-002, no fallback)**:
```json
{
  "ok": false,
  "error": "no published server image for 'pr-9999' — this reference cannot be tested this way (fork PR, or no server-side change published)",
  "degraded": [],
  "ref": "pr-9999",
  "state": "failed"
}
```

**Output — startup timeout (FR-004)**:
```json
{
  "ok": false,
  "error": "server for 'pr-3464' did not become ready within 180s — last state: container running, /livez not yet answering",
  "degraded": [],
  "ref": "pr-3464",
  "state": "failed",
  "elapsed_ms": 180000
}
```

## `mosdat_server_teardown` (new)

**Input**: `{"ref": "pr-3464"}`

**Output**:
```json
{"ok": true, "error": null, "degraded": [], "ref": "pr-3464", "torn_down": true}
```
If no instance exists for `ref`: `{"ok": true, "torn_down": false, ...}` (already gone is
not an error — teardown is idempotent, same convention as provisioning). Never touches an
**External Server Reference** (a URL an agent passed to `mosdat_run_functional` directly)
— there is nothing to tear down there, by construction (research.md Decision 4).

## `mosdat_server_list` (new)

**Input**: `{}`

**Output**:
```json
{
  "ok": true,
  "error": null,
  "degraded": [],
  "instances": [
    {"ref": "pr-3464", "state": "ready", "url": "http://192.168.13.20:54217", "elapsed_ms": 340000}
  ]
}
```

## `mosdat_run_functional` (extended — one new optional field)

Existing contract (`specs/001-agent-desktop-testing/contracts/mcp-tools.md`) unchanged
except one new optional input field:

```json
{"vm": "ubuntu2404", "scenario": "rocketchat-smoke-linux", "server_url": "http://192.168.13.20:54217"}
```

`server_url` overrides the scenario's configured `workspace_url` for this call only
(research.md Decision 4) — satisfies FR-005/FR-009 for both a `mosdat_server_provision`
result's `url` and any other already-running server the agent already knows about (e.g. a
local dev instance). Omit it to keep today's behavior (the config's static
`workspace_url`, e.g. `rocketchat.jeanbrito.com`) — nothing about existing callers changes.

---

## Cross-cutting rules

- **FR-008 (no port collisions)**: every instance binds its app container to a host port
  that mosdat pre-allocates via an OS bind-probe (so `ROOT_URL` can be set correctly at
  `compose up` time — research.md Decision 3 amendment) instead of Docker's own `0:`
  ephemeral mapping; the OS's own port allocator still guarantees no collision.
  `mosdat_server_provision`/`_list` report that pre-allocated port.
- **FR-003 (three states, never a false "ready")**: `state` is computed by actually
  calling `GET <url>/livez` — a running container alone is `"starting"`, never `"ready"`.
- **Discovery is Docker-native**: a matching instance is found via its Compose project
  name (`mosdat-rc-<sanitized-ref>`), not a separate state file — restarting the mosdat
  host/process loses no state, since the containers themselves are the source of truth.
