# mosdat Routines (R1)

Routines are parameterized, tested, reusable procedures that wrap a sequence of scenario steps with optional pre/postcondition verification and fallback branches. Think Cypress custom commands or Cucumber step definitions, but for desktop UI automation.

## Schema

Routines live at `shared/routines/<name>.yaml`. Fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str (kebab-case) | yes | Unique routine identifier |
| `description` | str | yes | One-line summary |
| `schema_version` | str | no | "v1" (default) |
| `inputs` | dict[str, RoutineInput] | no | Declared input parameters |
| `preconditions` | list[verify step] | no | Assertions run before main steps |
| `steps` | list[step] | no | Main body steps |
| `postconditions` | list[verify step] | no | Assertions run after main steps |
| `fallbacks` | list[RoutineFallback] | no | Conditional step branches |
| `on_failure` | list[step] | no | Diagnostic steps run if a postcondition fails |
| `tags` | list[str] | no | Discovery labels |

### RoutineInput

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | required | Parameter name |
| `type` | "string"\|"int"\|"bool"\|"list" | "string" | Type hint |
| `required` | bool | true | Whether the caller must supply this input |
| `default` | Any | null | Default value (only valid when required=false) |

### RoutineFallback

```yaml
fallbacks:
  - when: "capability.wayland"   # jinja2 expression against {capability, inputs}
    steps:
      - shell: echo wayland path
```

First matching `when:` expression wins. If no manifest is supplied, fallbacks are skipped and `steps` is used.

## Worked Example

```yaml
# shared/routines/open-settings.yaml
name: open-settings
description: Navigate to RC Settings > General tab
tags: [ui, settings]

preconditions:
  - verify: "RC main window is open and visible"

steps:
  - shell: xdotool mousemove 97 346 click 1
  - wait: 1
  - shell: xdotool mousemove 156 336 click 1
  - wait: 1
  - localize: "General tab in settings sidebar"
    click: left

postconditions:
  - verify: "Settings panel is open showing the General tab"
```

Use in a scenario:

```yaml
# shared/scenarios/functional/my-test.yaml
name: test-telephony-toggle
steps:
  - routine: open-settings

  - localize: "Telephony toggle switch"
    click: left

  - verify: "Telephony section shows enabled"
```

Or with inputs:

```yaml
steps:
  - routine:
      name: launch-app
      with:
        app_path: /opt/Rocket.Chat/rocketchat-desktop
        flags: --no-sandbox
```

## Expansion Order

For each `- routine:` call, the loader expands into:

```
[precondition steps] + [main steps or fallback] + [postcondition steps]
```

This happens **before** `ScenarioModel.model_validate`, so expanded steps are validated against the full step schema.

## CLI

```bash
mosdat routines list                   # name + description + tags (empty until R2 seeds)
mosdat routines show open-settings     # full YAML dump
mosdat routines test open-settings     # R3 stub — not yet implemented
```

## Input Resolution Priority

1. `with:` dict in the call site (highest)
2. Parent scenario `vars:` block
3. Routine `default:` (lowest; only for `required: false` inputs)
4. Missing required input → error at load time

## Cycle Detection

Routine A calling routine B calling routine A raises `RuntimeError` at scenario load time. The error message lists the full call chain.

## Testing Routines (R3)

Routines can be tested in isolation against a VM with a known pre-baked state (fixture).

### Basic invocation

```bash
mosdat routines test launch-rocketchat \
  --vms ubuntu2204 \
  --fixture rc-killed-userdata-wiped \
  --config examples/rocketchat.toml
```

Sample output:

```
[routines:test] Fixture: rc-killed-userdata-wiped — RC process killed and userData dirs wiped — baseline clean state
[routines:test] Probing SSH user@192.168.100.10 ...
[routines:test] SSH OK
[routines:test] Synthetic scenario: 6 steps
[routines:test] Screenshots: results/routines/ubuntu2204/launch-rocketchat_20260517_143022
[routines:test] Step 1 [shell]  PASS
[routines:test] Step 2 [shell]  PASS
[routines:test] Step 3 [wait]   PASS
[routines:test] Step 4 [verify] PASS
[routines:test] Result: PASS (18234 ms) — screenshots: results/routines/ubuntu2204/launch-rocketchat_20260517_143022
```

### Fixtures

Fixtures live at `shared/fixtures/<name>.yaml`. They describe a pre-baked VM state and optionally carry `setup_steps` and `teardown_steps` that bracket the routine under test.

