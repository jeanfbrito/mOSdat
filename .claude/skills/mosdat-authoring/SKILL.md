---
name: mosdat-authoring
description: "Use when authoring mOSdat scenarios for a new feature. Walks the routines-first workflow: identify atomic interactions, reuse or create routines, compose scenario. Triggers on /mosdat-authoring, 'write tests for X in mOSdat', 'author scenario for X', 'add mOSdat coverage for PR #N'."
---

# /mosdat-authoring — Routines-First Scenario Authoring

Triggers: `/mosdat-authoring`, "write tests for X in mOSdat", "author scenario for X", "add mOSdat coverage for PR #N"

## 1. When to Invoke

Use this skill when:

- A PR has been implemented and needs functional test coverage in mOSdat.
- You are asked to "write tests for X", "add mOSdat coverage", or "author a scenario for PR #N".
- A new feature surface is untested and you need to build coverage from scratch.
- You want to add a new routine to `shared/routines/` and integrate it into a scenario.

Do NOT use this skill for:

- Adding a single `verify:` step to an existing passing scenario.
- Debugging a failing scenario (use `/mosdat-insight` or `mosdat recipes` instead).
- Editing scenario infrastructure, fixtures, or runner code.

The defining test: is this NEW feature coverage being authored from zero? If yes, invoke this skill.

---

## 0. Discovery — what's already in the library

Before writing anything, learn the library.

```bash
mosdat routines list                # full inventory + tags
mosdat routines show <name>         # inspect any routine's schema, inputs, fallbacks
mosdat routines explain <name>      # dry-run preview — shows which fallback fires
mosdat routines report --format md  # coverage map — which scenarios use which routines
mosdat routines fixtures            # available VM-state fixtures
```

Then for every atomic interaction your feature needs, run:

```bash
mosdat routines list | grep -i <keyword>
```

Try keywords: feature area (telephony, settings, login, modal), UI surface (dialog, dropdown, sidebar), side-effect category (dispatch, toggle, select).

---

## 2. Pre-Flight Checks

Run these before touching any file. They prevent authoring against a stale binary or a broken VM.

```bash
# I3 — get a clean build of the PR binary, deploy to VM, verify the feature symbol is present
mosdat build --pr <N> --deploy <vm> --verify-symbol <feature>

# I2 — confirm the existing baseline still passes before you change anything
mosdat preflight <toml> --vms <vm> --test <existing-stable-scenario>

# I13 — VM health: SSH, VNC, X11 surface, display reachable
mosdat doctor <toml> --vms <vm>

# F2 — check for known platform constraints related to this feature area before you design steps
mosdat recipes search "<feature keyword>"

# F1 — refresh the capability manifest (accelerator probes, popup behavior)
mosdat trace <toml> --vms <vm>
```

Stop if `mosdat build` or `mosdat preflight` returns non-zero. Do not author against a broken baseline.

---

## 3. Routines-First Workflow

Do not start by writing a scenario. Start by listing atomic interactions.

### Step 1 — List atomic UI interactions

Write down every discrete UI action the feature needs. Examples:

- Kill the app and wipe user data
- Launch with a specific config pre-staged
- Dispatch a `tel:` deeplink
- Open Settings panel
- Toggle a switch
- Dismiss a modal
- Verify app is still alive

### Step 2 — Find existing routines

```bash
mosdat routines list
mosdat routines show <name>
```

For each atomic interaction, check whether an existing routine covers it. If the match is close but not exact, prefer reusing with an input parameter over writing a new routine.

For EACH atomic interaction, exhaustively check the existing library before authoring. Procedure:

1. `mosdat routines list` — scan all 9+ routines by name + tags.
2. If a name kinda matches → `mosdat routines show <candidate>` and READ THE FULL spec (description, inputs, postcondition). Decide: reuse / extend with new input / fork into new routine.
3. If extending: prefer ADD-input over fork (preserves reuse). Document the new input in the routine's `inputs:` block with default that preserves existing callers' behavior.
4. Only fork into a NEW routine if the postcondition guarantee differs.

### Step 3 — Author new routines (if needed)

Create `shared/routines/<slug>.yaml`. Schema reference: `docs/routines.md`.

### Step 4 (pre-YAML) — Pick a sibling as template

Before writing any YAML, pick a sibling routine in the same family (same tag) as a template. Open it:

```bash
mosdat routines show <sibling-name>
```

Copy its structure (description shape, input declarations, pre/postcondition wording). Customize for the new interaction. Examples by family:

