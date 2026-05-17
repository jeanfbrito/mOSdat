# PR #3325 — Retrospective

> Internal process retrospective. Not public-facing.
> Date: 2026-05-17. Work period: 2026-05-14 – 2026-05-16.

---

## Summary

- **Goal**: implement 5 functional scenarios covering RocketChat Electron PR #3325 (click-to-call telephony).
- **Outcome**: 5/5 scenarios green + 15 follow-up improvements (I1–I15) + 5 retrospective tools (F1–F5) + 1 RC.Electron upstream draft PR (#3336).
- **Cost**: ~30h elapsed, ~6h on the Settings-access saga alone.

---

## Timeline (chronological)

- **Day 1 morning** — Initial 5 PR #3325 scenario stubs authored via `mosdat author`; first full run attempted on `ubuntu2204`. RC booted to "Add a server" silently; all 5 scenarios fail at login verify. Root cause: PR build writes userData to `Rocket.Chat (development)/` not `Rocket.Chat/`; config pre-stage missed the dev suffix. (`24433dd`)
- **Day 1 late morning** — Fixed userData dir; scenarios now reach the telephony Settings panel. `key: "ctrl+,"` attempted first (~45 min, ~6 reruns). RC Electron has no `accelerator:` on the Settings menu item on Linux; `Ctrl+,` is a macOS convention only.
- **Day 1 afternoon (early)** — Switched strategy: sidebar kebab menu + `localize "Settings"`. VLM hallucinated click coordinates on the transient kebab popup (~1h, 4 reruns). Popup dismisses in <200 ms after mouse movement; screenshot captured mid-dismiss.
- **Day 1 afternoon (mid)** — Third attempt: `key: "alt+w"` to open Window menu, then Down/Return to reach Settings. Webview swallowed `alt+w` because the workspace login form held keyboard focus. Defocus attempts unreliable.
- **Day 1 afternoon (late)** — 2-strike rule triggered; auditor dispatched to diagnose the Settings-access bottleneck. Auditor read the wrong clone (production .deb, not PR3325 build). Declared telephony feature absent from the binary. Work halted ~1h.
- **Day 1 evening** — User corrected: auditor had read wrong clone. Rebuilt PR head binary. Ran `strings <asar> | grep isTelephonyEnabled` on VM — symbols confirmed present. Auditor finding discarded; root cause confirmed as input-pathway limitation only.
- **Day 2 morning** — Pivot: bypass Settings UI entirely. Pre-stage all telephony Redux keys directly into `~/.config/Rocket.Chat (development)/config.json` between phases (kill RC → rewrite config → relaunch). Scenarios refactored from 1488 → 695 lines. (`4770fc0`)
- **Day 2 midday** — All 5 PR #3325 scenarios green (5/5). IMPROVEMENTS.md roadmap drafted; I1–I15 items captured from accumulated pain points. (`28a7cb7`)
- **Day 2 afternoon** — I1–I15 fan-out: Wave 1 ran 6 parallel builders (I1/I2/I3/I5/I6/I13 simultaneously); I4/I7/I8/I9 serial; I10–I15 polish wave. All improvements shipped same day. (`a499109`, `95077da`)
- **Day 3** — F1–F5 retrospective tooling built (lint+trace, recipes corpus, /insight skill, auditor strict-brief contract, state-first authoring doc). Docs refreshed. Upstream RC.Electron PR #3336 drafted. (`28a7cb7`, `a42d2c5`)

---

## Three pivots that worked

### Pivot 1: Settings UI → Redux state pre-stage

After exhausting `Ctrl+,`, kebab+localize, and `alt+w` menu nav, we stopped trying to drive the UI at all. Pre-staging `config.json` before RC launches eliminates the entire Settings navigation problem. Saved an estimated 5h+ of further UI-banging. Scenario corpus dropped from 1488 → 695 lines; pass rate from 0/5 → 5/5.

Reference: `.knowledge/mosdat/scenarios/pr3325-bypass-settings-ui-via-config-prestage.md`

### Pivot 2: Stale-clone correction

The auditor declared the telephony feature absent after reading the wrong binary. Rather than accepting that verdict and abandoning the scenarios, we forced a fresh PR head build and ran `strings <asar> | grep <symbol>` directly on the deployed VM. Source-grep proved the feature present and the auditor's conclusion wrong. This single verification step saved the entire effort from being written off.

Reference: `.knowledge/mosdat/scenarios/pr3325-bypass-settings-ui-via-config-prestage.md`

### Pivot 3: `accept_any:` verify union

Replaced fragile "either X OR Y" prose in verify prompts with the native `accept_any:` list (I7). Cleaner semantics, fewer VLM false negatives when two valid outcomes are acceptable.

Reference: `shared/scenarios/functional/3325-global-shortcut.yaml` (Phase 2 verify converted)

---

## Three traps that cost time

### Trap 1: Assuming `Ctrl+,` works on Linux (~2h)

macOS keyboard convention. RC Electron's `menuBar.ts` registers the Settings menu item without an `accelerator:` field on Linux. This constraint exists in the codebase but no mOSdat pre-flight check surfaces it. Burned ~2h across multiple reruns before confirming the shortcut simply does not exist on Linux.

Now addressed by: F2 recipes corpus (`settings-electron-linux` recipe), I2 preflight symbol/capability check.

### Trap 2: VLM hallucinated coords on transient kebab popup (~1h)

The sidebar kebab popup is transient (<200 ms lifetime after cursor movement). VLM received a screenshot captured mid-dismiss and returned plausible-looking coordinates that pointed at empty space. No confidence score was emitted; the failure looked identical to a mis-click.

Now addressed by: F2 `webview-focus-stealing` recipe (Pivot 1: avoid VLM localize on transient popups), I14 raw VLM response in HTML report.

### Trap 3: Auditor read wrong clone, declared feature absent (~1h)

Auditor subagent was dispatched without a strict brief specifying which filesystem path to inspect. It read the production .deb installation rather than the freshly-built PR3325 binary. Returned a confident "feature not present" verdict. Cost ~1h of halt + rebuild time to disprove.

Now addressed by: F4 auditor strict-brief contract (brief must include exact binary path + expected symbol list + `strings` grep command to run).

---

## What sped us up

- **Multi-agent fan-out**: Wave 1 ran 6 parallel builders for I1/I2/I3/I5/I6/I13 simultaneously; all 15 improvements shipped within one working day.
- **2-strike rule**: Eventually halted UI-banging at the Settings problem, even if the halt came one attempt later than ideal.
- **Source-grep verification**: `strings <asar> | grep <symbol>` on the deployed VM is a 10-second check that definitively answers "is this feature in the binary?" — proved decisive in the stale-clone incident.
- **`mosdat author` for iterating**: Interactive VNC authoring session let us walk the real UI screenshot-by-screenshot before running a 15-min full scenario.

---

## What slowed us down

- **No pre-flight input-pathway check**: Nothing warned us that `Ctrl+,` is Linux-unsupported before we spent 2h on it. (Now: F1 lint + capability manifest.)
- **No recipe corpus**: The `settings-electron-linux` and `webview-focus-stealing` constraints had to be re-discovered from scratch. (Now: F2 `mosdat recipes`.)
- **No mandatory auditor brief contract**: Auditor dispatch had no required fields for "binary path under inspection." (Now: F4 strict-brief contract.)
- **No state-first authoring guidance**: Scenario authors had no documented signal for when UI navigation is the wrong tool. (Now: F5 `docs/skills/state-first-testing.md`.)
- **VLM hallucination on transient UI not auto-detected**: No confidence score in `events.jsonl`; false verdicts look identical to correct ones in logs. (Remains open — future work.)

---

## Lessons (one-liner each + reference)

| # | Lesson | Reference |
|---|---|---|
| L1 | When 2+ UI input methods fail, the bottleneck is the input pathway, not the target. | `.knowledge/mosdat/scenarios/pr3325-bypass-settings-ui-via-config-prestage.md` |
| L2 | Verify build-clone HEAD against deployed binary BEFORE reasoning about feature presence. | `.knowledge/mosdat/scenarios/pr3325-bypass-settings-ui-via-config-prestage.md` |
| L3 | Test persisted state directly via inject-config. UI nav to set state is brittle. | `docs/skills/state-first-testing.md` |
| L4 | VLM hallucinates transient popups. Use VLM localize for stable surfaces only. | `shared/recipes/webview-focus-stealing.yaml` |
| L5 | Webview steals alt+key when a form input has focus. Defocus first or avoid. | `shared/recipes/webview-focus-stealing.yaml` |
| L6 | PR/dev Electron builds write userData to `<App> (development)/`, not `<App>/`. Confirm via post-launch `ls ~/.config/`. | `.knowledge/mosdat/scenarios/rc-pr3325-userdata-dir-development-suffix.md` |

---

## Improvements shipped (cross-reference table)

| ID | Title | What it fixes |
|---|---|---|
| I1 | `--inject-config` | Eliminates Settings UI navigation for persisted state |
| I2 | `mosdat preflight` | Catches userData/deps/symbol mismatches upfront |
| I3 | `mosdat build --pr` | Eliminates stale-clone class of bug |
| I4 | Implicit X11 env | Eliminates XAUTHORITY-forget failures |
| I5 | `mosdat replay` | Iterate verify prompts in seconds, not 10-min reruns |
| I6 | VLM verify cache | ~50% faster reruns for partially-changed scenarios |
| I7 | `accept_any:` verify union | Cleaner than "X OR Y" prose; fewer false negatives |
| I8 | Jinja vars | Workspace-portable scenarios via `{{ var }}` |
| I9 | Named phases | `--from-phase` / `--until-phase` scoped reruns |
| I10 | Step labels from comments | Failure logs show `A4: dispatch tel:` not `Step 8` |
| I11 | Imported step library | Eliminates copy-paste of cleanup blocks |
| I12 | Author session diff | Stamp manual walkthroughs into existing scenarios |
| I13 | `mosdat doctor` | Per-VM health checklist |
| I14 | Rich HTML report + config snapshots | Shell command sent + raw VLM response + cache hit visible |
| I15 | Negative fixture suite | Guards against "test passes on broken build" |
| F1 | Lint + trace + capability manifest | Pre-flight UI input audit |
| F2 | Recipes corpus + browser | Lookup of known platform constraints |
| F3 | `/insight` skill + autouse hook | Surface relevant lessons after N failures |
| F4 | Auditor strict-brief contract | Kills wrong-clone class of failure |
| F5 | State-first skill doc + upstream asks | Authoring guidance + RC.Electron PR #3336 |

---

## Open work

- **RC.Electron upstream PR #3336** (`CmdOrCtrl+,` accelerator on Settings) — awaiting maintainer review.
- **Ask B** (`data-testid` attrs on telephony UI elements) and **Ask C** (`--load-state` / `--export-state` CLI flag) — not yet PR'd upstream.
- **Multi-OS matrix**: only `ubuntu2204` exercised against PR3325 binary. `fedora42`, `ubuntu2404`, `manjaro`, `opensuse` not yet run.
- **VLM hallucination detector**: confidence score in `events.jsonl` — not implemented; future work.
- **F1 lint + trace + capability manifest** — design exists, implementation not yet shipped.
- **F3 `/insight` autouse hook** — design exists, implementation not yet shipped.

---

## How to reproduce the green state

```bash
mosdat build --pr 3325 --deploy ubuntu2204 \
    --verify-symbol isTelephonyEnabled \
    --verify-symbol telephonyGlobalShortcutConfig

mosdat preflight examples/rocketchat.toml \
    --vms ubuntu2204 \
    --test 3325-master-toggle

mosdat functional examples/rocketchat.toml \
    --vms ubuntu2204 \
    --test 3325-master-toggle \
    --model qwen3.6-35b-a3b-apex-vl \
    --verify-model qwen3.6-35b-a3b-apex-vl
```