| Field | Type | Description |
|-------|------|-------------|
| `name` | str (kebab-case) | Unique fixture identifier |
| `description` | str | One-line summary |
| `vm_state` | dict | Declarative state: `rc_killed`, `userdata_wiped`, `config`, `launched`, etc. |
| `setup_steps` | list[dict] | Steps run before the routine (uses standard step grammar) |
| `teardown_steps` | list[dict] | Steps run after the routine to restore baseline |

If `vm_state.config` is present, the harness calls `inject()` (I1) to write `config.json` + `servers.json` before running setup steps.

### Available fixtures

```bash
mosdat routines fixtures
```

```
rc-killed-userdata-wiped             RC process killed and userData dirs wiped — baseline clean state  [rc_killed=True, userdata_wiped=True, launched=False]
rc-launched-1-server-telephony-on    RC launched with 1-server config and telephony enabled — login page visible  [rc_killed=True, userdata_wiped=True, launched=True, config=<dict>]
rc-launched-2-server                 RC launched with 2-server config via inject_config — login page visible  [rc_killed=True, userdata_wiped=True, launched=True, config=<dict>]
```

### Routine input overrides

```bash
mosdat routines test launch-rocketchat \
  --vms ubuntu2204 \
  --fixture rc-killed-userdata-wiped \
  --with migrations_version=4.14.2 \
  --config examples/rocketchat.toml
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Routine passed (all steps + postconditions) |
| 1 | Routine failed (step or postcondition failure) |
| 2 | Fixture setup failed (inject error or fixture not found) |
| 3 | VM unreachable (SSH probe failed) |

## Capability-aware fallbacks

When `mosdat trace --write-manifest` has probed the target binary, the resulting manifest is stored in `shared/binary_capabilities/<sha>.json`. During scenario expansion, `expand_call` automatically loads the latest manifest (by mtime) and evaluates each fallback's `when:` expression against it.

### `when:` expression syntax

Each `when:` value is a **jinja2 expression** (not a template). It is evaluated with three context variables:

| Variable | Type | Description |
|----------|------|-------------|
| `capability` | dict | Full manifest dict (e.g. `{"accelerators": {"ctrl+,": "ok"}, ...}`) |
| `inputs` | dict | Resolved routine inputs for this call |
| `vars` | dict | Parent scenario vars block |

Examples:

```yaml
fallbacks:
  # Fire when the binary swallows alt+w
  - when: "capability.accelerators['alt+w'] == 'swallowed'"
    steps:
      - shell: echo use mouse nav instead

  # Fire when running in CI (parent scenario sets vars.env: ci)
  - when: "vars.env == 'ci'"
    steps:
      - shell: echo ci path

  # Catch-all: fires when no other fallback matched
  - when: default
    steps:
      - shell: echo default path
```

**Evaluation rules:**

- `when: default` is reserved — it fires only when no earlier fallback matched.
- If multiple fallbacks match, the **first declared** wins.
- If no manifest is available (directory missing or empty), all non-`default` fallbacks are skipped and `steps` is used. `default` still fires if present.
- Invalid jinja2 syntax in `when:` is rejected at YAML load time with a `ValidationError`.

### Worked example

```yaml
# shared/routines/open-settings.yaml
name: open-settings
description: Navigate to RC Settings > General tab
tags: [ui, settings]

steps:
  # Default: open via keyboard shortcut
  - shell: xdotool key ctrl+comma
  - wait: 1

fallbacks:
  - when: "capability.accelerators['ctrl+,'] == 'no_accel'"
    steps:
      # Fallback: click the kebab menu instead
      - shell: xdotool mousemove 97 346 click 1
      - wait: 1
      - shell: xdotool mousemove 156 336 click 1
      - wait: 1
```

When `mosdat trace` reports `ctrl+,` is not accelerated (e.g. intercepted by the window manager), the fallback fires automatically. No scenario changes needed.

### CLI introspection

```bash
# Show which fallback would fire given the latest auto-detected manifest:
mosdat routines explain open-settings

# Specify a manifest explicitly:
mosdat routines explain open-settings --manifest shared/binary_capabilities/abc123.json

# Override inputs:
mosdat routines explain open-settings --with accel_key=ctrl+comma
```

Sample output:

```
Routine:  open-settings
Manifest: asar_sha=abc123def456
Fallback: ACTIVE  when="capability.accelerators['ctrl+,'] == 'no_accel'"

Expanded steps (2):
   1. shell: xdotool mousemove 97 346 click 1
   2. shell: xdotool mousemove 156 336 click 1