- Telephony UI mutation: template = `enable-telephony-toggle`
- Modal interaction: template = `select-telephony-server-from-modal`
- Settings nav: template = `open-settings` (with capability fallback)
- Lifecycle: template = `cleanup-rocketchat` / `launch-rocketchat`
- Health check: template = `verify-app-alive`
- Dispatch: template = `dispatch-tel-link`

Minimal structure:

```yaml
name: <kebab-case-slug>
description: <one sentence — what it does>
schema_version: v1
tags: [<feature-area>, <ui-surface>, <side-effect-category>]

inputs:
  - name: <param>
    type: string
    required: true

preconditions:
  - verify: "<expected pre-state before main steps>"

steps:
  - shell: ...

postconditions:
  - verify: "<guaranteed end-state>"

on_failure:
  - shell: |
      DISPLAY=:0 scrot /tmp/routine-failure.png
  - shell: journalctl --user -n 50 --no-pager

fallbacks:
  - when: "capability.accelerators['<key>'] == 'swallowed'"
    steps:
      - shell: <alternate input method>
```

### Step 5 — Test each routine in isolation

```bash
mosdat routines test <name> --vms <vm> --fixture <fixture> --config <toml>
```

List available fixtures:

```bash
mosdat routines fixtures
```

Iterate until exit code 0. A routine that fails in isolation will fail in every scenario that uses it. Fix here, not in the scenario.

Override a specific input for a test run:

```bash
mosdat routines test <name> --vms <vm> --fixture <fixture> --with <key>=<value>
```

### Step 6 — Compose the scenario

Stitch `- routine: <name>` calls together and add feature-specific `verify:` steps.

Canonical worked example: `shared/scenarios/functional/3325-master-toggle.yaml` (76 lines, 4 routine calls).

```yaml
name: "<App> — <Feature description> (PR #N)"
vars:
  workspace_url: "https://..."

phases:
  - id: A
    name: "baseline: feature off"
    from_step: 1
  - id: B
    name: "feature on"
    from_step: <N>

steps:
  - routine: cleanup-rocketchat
  - routine:
      name: launch-rocketchat
      with:
        servers: [...]
        <feature_flag>: false

  - routine:
      name: <action-routine>
      with: { <param>: <value> }
  - verify: "<expected negative result>"
    verify_timeout: 15
    retries: 3

  - routine: cleanup-rocketchat
  - routine:
      name: launch-rocketchat
      with:
        servers: [...]
        <feature_flag>: true

  - routine:
      name: <action-routine>
      with: { <param>: <value> }
  - verify: "<expected positive result>"
    verify_timeout: 20
    retries: 5

  - key: Escape
  - wait: 2
  - routine: verify-app-alive
  - routine: cleanup-rocketchat
```

### Step 7 — Validate scenario schema

```bash
python3 -c "
from pathlib import Path
from automation.runners.scenario_loader import load_test_yaml
load_test_yaml(Path('shared/scenarios/functional/<scenario-name>.yaml'))
"
```

Fix any `ValidationError` before running.

### Step 8 — Run the scenario

```bash
mosdat functional <toml> --vms <vm> --test <scenario-name> --save-screenshots
```

Resume from a specific step or phase after a failure:

```bash
mosdat functional <toml> --vms <vm> --test <scenario-name> --from-step <N>
mosdat functional <toml> --vms <vm> --test <scenario-name> --from-phase <phase-id>
```

### Step 9 — On failure

1. Check screenshots in `results/functional/<run-timestamp>/<vm>/`.
2. Use `mosdat replay <result-dir>` (I5) to iterate on verify prompts without re-running the full scenario.
3. Run `mosdat recipes search "<symptom keyword>"` (F2) — check if the failure matches a known platform constraint.
4. After 2 failed attempts on the same step, dispatch `/mosdat-insight` to surface relevant lessons. Do not try a third approach before consulting it.

---

## 4. Routine Authoring Checklist

Before saving a new routine, verify:

- [ ] `name` is kebab-case and matches what the routine does
- [ ] `inputs` declared with `RoutineInput` fields: name / type / required / default
- [ ] `preconditions` contain only `verify:` steps asserting the required pre-state
- [ ] `postconditions` contain only `verify:` steps asserting the GUARANTEED end-state
- [ ] `fallbacks` use `when:` jinja2 expressions consuming `capability.*` or `vars.*`
- [ ] `on_failure` has at least one diagnostic step (screenshot capture, log dump)
- [ ] `description` is one sentence stating what the routine does
- [ ] The description implicitly declares: what state it guarantees on exit AND what it does NOT cover
- [ ] `tags` include: feature area, UI surface, side-effect category
- [ ] `schema_version: v1` present (or omitted — defaults to v1)
- [ ] **Reuses an existing pattern**: name follows existing convention (kebab-case verb-noun), structure mirrors a sibling routine in the same tag family, description shape matches.
- [ ] **Has at least 1 plausible second caller**: if you can't name a future scenario that would use it, reconsider whether it should be a routine vs inline.

