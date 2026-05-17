# Auto-Authoring: Generating Functional Test Scenarios from Code Changes

**For AI agents.** This document describes the workflow for generating mOSdat functional test scenario YAMLs from implementation changes — either after coding a feature or when reviewing a PR that needs test coverage.

**TL;DR:** Classify the change type → `mosdat draft` → fill TODOs → run → iterate.

---

## When to Use This

| Scenario | Who runs it | Example |
|----------|------------|---------|
| After Claude Code/agent implements a feature | The implementation agent | "I just added a telephony deeplink handler" → generate test that exercises it |
| PR arrives and needs test coverage | The reviewer agent | "PR #3410 changes proto handler registration" → generate cross-OS test |
| Bug fix verified, need regression test | Any agent | "Fixed crash on empty server list" → generate test that would have caught it |
| Existing feature needs coverage | Any agent | "We never tested the settings persistence" → generate from existing code |

---

## Step 1: Understand What Changed

Before generating anything, read the source. **Every agent must do this first.**

```bash
# If it's a PR
gh pr diff <pr-number>
gh pr view <pr-number> --json title,body,files

# If it's local work-in-progress
git diff <branch>..<target>

# Focus on:
# - New React components, HTML elements, dialogs, modals
# - New IPC handlers, preload scripts, electron-store writes
# - Protocol handler changes (app.setAsDefaultProtocolClient, xdg-open, tel:/callto:)
# - Global shortcuts, keyboard accelerators
# - New settings keys, preferences toggles
# - .desktop file, MimeType, autostart changes
```

Also read **UI text from source** — never guess labels:

```bash
# i18n keys
grep '"telephony\|"preferred_server" app/i18n/en.i18n.json
# Or any en.i18n.json in the project

# aria-labels and test IDs
grep -rn 'aria-label\|data-testid' app/<changed-files>

# React component text
grep -rn '>\w+' app/<changed-files>.tsx | grep -E '>[A-Z]'
```

---

## Step 2: Classify the Change Type

Read the diff and match it to one of these types:

| Change type | What the diff looks like | Detection hints |
|---|---|---|
| `ui` | New JSX, buttons, modals, dialogs, React components | `createElement`, `useState`, new `.tsx` files, conditional rendering |
| `persistence` | `electron-store.set()`, `config.json`, Redux selectors with localStorage | Store writes, config reads, state that survives app restart |
| `protocol_handler` | `app.setAsDefaultProtocolClient`, `xdg-open`, protocol URL handling | `tel:`, `callto:`, `customprotocol`, `open-url` event |
| `keyboard_shortcut` | `globalShortcut.register`, `Menu` accelerators, `keydown`, `keypress` listeners | `accelerator:`, `CmdOrCtrl+`, `globalShortcut` |
| `settings` | UseSetting hook, setPreference, Preferences component changes | Settings UI controls, dropdowns, toggles, sliders |
| `bug_fix` | Targeted fix (usually small diff), check for null/undefined, race condition | Conditional check added, try/catch around crash site, state guard |
| `de` | .desktop file, MimeType, `x-scheme-handler`, XDG desktop entries | `.desktop` file changes, postinst scripts, `MimeType=` lines |
| `autostart` | Autostart desktop file, `X-GNOME-Autostart`, systemd user service | `autostart` directory, systemd `.service` or desktop autostart entries |

**Mixed changes:** If a PR touches both UI and persistence (e.g., a settings toggle that saves to disk), generate **one scenario** of type `settings` (which covers both phases). Don't split into two.

---

## Step 3: Generate the Base YAML

```bash
# Simple case — just a PR number and short description
mosdat draft --change-type ui --pr 3410 --description "new telephony dropdown"

# With explicit output path (recommended)
mosdat draft --change-type protocol_handler --pr 3410 \
  --description "tel: and callto: deeplink support" \
  --output shared/scenarios/functional/3410-telephony-deeplink.yaml

# No PR number yet — use description as identifier
mosdat draft --change-type persistence \
  --description "remember telephony server choice" \
  --output shared/scenarios/functional/remember-telephony-choice.yaml
```