```

## Coverage report

`mosdat routines report` (R6) cross-references every routine against all scenario YAML files and the harness test-history log to identify unused and untested routines.

```
mosdat routines report               # markdown table (default)
mosdat routines report --format tty  # colorized terminal output
mosdat routines report --format json # structured JSON
```

Sample output (`--format md`):

```
| Routine | Tags | Scenarios | Last test | Status |
|---|---|---|---|---|
| open-settings | settings, ui, rocketchat | Feature test | ubuntu2204/rc-launched 2026-05-17T10:00:00Z ✓ | ok |
| enable-telephony-toggle | telephony | (none) | never tested | unused |
| cleanup-rocketchat | setup, cleanup, rocketchat | (none) | never tested | unused |

**Total: 6, ok: 2, untested: 3, unused: 1, failing: 0**
```

Status values:
- `ok` — at least one scenario references the routine and its last harness run passed.
- `untested` — referenced by at least one scenario but no harness run recorded.
- `failing` — last harness run returned non-zero exit code.
- `unused` — no scenario references this routine at all.

Test history is stored at `results/routines-test-history.jsonl` (gitignored — auto-generated by `mosdat routines test`).

## Schema versioning

Every routine declares its schema version with `schema_version: v1` (the default when omitted). This field exists so mosdat can detect version mismatches and apply migrations.

### Version constants

```python
from automation.routines import CURRENT_SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS
# CURRENT_SCHEMA_VERSION = "v1"
# SUPPORTED_SCHEMA_VERSIONS = ["v1"]
```

### Validation rules

- `schema_version: v1` (or omitted) — accepted and loaded normally.
- A version in `SUPPORTED_SCHEMA_VERSIONS` but older than current — migration applied before validation (forward-compatible load).
- A version not in `SUPPORTED_SCHEMA_VERSIONS` — `ValidationError` with message: `"schema_version 'vX' is newer than this mosdat (v1); upgrade mosdat or downgrade the routine"`.

### Migration story

When a breaking schema change is required, bump `CURRENT_SCHEMA_VERSION` in `automation/routines/__init__.py`, add the new version to `SUPPORTED_SCHEMA_VERSIONS`, and write a migration function in `automation/routines/loader.py::_migrate_to_current`:

```python
def _migrate_to_current(data: dict) -> dict:
    version = data.get("schema_version", CURRENT_SCHEMA_VERSION)
    if version == "v1":
        # rename old field → new field
        if "old_field" in data:
            data["new_field"] = data.pop("old_field")
        data["schema_version"] = "v2"
        version = "v2"
    if version == CURRENT_SCHEMA_VERSION:
        return data
    return data
```

Migration runs before `Routine.model_validate(data)` in both `load_routine` and `list_routines`. Old routines continue to load transparently; they just need their `schema_version` to be in `SUPPORTED_SCHEMA_VERSIONS`.

### CLI

```bash
mosdat routines version
# current:   v1
# supported: v1
```

## Worked example: PR #3325 master-toggle

`shared/scenarios/functional/3325-master-toggle.yaml` is the canonical routines-first authoring example for PR #3325 Gap G1 (F1: master telephony toggle).

**Before** (236 lines, inline shell blocks):
- 3 multi-line `shell:` heredoc blocks for config pre-staging (config.json + servers.json)
- 2 inline `shell:` blocks for RC launch (X11 env setup + nohup)
- 1 inline `shell:` block for intermediate kill between phases
- 1 manual final cleanup shell block

**After** (76 lines, routine calls):
- `- routine: cleanup-rocketchat` replaces kill + wipe
- `- routine: launch-rocketchat with: {servers: [...], telephony_enabled: false/true}` replaces config writing + launch + wait
- `- routine: dispatch-tel-link with: {number: "+5511999999999"}` replaces dispatch shell
- `- routine: verify-app-alive` replaces final health check
- Explicit `verify:` steps test the actual G1 assertion (no modal / modal present)

Line-count delta: **236 → 76 (-160 lines, -68%)**. Expanded step count: **32 steps** (after routine expansion, same behavior).

The converted scenario:

```yaml
name: "Rocket.Chat Desktop — Click-to-Call master telephony toggle (PR #3325 G1)"
vars:
  workspace_url: "https://rocketchat.jeanbrito.com"
  mobile_url: "https://mobile.rocket.chat"

phases:
  - id: A
    name: "toggle OFF baseline"
    from_step: 1
  - id: B
    name: "toggle ON via config pre-stage"
    from_step: 7

