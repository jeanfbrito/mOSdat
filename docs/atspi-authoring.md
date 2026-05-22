# AT-SPI Authoring Guide

How and when to drive Linux UI tests via the AT-SPI accessibility bus instead
of VLM `localize_click`. For scenario authors who already know how to write
`localize:` / `verify:` steps and want zero-flake on standard widgets.

Shipped 2026-05-22 (Stages 1+2+3 of the swiss-knife epic). Live-verified
against `ubuntu2204@192.168.13.81`.

## TL;DR

- **AT-SPI = semantic Linux accessibility-tree click/verify.** Replaces brittle
  VLM `localize_click` for standard HTML widgets (push buttons, entries, links,
  menu items, frames).
- **When the a11y tree exposes the widget**, latency and flake drop to near
  zero. When the region is canvas-rendered or custom-painted (chat message
  list, emoji picker, video panel, rich-text editor), the tree is opaque —
  keep VLM `localize:` there. See KNOWN_ISSUES.md item #2.
- **Discover live role+name combos** with `mosdat atspi-dump CONFIG.toml --vms VM`.
  Copy what you see into YAML.
- **Three new step fields**: `atspi:` (click), `verify_atspi:` (existence
  check), `wait_for:` (poll-on-VM until condition fires or timeout).

## Quick start — replace one VLM step with AT-SPI

Before (VLM screenshot + VLM model + coord click):

```yaml
- label: "click Login"
  localize: "the Login button on the welcome screen"
  retries: 3

- label: "Email field present"
  verify: "the Email or username text input is visible"
  verify_timeout: 5
```

After (AT-SPI semantic click + verify):

```yaml
- label: "click Login"
  atspi: {role: "push button", name: "Login"}
  retries: 1

- label: "Email field present"
  verify_atspi: {role: "entry", name: "Email or username"}
  retries: 1
```

Both forms shipped working in `shared/scenarios/functional/_smoke-atspi.yaml`.

## Discovery — find role+name combos

The authoring loop is: run the app on the VM, dump the live tree, copy the
`[role] name` pairs you need into YAML.

```bash
# Default: indented ASCII tree, app_filter=rocket, max_depth=30.
mosdat atspi-dump examples/rocketchat.toml --vms ubuntu2204 --format tree | less

# Raw JSON (role, name, path, depth, n_actions, action_names per node).
mosdat atspi-dump examples/rocketchat.toml --vms ubuntu2204 --format json

# Histogram of role types — quick triage of what is reachable.
mosdat atspi-dump examples/rocketchat.toml --vms ubuntu2204 --format roles

# Save to file instead of stdout.
mosdat atspi-dump examples/rocketchat.toml --vms ubuntu2204 \
    --format tree --output /tmp/rc-tree.txt

# Walk the entire desktop (bypass --app-filter).
mosdat atspi-dump examples/rocketchat.toml --vms ubuntu2204 --raw

# Target a different app.
mosdat atspi-dump examples/rocketchat.toml --vms ubuntu2204 --app-filter firefox
```

Notes:

- The target app **must be running on the VM first**; otherwise the tree is
  empty. Launch RC (or whatever app) before dumping.
- `--app-filter` is a case-insensitive substring match on the top-level
  application name. Default is `rocket`.
- `--max-depth` caps tree-walk depth (default 30 — usually more than enough).
- Windows VMs are rejected — AT-SPI is the GNOME accessibility bus.

## Schema reference

All three step fields are dicts that accept a `find` payload of
`{role, name, name_substr}`. See `automation/atspi/client.py` for the live
contract.

### `atspi:` — semantic click

```yaml
- label: "click Login"
  atspi: {role: "push button", name: "Login"}
  retries: 1
```

Fields:

- `role` (str, required) — AT-SPI role string. Common roles seen on RC:
  `push button`, `entry`, `frame`, `menu item`, `page tab`, `label`,
  `section`, `panel`, `document web`, `link`, `static`.
- `name` (str, optional) — accessible name. If omitted, matches the first
  node of `role`.
- `name_substr` (bool, default `false`) — if true, `name` is a substring;
  else exact match. See KNOWN_ISSUES.md item #4 for the `name_substr` +
  missing-`name` edge case.
- `action_idx` (int, default `0`) — which AT-SPI action to invoke (e.g. for
  a push button, `0 = press`, `1 = showContextMenu`). Used in
  `via: "action"` mode only.
- `app_filter` (str, default `"rocket"`) — top-level app substring filter,
  case-insensitive. Override for non-RC apps.
- `via` (str, default `"pointer"`) — click mode. See "Choosing a click
  mode" below.
- `motion` (str, optional) — cursor-motion profile override for
  `via: "pointer"`; passed through to the VNC injector.
- `dwell_ms` (int, optional) — dwell time at the target before click for
  `via: "pointer"`; passed through to the VNC injector.

#### Choosing a click mode

- `pointer` (default): real cursor motion + pre-click verify via
  `get_accessible_at_point` + real button click. Cursor visible in the
  session recording; exercises the real input event chain. Use for final
  test runs and PR-gating scenarios. Widget must expose Component
  extents (most standard widgets do).
- `action`: semantic `do_action(0)` invocation; no cursor motion.
  Fast and reliable but the recording shows no interaction. Use for:
    * smoke runs where speed matters more than the visual artifact
    * pre-flight / validation scaffolding inside a larger scenario
    * widgets that lack Component extents (rare — typically offscreen)
    * widgets whose click handler isn't tied to pointer events

Example pair:

```yaml
# Final-run style (default — real cursor, visible in recording)
- atspi: {role: "push button", name: "Login"}

# Smoke / validation style (semantic, no cursor)
- atspi: {role: "push button", name: "Login", via: "action"}
```

The pointer-mode verify accepts exact-path match OR
ancestor/descendant subtree match — the cursor landing on a child
label or parent container of the intended widget is still "on" the
widget. One retry fires on mismatch (re-find + re-move + re-verify)
before raising `AtspiError`.

### `verify_atspi:` — semantic existence check

```yaml
- label: "Email field present"
  verify_atspi: {role: "entry", name: "Email or username"}
  retries: 1
```

Same field set as `atspi:` (no `action_idx`). The step passes if a matching
node exists in the tree at the moment of the check; fails otherwise. Use
`retries:` for inherently racy checks — the surrounding retry loop re-polls
with `time.sleep` between attempts.

### `wait_for:` — poll-on-VM wait

```yaml
- label: "wait_for frame to appear"
  wait_for:
    any:
      - {role: "frame", name: "rocket", name_substr: true}
    timeout: 30
    interval: 0.5
  retries: 1
```

Fields:

- `any:` (list of conditions) — OR semantics. First condition to fire wins.
- `all:` (list of conditions, alternative) — AND semantics. All must fire
  before the step exits.
- `timeout` (int seconds, default `15`) — total wait budget.
- `interval` (float seconds, default `0.5`) — poll interval on the VM.
- Each condition is a `{role, name, name_substr}` dict — the same shape used
  by `atspi:` find.

**Current limitation**: the worker only implements AT-SPI conditions.
`{window: "..."}` and `{shell: "..."}` were specced but not implemented yet.
Future extension — no schema change needed since the dict accepts arbitrary
keys, they will simply be ignored by today's worker.

## Fallback chain — `atspi → coords → VLM`

If a step has both `atspi:` AND `localize:` set, AT-SPI is tried first. On
`AtspiError` (widget not found, action failed, SSH error), the runner falls
through to VLM `localize`:

```yaml
- label: "click Send (may be canvas in some builds)"
  atspi: {role: "push button", name: "Send"}
  localize: "the Send arrow button at the right edge of the message composer"
  retries: 3
```

Use this for widgets that **might** be canvas-rendered depending on RC version
or feature flag. If only `atspi:` is set, an `AtspiError` fails the step
immediately (no VLM fallback).

## `--record-window-state` flag

Opt-in flag for `mosdat functional` / `mosdat confirm`. When set, the
`SessionRecorder` runs a low-frequency background thread (~5 Hz) on the
runner that polls the VM via SSH for window and cursor state, and bundles
the results into every frame entry of `index.jsonl`.

Extra per-frame keys when enabled:

- `active_window` — output of `xdotool getactivewindow getwindowname`
- `open_windows` — list parsed from `wmctrl -l`
- `cursor_x`, `cursor_y` — from `xdotool getmouselocation --shell`
- `timestamp_ns` — high-resolution capture timestamp

Notes:

- Default OFF — opt-in for failure debugging only.
- Old `index.jsonl` readers ignore unknown keys; the schema change is
  backward-compatible.
- Sampler self-disables on first failure (missing wmctrl/xdotool, no DISPLAY).
  Recording continues; metadata is simply absent. See KNOWN_ISSUES.md item #5.
- DISPLAY=:0 and the mutter XAUTHORITY are auto-injected by the sampler.

```bash
mosdat functional examples/rocketchat.toml --vms ubuntu2204 \
    --test rocketchat-smoke-linux --record-window-state
```

## Known canvas regions — keep VLM here

The following Rocket.Chat regions render to canvas / custom Web Components
and are NOT reachable through the a11y tree. Use `localize:` (VLM) for them.

- Chat message list (virtual-scroll Web Component)
- Emoji picker
- Video call panel
- Rich-text editor surface (composer)
- Drag-and-drop file-upload zone overlay

This list grows from experience. When you discover a new opaque region,
append it here and to KNOWN_ISSUES.md item #2.

## Performance notes

- **SSH per-call**: ~400 ms first call (cold SSH handshake + worker SCP),
  ~17 ms on subsequent calls. ControlMaster multiplexes the SSH socket and
  is auto-enabled (`persistent=True`) for the AT-SPI client — see
  `automation/commands/functional_cmd.py`. Multiplexing benefit is measured
  on local LAN; over VPN expect 50–150 ms per call.
- **Tree walk**: `tree_dump max_depth=30` takes ~150 ms inside the worker.
  Lower `--max-depth` on hot paths if you want to trim further.
- **Live measurement**: 10 sequential AT-SPI find calls on RC menu items
  ran at 87–115 ms each — see `findings.md` "Stage 2+3 live verify" Test 4.

## Live-verified examples

| Scenario | Demonstrates |
|---|---|
| `shared/scenarios/functional/_smoke-atspi.yaml` | basic `atspi:` + `verify_atspi:` |
| `shared/scenarios/functional/_smoke-stage2-waitfor.yaml` | `wait_for:` replaces fixed `wait:` |
| `shared/scenarios/functional/_smoke-stage3-controlmaster.yaml` | 10 sequential AT-SPI finds, ControlMaster |

For platform limitations and constraints, see
[KNOWN_ISSUES.md](KNOWN_ISSUES.md) (entries on `launch:` routine bypass,
canvas opacity, shallow `_resolve_in_dict`, `name_substr` semantics, and the
window-state sampler DISPLAY requirement).