This produces a scaffold with TODOs marking where you need to insert specific details.

> **Schema examples for advanced scenario features:**
> - `vars:` + `{{ key }}` substitution — see `shared/scenarios/functional/example-vars.yaml`
> - `accept_any:` union step (succeeds on first VLM yes) — see `shared/scenarios/functional/example-accept-any.yaml`
> - `imports:` + reusable step fragments — see `shared/scenarios/imports/` (ships `cleanup-rocketchat`, `install-x11-deps`, `launch-rocketchat`)

---

## Step 4: Customize the Generated YAML

`mosdat draft` produces a **template**, not a finished scenario. You must customize it:

### 4a. Replace TODO markers

Every generated YAML has `# TODO:` comments. These are your checklist:

| TODO | What to put there |
|------|-------------------|
| `TODO: update coordinates for current screen resolution` | Use `mosdat author click` or `xdotool getmouselocation` to find coordinates, or replace with a `localize` + `click` step |
| `TODO: set correct key combo` | Replace `ctrl+comma` with the actual shortcut from source code |
| `TODO: login and navigate to feature` | Add type/key steps for login (email + password) and then click to the feature being tested |
| `TODO: navigate to specific setting, toggle/change it` | Read the settings component to know what to click |
| `TODO: describe the reproduction steps that the fix addresses` | Translate the bug report or PR description into concrete steps |

### 4b. Write VLM verify prompts

Replace the generic verify prompts with specific ones based on the actual UI. See [FUNCTIONAL-TESTS-LINUX.md](FUNCTIONAL-TESTS-LINUX.md) for VLM prompt style guidelines.

**Rules:**
- Describe the **type** of UI state, not exact implementation details
- For login page: "The application shows a login page with email/username and password fields"
- For a modal: "A dialog or modal window appears on top of the main application"
- For no-crash: "The application window is still visible and responsive — no crash dialog, no frozen window"
- Use `verify_timeout: 20` for initial app load (VNC is slow)
- Use `retries: 5` for transient modal/dialog appearances

### 4c. Set correct coordinates or use localize

The templates use `xdotool mousemove X Y click 1` as placeholders. Replace with either:

**Option A — known coordinates** (for stable UI elements at fixed positions):
```yaml
- shell: |
    export DISPLAY=:0
    xdotool mousemove 300 280 click 1
```

**Option B — VLM localize** (for dynamic content):
```yaml
- localize: "the 'Settings' button or gear icon"
  click: true
```

**Option C — keyboard navigation** (preferred when possible):
```yaml
- key: Tab
- key: Tab
- key: Enter
```

---

## Step 5: Run and Iterate

```bash
# First run (expect failures — debug the scenario, not the app)
mosdat functional examples/rocketchat.toml --vms ubuntu2404 \
  --test 3410-telephony-deeplink --save-screenshots

# If step N fails, re-run from that step (avoids re-doing setup)
mosdat functional examples/rocketchat.toml --vms ubuntu2404 \
  --test 3410-telephony-deeplink --from-step 5

# Or jump to a named phase (see top-level `phases:` in your scenario YAML)
mosdat functional examples/rocketchat.toml --vms ubuntu2404 \
  --test 3410-telephony-deeplink --from-phase setup
```

**Debugging failures:**
1. Check the screenshots in `results/functional/<run-timestamp>/ubuntu2404/`
2. Did the VLM see what you expected? If yes but it answered "no" → make prompt simpler
3. Did the app actually reach that state? If no → fixed timing or click coordinates
4. Use `--save-screenshots` on the first run or when debugging
5. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common functional test failures

---

## Step 6: Finalize

When the scenario passes on the primary VM:

1. **Consider cross-OS differences** — Wayland vs X11 affects xdotool/VNC behavior
2. **Remove `--save-screenshots`** before committing (keeps results/ clean)
3. **Update the test_notes** in the task body for future reference
4. **Commit the scenario** to `shared/scenarios/functional/`

---

## Architecture: How Draft Templates Work

```
mosdat draft --change-type <type>
       │
       ▼
  automation/draft.py
       │
       ├── embedded TEMPLATES dict (8 types, hardcoded)
       └── shared/scenarios/templates/<type>.yaml  (disk override if exists)
              │
              ▼
         scafold YAML with TODOs
```

- Embedded templates ship with mOSdat — always available
- Disk templates at `shared/scenarios/templates/*.yaml` override embedded — customize without touching Python
- Run `mosdat draft --templates` to write all embedded templates to disk (e.g., after updating mOSdat)
- Templates use Python `.format()` with `{pr_or_desc}` and `{description}` variables

---

## Adding a New Change Type

When a new feature category emerges (e.g., "system-tray" or "notifications"):

1. Add the template string to `TEMPLATES` dict in `automation/draft.py`
2. Add the type name to the `--change-type` choices in both `automation/draft.py` and `automation/main.py`
3. Optionally write to disk with `mosdat draft --templates`
4. Update this document's classification table (Step 2)

---

## Pitfalls

### ❌ Generating without reading the code first

`mosdat draft` gives you a scaffold. If you don't know what the code changed, you can't fill in the TODOs. **Always read the diff first.**

### ❌ Leaving TODO markers in the final YAML

Every `# TODO:` is a missing piece. The scenario won't test what you think it tests until all TODOs are resolved.

### ❌ Using generic verify prompts on a persistence test

Phase 2 of a persistence test is the critical one. "The app is visible" is not enough. You need: "The previous setting/preference is still active — no modal/prompt asking to re-configure."

### ❌ Not accounting for Wayland vs X11

`xdotool` works differently (or not at all) on Wayland for some operations. Always test on both display servers.

### ❌ Forgetting cleanup steps

Always start with `pkill` + `rm -rf "$HOME/.config/Rocket.Chat" "$HOME/.config/Rocket.Chat (development)"`. Stale state from a previous run causes false positives.

### ❌ VLM prompt too brittle

```yaml
# WRONG — version-specific, will break next release
- verify: "The version number 'v6.8.0' in the bottom-left corner"

# RIGHT — describes the state, not the exact value
- verify: "The application shows version information in a status bar or footer area"
```

---

## Full Agent Workflow Example

```
Agent sees: "PR #3410 adds telephony deeplink support (tel:/callto:)"

1. gh pr diff 3410 → reads protocol handler registration + xdg-open changes
2. grep 'en.i18n.json' telephony → finds "Preferred Telephony Server"
3. Classifies: protocol_handler
4. Runs:
   mosdat draft --change-type protocol_handler --pr 3410 \
     --description "tel: and callto: deeplink support" \
     --output shared/scenarios/functional/3410-telephony-deeplink.yaml
5. Opens the YAML, replaces:

   # TODO: update coordinates for current screen resolution
   → xdotool mousemove 250 300 click 1  (verified via author-capture)

   # TODO: describe what the deeplink should trigger
   → verify: "A telephony server selection modal appears, listing available
     telephony providers. No crash dialog is visible."

6. Runs: mosdat functional ... --test 3410-telephony-deeplink --save-screenshots
7. Screenshots show: app crashes at deeplink → bug in implementation, not test
8. Fixes bug, re-runs, passes
9. Commits scenario YAML
```

---

## See Also

- [FUNCTIONAL-TESTS-LINUX.md](FUNCTIONAL-TESTS-LINUX.md) — VNC/VLM desktop-driving model, prompt style guide, Wayland constraints
- [docs/runbooks/live-dashboard.md](runbooks/live-dashboard.md) — Author Workbench for interactive scenario creation
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Common functional test failures
- `mosdat-hermes` skill (Hermes agents) — MCP tools, scenario schema, pitfall guide