steps:
  - routine: cleanup-rocketchat
  - routine:
      name: launch-rocketchat
      with:
        servers:
          - { title: "Workspace", url: "{{ workspace_url }}" }
          - { title: "Mobile RC", url: "{{ mobile_url }}" }
        telephony_enabled: false

  - routine:
      name: dispatch-tel-link
      with: { number: "+5511999999999" }
  - verify: "No telephony Select Server modal is visible..."
    verify_not: "A telephony modal or dial pad dialog is overlaying..."
    verify_timeout: 15
    retries: 3

  - routine: cleanup-rocketchat
  - routine:
      name: launch-rocketchat
      with:
        servers: [...]
        telephony_enabled: true

  - routine:
      name: dispatch-tel-link
      with: { number: "+5511999999999" }
  - verify: "The Rocket.Chat application window shows a modal dialog..."
    verify_timeout: 20
    retries: 5

  - key: Escape
  - wait: 2
  - routine: verify-app-alive
  - routine: cleanup-rocketchat
```

What improved:
- **Readability**: intent visible at a glance — cleanup, launch-with-config, dispatch, assert.
- **Reuse**: the same `launch-rocketchat` routine is invoked twice with different `telephony_enabled` values — the 80-line config-writing shell block runs once per routine and is not duplicated in the scenario.
- **Isolated test coverage**: `cleanup-rocketchat`, `launch-rocketchat`, and `dispatch-tel-link` are independently testable via `mosdat routines test`. A failure in one routine immediately identifies which sub-procedure broke.
- **Maintainability**: changing the userData directory layout or the config.json schema only requires updating the routine, not every scenario that uses it.

---

## Cursor motion (Mn series)

mOSdat no longer teleports the VNC cursor. Every click and hover step traverses
a plausible curved path so hover handlers fire and transient popups stay open
between sequential clicks. Background: `docs/research/cursor-motion.md`.

### Profile system

Four profiles are available via `automation/transport/cursor_motion.py`:

| Profile | Behaviour |
|---------|-----------|
| `instant` | Single step, zero delay — equivalent to old teleport behaviour |
| `linear` | Straight line at constant speed |
| `bezier` | Quadratic Bezier + perpendicular jitter + ease-in/out tween **(default)** |
| `windmouse` | Physics gravity/wind model; more organic, variable step count |

`bezier` is recommended. It emits 8–16 pointer events over 80–300 ms (distance-
scaled), fires hover handlers well before the final position, and has a
deterministic frame count that fits the 60 fps VNC budget.

### Global TOML `[cursor]` config

```toml
[cursor]
profile        = "bezier"   # instant | linear | bezier | windmouse
duration_ms    = 150        # target move time (auto-scaled from distance if 0)
hover_dwell_ms = 0          # ms cursor rests on element before click fires (global default)
seed           = "auto"     # int or "auto"; set a fixed int for reproducible CI replays
```

### Per-step overrides

Any `click:` or `hover:` step can override the global profile and add a dwell:

```yaml
steps:
  - localize: "the kebab menu button in the sidebar"
    click: left
    motion: bezier       # instant | linear | bezier | windmouse
    dwell_ms: 200        # cursor rests here for 200 ms before the click fires
```

`dwell_ms` is the critical knob for hover-sensitive UI. It keeps the cursor on
the element long enough for transient popups and submenus to open before the
next step begins. See `shared/recipes/cursor-teleport-misses-hover-handlers.yaml`
for the full diagnostic recipe.

### CLI: `--cursor-instant` for fast CI runs

```bash
mosdat functional examples/rocketchat.toml --vms ubuntu2404 \
    --cursor-instant
```

Forces `profile = "instant"` globally for the run — identical to pre-Mn
teleport behaviour. Useful in CI where speed matters and no hover-sensitive
interactions are exercised.

### Worked example: hover step that fires a submenu

```yaml
# shared/routines/open-settings.yaml (fallback path)
fallbacks:
  - when: "capability.accelerators['alt+w'] == 'swallowed'"
    steps:
      - localize: "the kebab or three-dot menu button in the sidebar"
        click: left
        # Popup auto-dismisses if cursor doesn't dwell — keep it alive.
        dwell_ms: 250
      - wait: 2
      - localize: "the 'Settings' menu item in the popup"
        click: left
```

Without `dwell_ms: 250` the kebab popup closes before the second `localize`
completes because the cursor moves away immediately after the click event.
With bezier motion + 250 ms dwell the popup stays open until VLM resolves the
menu item.
