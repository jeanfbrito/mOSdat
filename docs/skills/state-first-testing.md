# State-First Testing

*Guidance for mOSdat scenario authors.*

---

## 1. Principle

Persisted state can be written directly to `config.json` before the app launches.
UI navigation to set that same state — clicking through Settings panels, dropdowns,
and save buttons — is brittle, slow, and tests the wrong thing.
Prefer **pre-stage**: inject the desired state via `--inject-config`, launch the app,
then verify the behavioral side effect.
Reserve UI navigation only for state that genuinely cannot be pre-staged.

---

## 2. Decision Checklist

Before designing a UI sequence to set a value, run through these four questions:

- [ ] Is the target value persisted to `userData/config.json` on app exit?
- [ ] Is the value's key in `PersistableValues` (or your app's Redux persist schema)?
- [ ] Can you set it via `mosdat functional --inject-config` (I1)?
- [ ] Is the side effect testable **without** first navigating Settings UI?

If **yes to all four** → pre-stage. Skip the Settings navigation entirely.

If **no to any** → UI navigation is unavoidable for this value. See §5.

---

## 3. Anti-Patterns

These patterns look correct but create fragile, slow tests. Avoid them when the value
is persisted.

### 3a. Toggle via Settings UI click

```yaml
# BAD — tests the toggle click, not the feature behavior
- shell: |
    xdotool key alt+F1   # open app menu
- verify: "app menu open"
- localize: "Settings menu item"
  then_click: true
- verify: "Settings panel open with General tab visible"
- localize: "Enable Telephony toggle"
  then_click: true
- verify: "Enable Telephony toggle is now ON"
```

Why it fails:
- RC Electron has no `Ctrl+,` accelerator on Linux; keyboard shortcut nav is dead.
- Sidebar kebab popup is transient; VLM hallucinates click coordinates.
- `alt+w` is swallowed by the webview when a login form holds focus.
- Every approach to open Settings adds 3–6 flaky steps before the real test begins.

### 3b. Dropdown + save round-trip

```yaml
# BAD — tests dropdown interaction, not persistence
- localize: "Telephony server dropdown"
  then_click: true
- verify: "server selection dropdown open"
- localize: "Workspace option in dropdown"
  then_click: true
- verify: "Workspace selected in dropdown"
# ... save button, close settings, relaunch ...
```

Why it fails: the real feature under test is whether the saved value affects
subsequent behavior. The dropdown interaction is noise. Any transient UI state
(popup closing, focus shift) can cause the localize to miss.

---

## 4. Correct Patterns

The same tests, rewritten with pre-staging.

### 4a. Toggle via inject-config

```yaml
# GOOD — pre-stage state, verify behavior
# CLI: mosdat functional config.toml --vms ubuntu2204 --test my-test \
#       --inject-config '{"isTelephonyEnabled": true}'
steps:
  - import: cleanup-rocketchat
  - import: launch-rocketchat
  - shell: |
      XAUTH=$(ls /run/user/$(id -u)/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
      export DISPLAY=:0 XAUTHORITY="$XAUTH"
      xdotool type "tel:+15551234567" && xdotool key Return
  - verify: "telephony call modal visible with number +15551234567"
    verify_timeout: 15
```

The telephony state is injected before launch. The test verifies the behavioral
consequence (modal appears), not the toggle click.

### 4b. Server selection via inject-config

```yaml
# GOOD — inject preferred server, verify routing behavior
# CLI: --inject-config '{"telephonyPreferredServer": "https://rocketchat.example.com"}'
steps:
  - import: cleanup-rocketchat
  - import: launch-rocketchat
  - shell: |
      DISPLAY=:0 xdotool type "tel:+15551234567"
      xdotool key Return
  - verify: "call dispatched directly to rocketchat.example.com without server-select modal"
    verify_timeout: 15
```

No dropdown interaction. No save button. No fragile modal dismissal. The correct
server is already in `config.json` when the app starts.

---

## 5. When UI Navigation IS Correct

Use UI navigation (VLM `localize`, `xdotool`, `key`) for state that **cannot** be
pre-staged:

| Scenario | Why UI nav is correct |
|---|---|
| In-flight state: dial pad open | Not persisted; no config.json key |
| Modal confirmation dialogs | Triggered by runtime action, not config |
| "Remember this choice" checkbox | Per-session UI interaction, checked mid-flow |
| Animations / focus rings | Visual-only; not written to disk |
| Non-Electron app UI (browser, OS dialogs) | No Redux persist layer to write to |
| First-launch server-add flow | App requires the UI path; no servers.json bypass available |

A useful heuristic: if you can find the key in `PersistableValues` in the source,
it's persisted. If you cannot, UI nav is probably required.

For UI nav that IS unavoidable (modals, dial pad, animations), prefer a tested ROUTINE over inline steps. See `docs/AUTO-AUTHORING.md` § Routines-first authoring.

---

## 6. Worked Example — PR3325 master-toggle.yaml

### Before (Settings UI navigation — fragile)

```yaml
# Phase A: enable telephony via Settings
# 1488 total lines, 0/5 green across 6 rerun cycles
steps:
  - shell: |
      DISPLAY=:0 xdotool key ctrl+comma
  - verify: "Settings panel open"
    verify_timeout: 10
  - localize: "Enable Telephony toggle"
    then_click: true
  - verify: "Enable Telephony toggle is ON"
    verify_timeout: 10
  # ... 12 more steps of modal nav before the actual feature test ...
```

Failure modes hit in practice (2026-05-16 PR3325 marathon):
- `Ctrl+,` has no `accelerator:` field in `menuBar.ts` on Linux → keypress silently ignored.
- Sidebar kebab popup appeared then vanished before VLM localize responded.
- `alt+w` consumed by webview when login form was focused.

### After (inject-config — stable)

```yaml
# Phase A: inject state, verify behavior directly
# 695 total lines, 5/5 green first run
# mosdat functional ... --inject-config '{"isTelephonyEnabled": true, "telephonyPreferredServer": null}'
steps:
  - import: cleanup-rocketchat
  - import: launch-rocketchat
  # A3: Dispatch tel: URI → expect server-select modal
  - shell: |
      XAUTH=$(ls /run/user/$(id -u)/.mutter-Xwaylandauth.* 2>/dev/null | head -1)
      export DISPLAY=:0 XAUTHORITY="$XAUTH"
      xdg-open "tel:+15551234567" 2>/dev/null || true
  - verify: "modal labeled 'Select Server' with two server rows visible"
    verify_timeout: 15
    retries: 5
```

Diff summary:
- Removed: 3 flaky Settings-nav steps per phase (×5 scenarios = 15 steps removed).
- Added: `--inject-config` flag on the CLI call (1 line).
- Net: −793 YAML lines across the PR3325 scenario suite.

---

## 7. References

- [`docs/IMPROVEMENTS.md` — I1 `--inject-config`](../IMPROVEMENTS.md#i1---inject-config-flag-mosdat-functional--implemented-2026-05-16)
- [`.knowledge/mosdat/scenarios/pr3325-bypass-settings-ui-via-config-prestage.md`](../../.knowledge/mosdat/scenarios/pr3325-bypass-settings-ui-via-config-prestage.md)
- [`shared/scenarios/functional/3325-master-toggle.yaml`](../../shared/scenarios/functional/3325-master-toggle.yaml)