---

## 5. Scenario Authoring Checklist

**Keep inline** (do not routinize):

- Feature-specific `verify:` assertions that will not recur in other tests
- One-off shell setup steps unique to this scenario
- Modal dismissal with `key: Escape` when it is a single step

**Move to a routine** (routinize):

- Any sequence used in more than one scenario
- Complex UI navigation (multi-click, window activation, multi-step form)
- State pre-staging that involves multiple config writes or launch parameters
- Any step sequence where a mid-step failure would be ambiguous without its own diagnostics

Decision heuristic: if you would have to copy-paste this block into the next scenario, it is a routine.

---

## 6. Anti-Patterns

**Inline 200-line scenario** — A scenario with 40+ inline shell blocks is authoring debt. Each block is a point of silent failure. Split into routines. See `3325-master-toggle.yaml`: 236 lines → 76 after routines-first.

**Hard-coded pixel coordinates** — `xdotool mousemove 300 280` breaks when resolution or layout changes. Use `localize: "<element description>"` with `click: true` for dynamic elements. Use coordinates only for stable, permanently-positioned UI chrome.

**Settings nav for persisted state** — Do not click through Settings to set a value that lives in `config.json`. Use `--inject-config` (I1) or the `launch-rocketchat` routine's input parameters. Settings nav on Linux Electron is a known failure surface: `Ctrl+,` has no accelerator, sidebar kebab is transient, `alt+w` is swallowed by the webview. See `docs/skills/state-first-testing.md` §3.

**Skipping routine isolation tests** — Running the full scenario first and debugging composite failures wastes cycles. Test each new routine independently via `mosdat routines test` before composing.

**VLM prompts naming exact values** — `verify: "version number v6.8.0 visible"` will break on the next release. Describe the state type: `verify: "version information shown in the status area"`.

**Forgetting to grep the library** — every new feature inherits ALL existing routines. Skipping `mosdat routines list` means you'll re-implement someone else's debugged work. Always run the discovery commands in section 0 first.

**One-off "this is special" mindset** — if you tell yourself "this interaction is unique, no point making it reusable," you're almost always wrong. Future features rhyme; routines compound.

---

## 7. Definition of Done

Before declaring authoring complete, all of the following must be true:

- [ ] `mosdat lint <scenario>` (F1a) returns no warnings or errors
- [ ] `mosdat routines test <each-new-routine>` exits 0 on the target VM
- [ ] `mosdat routines report` shows every new routine with test history (not `untested`)
- [ ] `mosdat functional <toml> --vms <vm> --test <scenario-name>` passes end-to-end
- [ ] Help-drift snapshot still passes (no unintended CLI surface changes)
- [ ] New routines appear in `mosdat routines report --format md` output
- [ ] No `# TODO:` markers remain in the scenario YAML

---

## 9. Reusability gate — stop and routine-ize

When writing your scenario, if you find yourself adding:

- More than 2 inline shell steps that name a UI element
- A localize+click+verify trio that could plausibly be used elsewhere
- A pre-condition / post-condition that any reasonable feature could need

STOP. Routine-ize it. Procedure:

1. Pause scenario authoring.
2. Author the new routine in `shared/routines/<slug>.yaml` using a sibling as template (step 4 above).
3. `mosdat routines test <slug>` against a fixture until green.
4. Return to scenario; replace the inline steps with `- routine: <slug>`.
5. Update `docs/routines.md` if the routine introduces a new family.

Rule of thumb: if you can describe the interaction in one English sentence ("open settings", "dismiss modal", "type credentials into login form"), it's probably a routine. If it takes a paragraph, it's scenario logic.

---

## 8. References

- `docs/routines.md` — full routine schema, RoutineInput fields, fallback syntax, fixture reference, CLI
- `docs/AUTO-AUTHORING.md` — routines-first workflow, change-type classification, `mosdat draft` scaffold
- `docs/skills/state-first-testing.md` — when to use `--inject-config` vs UI navigation
- `CHANGELOG.md` — full feature surface: I1-I15, F1-F5, R1-R7
- `shared/scenarios/functional/3325-master-toggle.yaml` — canonical worked example (76 lines)
- `shared/routines/` — existing routine library (reuse before authoring)
- `shared/recipes/` — platform constraint corpus (`mosdat recipes search`)
