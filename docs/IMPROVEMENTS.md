# mOSdat Improvements — Roadmap

Captured 2026-05-16 after a 30+ hour PR #3325 test-authoring marathon. Every item maps to a concrete pain point hit ≥3 times during that work. Ordered by repeated-pain ratio.

## Retrospective

Full process retrospective covering the PR #3325 saga: timeline, pivots, traps, lessons, and a cross-reference of all improvements.

→ [`docs/retrospectives/pr3325-saga.md`](retrospectives/pr3325-saga.md)

## Tier 1 — kills entire classes of bug

### I1. `--inject-config` flag (`mosdat functional`) — IMPLEMENTED 2026-05-16

Code: `automation/setup/inject_config.py` (detect_userdata_dirs, detect_app_version, wipe_userdata, write_servers_json, write_config_json, inject orchestrator), `automation/commands/parser.py` (`--inject-config`, `--inject-servers`, `--inject-app-name`, `--inject-install-path`, `--inject-migrations-version`), `automation/main.py` cmd_functional (orchestration call before `runner.run_test`). Tests: `tests/test_inject_config.py` (27 unit tests + 1 opt-in live-VM smoke gated by `MOSDAT_TEST_VM=ubuntu2204`).

Goal: declarative pre-staging of Electron app userData before launch.

```
mosdat functional <toml> --vms <vm> --test <name> \
    --inject-config '{ "isTelephonyEnabled": true, "telephonyPreferredServer": null }' \
    --inject-servers 'Workspace=https://rocketchat.jeanbrito.com,Mobile RC=https://mobile.rocket.chat'
```

Behavior:

1. Wipe `~/.config/<app>` AND `~/.config/<app> (development)` on VM.
2. Auto-detect app name + dev-suffix dir from binary on the VM.
3. Write `servers.json` from `--inject-servers`.
4. Write `config.json` merging `--inject-config` with: `currentView: { url: <first-server-url> }`, `lastSelectedServerUrl`, `__internal__.migrations.version` (looked up from asar / app version).
5. Validate JSON.
6. Run scenario.

Removes ~80 boilerplate lines per scenario + the heredoc/printf/missing-migrations-version footguns.

Touches: `automation/commands/dispatchers.py`, new `automation/setup/inject_config.py`.

### I2. `mosdat preflight <scenario> --vms <vms>` — **IMPLEMENTED 2026-05-16**

Single command runs every "should have caught earlier" check before a scenario run:

- YAML + ScenarioModel schema validation
- VM SSH reachable, X11 cookie present
- VM has `wmctrl xclip xdotool xdg-mime xdg-open` installed
- Binary exists at `<install-path>` and contains expected feature symbols (configurable in scenario header)
- userData dir name detected from VM, matches scenario's wipe targets
- Dry-runs first 3 setup shell blocks (returns stdout, no UI assertions)

Report: PASS / FAIL per check. Catches userData-dir mismatch, missing deps, wrong binary, missing migrations version, missing XAUTHORITY in shell blocks.

Touches: new `automation/commands/preflight.py`.
Tests: `tests/test_preflight.py` (18 unit tests, mock SSH, no live VM required).

### I3. `mosdat build --pr <N> --deploy <vms>` — IMPLEMENTED (2026-05-16)

Reproducible PR build+deploy. Eliminates stale-clone class of bug.

Implemented in `automation/commands/build.py` + parser wiring + `tests/test_build_cmd.py`.
v1 ships `--target deb` only; rpm/AppImage/exe stubs marked TODO in `TARGETS`.
Exit codes: 0 OK, 1 missing verify-symbol, 2 build failed, 3 deploy failed,
4 clone/fetch failed, 5 bad args. Use `--dry-run` to preview the plan.

```
mosdat build --pr 3325 --repo RocketChat/Rocket.Chat.Electron \
    --target deb --deploy ubuntu2204,ubuntu2404 \
    --verify-symbol isTelephonyEnabled,telephonyGlobalShortcutConfig
```

Steps:

1. `gh repo clone` (or update existing) latest PR head.
2. `yarn install && yarn release --linux deb` (target-aware).
3. SCP built artifact to each VM `/tmp/`.
4. SSH install (`sudo dpkg -i ...` / `rpm -i` / etc).
5. SSH `strings <asar> | grep <symbols>` — fail if any missing.
6. Print version + commit SHA installed per VM.

Touches: new `automation/commands/build.py`.

### I4. Implicit X11 env per VM (TOML) — IMPLEMENTED 2026-05-16

```toml
[vm.ubuntu2204]
x11 = "auto"  # mosdat injects DISPLAY + XAUTHORITY + ozone flag
```

Behavior: any `- shell: |` step gets a prepended preamble (only if `x11=auto` and step contains `xdotool|wmctrl|xclip|xdg-|nohup.*/opt/.*-desktop`):

```bash
XAUTH=$(ls /run/user/$(id -u)/.mutter-Xwaylandauth.* /run/user/$(id -u)/gdm/Xauthority 2>/dev/null | head -1)
export DISPLAY=:0 XAUTHORITY="$XAUTH"
```

Removes XAUTH-forget-and-die failures from `tel:` dispatches and shell GUI steps.

Code: `automation/transport/x11_preamble.py` (`should_inject`, `inject`), `automation/config.py` (`VMConfig.x11` field + `load_config` wiring), `automation/runners/functional_steps.py` (preamble injection at shell dispatch), `automation/runners/functional.py` (`x11_mode` constructor param), `automation/main.py` (`x11_mode=vm.x11` wire). Default `"off"` — backwards-compatible. Tests: `tests/test_x11_preamble.py` (17 tests).

## Tier 2 — order-of-magnitude faster iteration

### I5. `mosdat replay <result-dir> --step N --verify "<prompt>"` — IMPLEMENTED

Rerun a single verify against the cached screenshot from a previous run. Iterate verify wording in seconds, not minutes.

```
mosdat replay results/functional/2026-05-16_18.../ubuntu2204 \
    --step 8 --verify "modal labeled 'Select Server' with two server rows"
```

Reads screenshot from result dir, re-asks VLM with new prompt, prints yes/no + raw VLM response.

Touches: new `automation/commands/replay.py`.

### I6. VLM verify cache by (screenshot SHA, prompt SHA) — IMPLEMENTED

Identical (image, prompt) pairs return cached yes/no. Cache key: `sha256(image_bytes) || sha256(prompt_text) || model`. TTL 24h.

Cuts ~50% off re-run time for partially-changed scenarios.

Touches:

- `automation/transport/vlm_cache.py` (new — SQLite cache, `cache_key`, `get`, `put`, `invalidate`, `clear`, `prune`, `stats`)
- `automation/vlm/client.py` — `verify()` wired to cache; `set_cache_enabled()` + `_cache_disabled` flag; `MOSDAT_VLM_NOCACHE` env var
- `automation/commands/parser.py` — `--no-cache` on `functional` + `replay`; `vlm-cache stats|clear|prune` subcommand
- `automation/commands/dispatchers.py` — `cmd_vlm_cache()` handler
- `automation/main.py` — `--no-cache` wiring + `vlm-cache` in dispatch table
- `tests/test_vlm_cache.py` — round-trip, TTL expiry, env var, concurrent puts

### I7. `verify: { accept_any: [...] }` native union — IMPLEMENTED 2026-05-16

```yaml
- accept_any:
    - "Settings panel open with General tab visible"
    - "Workspace login page unchanged (settings did not open)"
  verify_timeout: 15
```

Cleaner than long "either X OR Y" prose; fewer VLM false negatives.

Touches:

- `automation/scenario.py` — `AcceptAnyStep` model; mutual-exclusion validator with `verify`; added to `AnyStep` union.
- `automation/runners/scenario_loader.py` — `accept_any` field on `FunctionalStep`; parsed + `resolve_vars`.
- `automation/runners/functional_verify.py` — `_verify_accept_any()`: shared timeout, per-prompt events.
- `automation/runners/functional_steps.py` — `run_step` dispatches `accept_any` after `verify` block.
- `tests/test_accept_any.py` — 9 tests (schema + runtime).
- `shared/scenarios/functional/example-accept-any.yaml` — schema reference.
- `shared/scenarios/functional/3325-global-shortcut.yaml` — Phase 2 verify converted to `accept_any`.

### I8. Workspace-agnostic scenarios via jinja vars — IMPLEMENTED 2026-05-16

```yaml
vars:
  workspace_title: "Workspace"
  preferred_server_url: "https://rocketchat.jeanbrito.com/"

steps:
  - verify: "modal lists '{{ workspace_title }}' and 'Mobile RC' as options"
```

Vars overridable from CLI: `--var workspace_title=Southlogic`.

```
mosdat functional config.toml --vms ubuntu2204 --test my-scenario \
    --var workspace_title=Southlogic --var dial_number=+1234567890
```

Behaviour:

1. `vars:` block in YAML defines scenario defaults (flat string→string; numbers/bools cast to str).
2. `--var KEY=VALUE` CLI flags override or add vars at run time. Duplicate CLI keys are an error.
3. `{{ key }}` (Jinja2-compatible, optional surrounding whitespace) is substituted in all
   string step fields: shell, verify, verify_not, verify_input, verify_click, localize,
   then_type, type, then_key, key, then_key_pre, key_pre, focus, if_visible, launch,
   launch_window, verify_click_diff_prompt, canary_verify, canary_char, accept_any items,
   on_failure_agent goal/success_check, nested then: steps.
4. Rendering happens **before** `ScenarioModel.model_validate` — type checks run on rendered values.
5. Missing var (referenced but not defined) → fail-fast at load time with key name + step index.
6. Scenarios without `{{ ... }}` markers and without `vars:` block work unchanged.
7. Uses real `jinja2` (StrictUndefined) when available; regex fallback otherwise.

Variable types: only flat string→string for v1. Numbers/bools in yaml `vars:` are cast to
str before rendering (`True` → `"True"`, `3` → `"3"`). Nested dicts/lists are not supported.

Touches:

- `automation/runners/var_subst.py` (new — jinja2 rendering engine + regex fallback + MissingVarsError)
- `automation/runners/scenario_loader.py` — `load_test_yaml` accepts `cli_vars` kwarg;
  renders raw step dicts before ScenarioModel.model_validate.
- `automation/commands/parser.py` — `--var KEY=VALUE` (action=append) on `mosdat functional`.
- `automation/main.py` — parse `--var` into dict (error on duplicate keys), pass to `load_test_yaml`.
- `automation/scenario.py` — `vars` field changed from `Optional[dict[str, str]]` to `Optional[dict[str, Any]]`.
- `pyproject.toml` — added `jinja2>=3.0` dependency.
- `tests/test_scenario_vars.py` — 25 tests.
- `shared/scenarios/functional/example-vars.yaml` — schema reference.

## Tier 3 — UX / DX polish

### I9. Named phases + `--from-phase` / `--until-phase` — IMPLEMENTED 2026-05-16

```yaml
phases:
  - id: A
    name: toggle-off baseline
    from_step: 1
  - id: B
    name: toggle-on dispatches modal
    from_step: 10
```

Run subset: `--from-phase B`.

```
mosdat functional config.toml --vms ubuntu2204 --test 3325-master-toggle --from-phase B
# prints: [phases] running B (toggle-on dispatches modal) [steps 10..20]
```

Code:

- `automation/scenario.py` — `PhaseDef` model; `ScenarioModel.phases` field; `_check_phases` validator (unique ids, strictly increasing from_step, from_step >= 1).
- `automation/commands/parser.py` — `--from-phase ID` and `--until-phase ID` on `mosdat functional`.
- `automation/main.py` — `_resolve_phases()` helper (phase → step bounds, warn on override, SystemExit on unknown id); called in `cmd_functional` before B4 step slicing; prints `[phases] running ...` log line.
- `shared/scenarios/functional/3325-master-toggle.yaml` — added `phases:` block (A from_step=1, B from_step=10).
- `tests/test_scenario_phases.py` — 24 tests (schema + \_resolve_phases + demo scenario).

### I10. Step labels in logs — IMPLEMENTED 2026-05-16

Use scenario's comment header (`# ── A4: Dispatch tel: → expect modal ───`) as the step label. Log "FAIL: A4 dispatch tel: → expect modal" instead of "FAIL: Step 8".

Parses YAML with ruamel.yaml (round-trip mode) to access comments above each `- ` step entry.
Box pattern `# ── <label> ──` takes priority; plain comments fall back; explicit `label:` field wins all.
Multi-comment blocks joined (space, 80-char cap). Pure-separator lines (`# ════`) are skipped.

Touches:

- `automation/scenario.py` — `label: Optional[str] = None` on `_StepBase`.
- `automation/runners/scenario_loader.py` — `_extract_step_labels()`, `_parse_comment_label()`, `_tokens_to_comment_lines()` helpers; `load_test_yaml` calls them pre-parse; `parse_step` maps `label` field; `resolve_vars` passes through label.
- `automation/runners/functional_steps.py` — `step_display` computed as `"N: label"` or `"N"`; used in log lines; emitted as `step_label` field in `step_start` event.
- `automation/reporting/report_html.py` — labeled steps show `<sub>#N</sub>` + label as header; unlabeled steps show `Step N` unchanged.
- `pyproject.toml` — added `ruamel.yaml>=0.17` dependency.
- `tests/test_step_labels.py` — 28 tests covering all cases.

### I11. Imported step library — IMPLEMENTED 2026-05-16

```yaml
imports:
  - cleanup-rocketchat
  - install-x11-deps
steps:
  - import: cleanup-rocketchat # dict form (plain YAML, no tag needed)
  - !import install-x11-deps # tag form (also supported)
  - shell: ...
```

Fragment files live in `shared/scenarios/imports/<name>.yaml`. Each is a YAML dict with a `steps:` list. Fragment steps are spliced inline at load time; expansion is NOT written to disk.

Behaviour:

1. Both shapes accepted: `- import: name` (dict, standard YAML) and `- !import name` (custom tag, requires tag constructor).
2. Fragment loaded from `shared/scenarios/imports/<name>.yaml`. Walks up from scenario file to find project root.
3. Labels in imported steps get `[import:name] ` prefix (box-comment-derived or explicit `label:` field).
4. Vars from parent scenario (`{{ var }}`, I8) substitute into fragment steps — expansion happens before var rendering.
5. Recursive imports supported with cycle detection (RuntimeError naming the chain + scenario).
6. Missing fragment → load-time `FileNotFoundError` naming the fragment + scenario.
7. Manifest mismatch (`!import X` without listing X in top-level `imports:`) → `warnings.warn`, not error.

Canonical fragments shipped in `shared/scenarios/imports/`:

- `cleanup-rocketchat.yaml` — kill-loop + wmctrl close + apport-pkill + wipe both userData dirs.
- `install-x11-deps.yaml` — apt-get install wmctrl xclip xterm.
- `launch-rocketchat.yaml` — XAUTH+DISPLAY+ozone nohup launch + wait + workspace login verify (requires `{{ workspace_url }}`).

Demo: `shared/scenarios/functional/3325-master-toggle.yaml` — step A1 converted from inline 35-line shell block to `- import: cleanup-rocketchat`.

Touches:

- `automation/scenario.py` — `ImportStep` model (`import` alias field); added to `AnyStep` union; `ScenarioModel.imports: list[str]` field.
- `automation/runners/scenario_loader.py` — `_imports_dir()`, `_load_fragment_steps()`, `_is_import_step()`, `_expand_imports()`, `_register_import_constructor()`, `_warn_manifest_mismatch()`; `load_test_yaml` calls expansion before var substitution.
- `shared/scenarios/imports/cleanup-rocketchat.yaml` — new fragment.
- `shared/scenarios/imports/install-x11-deps.yaml` — new fragment.
- `shared/scenarios/imports/launch-rocketchat.yaml` — new fragment.
- `shared/scenarios/functional/3325-master-toggle.yaml` — step A1 uses `- import: cleanup-rocketchat`; `imports:` manifest added.
- `tests/test_step_imports.py` — 44 tests.

### I12. Author session → scenario diff — IMPLEMENTED 2026-05-16

After `mosdat author` session, `mosdat author export --base <existing-scenario>` emits a unified diff to insert recorded steps into an existing scenario at a given step index.

```
mosdat author export --session <id> --base shared/scenarios/functional/foo.yaml
mosdat author export --session <id> --base foo.yaml --insert-at-step 3
mosdat author export --session <id> --base foo.yaml --name new-name [--output path]
mosdat author export --session <id> --base foo.yaml --dry-run
```

Code: `automation/author_cli.py` (`_export_with_base`, new flags on `export` subparser),
`automation/commands/parser.py` (new flags wired to `mosdat author export`),
`automation/commands/dispatchers.py` (`cmd_author` passes `--base`, `--insert-at-step`, `--dry-run`).
Tests: `tests/test_author_diff.py` (8 tests).

### I13. `mosdat doctor <vms>` — IMPLEMENTED

Mirror of `ctx doctor`. Checks per VM: SSH, X11 cookie path, GNOME version, installed deps, binary presence, expected userData dir, asar grep, disk free in /tmp, current RC process count.

Touches: new `automation/commands/doctor.py`.

### I14. HTML report enhancements — IMPLEMENTED 2026-05-16

For each step include:

- Exact shell command sent (post X11-injection)
- VM response stdout/stderr (truncated to 2KB)
- `config.json` snapshot after shell steps (opt-in via `report_config_snapshots: true` in YAML or `--config-snapshots` CLI flag)
- Verify prompt text + VLM raw response (not just yes/no), cache hit indicator

New events emitted into events.jsonl (additive, backwards-compatible):

- `shell_step`: `command_sent`, `stdout_tail`, `stderr_tail`, `exit_code`, `duration_ms`
- `verify_step`: `prompt_text`, `raw_vlm_response`, `verdict`, `cache_hit`, `latency_ms`
- `accept_any_step`: `prompts`, `per_prompt_verdicts`, `verdict`
- `config_snapshot`: `content` (head 4KB of config.json), opt-in only

Touches:

- `automation/vlm/input.py` — `shell_result()` returning SSHResult
- `automation/vlm/client.py` — `verify_with_meta()` returning (verdict, raw, cache_hit)
- `automation/runners/functional_steps.py` — emits new events; `_emit_config_snapshot()`
- `automation/runners/functional_verify.py` — `_wait_for_state_with_meta()`, `_verify_accept_any_with_meta()`
- `automation/runners/functional.py` — `_config_snapshots: bool` field
- `automation/scenario.py` — `report_config_snapshots: bool = False`
- `automation/reporting/report_html.py` — renders new event types; new CSS classes
- `automation/commands/parser.py` — `--config-snapshots` flag
- `automation/main.py` — wires flag + yaml field into runner
- `tests/test_report_enhancements.py` — 16 unit tests

### I15. Negative-test fixture suite — IMPLEMENTED 2026-05-16

Deliberately-broken scenarios (wrong selector, deleted binary, killed network) that MUST fail with specific error. Guards against the "passes on broken build" lie.

Fixtures (5):

- `wrong-binary-path.yaml` — launches `/opt/Nonexistent.Chat/rocketchat-desktop` → verify fails (no window)
- `wrong-selector.yaml` — localize "purple unicorn icon" → VLM localize fails
- `wrong-verify-prompt.yaml` — verify "giant pink cat on screen" → VLM returns no
- `killed-process.yaml` — kills RC mid-scenario → subsequent verify fails (no window)
- `corrupt-config.yaml` — writes broken JSON to config.json → login page verify fails (RC shows Add Server)

Runner: `tests/test_negative_fixtures.py` (marker: `negative_fixtures`, opt-in via `MOSDAT_NEG_FIXTURES_VM` env var).
Schema: all fixtures validate against `ScenarioModel` (fail at runtime, not parse time).
Smoke: `pytest tests/test_negative_fixtures.py -v -m "not negative_fixtures"` → 1 skipped.

Touches: new `tests/negative_fixtures/`, `tests/test_negative_fixtures.py`.

---

## Tier 4 — pivot-faster tooling

### R1. `automation/routines/` — parameterized reusable procedure library — IMPLEMENTED 2026-05-17

Routines are parameterized, tested, reusable procedures that wrap a sequence of scenario steps with pre/postcondition verification and optional fallback branches. Composable into scenarios as `- routine: foo` or `- routine: { name: foo, with: { key: val } }`.

```yaml
# shared/routines/launch-rocketchat.yaml
name: launch-rocketchat
description: Kill any running RC, wipe userData, relaunch
inputs:
  app_path:
    name: app_path
    required: true
preconditions:
  - verify_not: "RC window or crash dialog visible"
steps:
  - shell: pkill -f rocketchat-desktop || true
  - shell: "{{ app_path }} --no-sandbox &"
postconditions:
  - verify: "RC login screen visible"
```

Usage in scenarios:

```yaml
steps:
  - routine: launch-rocketchat
  # or with inputs:
  - routine:
      name: launch-rocketchat
      with:
        app_path: /opt/Rocket.Chat/rocketchat-desktop
```

CLI:

```
mosdat routines list
mosdat routines show launch-rocketchat
```

Schema: `automation/routines/schema.py` — `Routine`, `RoutineInput`, `RoutineFallback` Pydantic models. Validated: name is kebab-case, preconditions/postconditions must be verify steps only, required inputs without default error on missing call arg.

Expansion: `automation/routines/runner.py` `expand_call()` — resolves inputs (call args > scenario vars > defaults), renders jinja subst over all steps, selects fallback branch if capability_manifest present and `when:` expression matches, recursively expands nested routine calls, cycle detection via `_resolving` frozenset.

Scenario integration: `automation/runners/scenario_loader.py` recognizes `- routine:` (both short string form and long `{name, with}` form), expands via `expand_call` BEFORE `ScenarioModel.model_validate`. Happens after I11 import expansion, before I8 var substitution. Internal metadata keys (`_routine_event`, `_on_failure`) stripped before schema validation.

Touches:

- `automation/routines/__init__.py` — package init
- `automation/routines/schema.py` — Pydantic models
- `automation/routines/loader.py` — `load_routine(slug)`, `list_routines()`, `routines_dir()`
- `automation/routines/runner.py` — `expand_call()` expansion engine
- `automation/commands/routines.py` — `list` / `show` / `test` (R3 stub) subcommands
- `automation/commands/parser.py` — `routines` subparser wired
- `automation/main.py` — `run_routines` added to dispatch dict
- `automation/runners/scenario_loader.py` — R1 expansion block in `load_test_yaml`
- `tests/test_routines_schema.py` — 12 schema unit tests
- `tests/test_routines_loader.py` — 6 loader tests
- `tests/test_routines_runner.py` — 10 runner tests
- `tests/test_routines_scenario_integration.py` — 4 integration tests
- `tests/expected_help/mosdat.txt` — updated snapshot
- `docs/routines.md` — schema reference + worked example

### R7. Routine schema versioning + PR #3325 master-toggle conversion — IMPLEMENTED 2026-05-17

Schema versioning enforcement for routine YAML files plus a routines-first rewrite of the 3325-master-toggle scenario as a worked example.

**Versioning (Part A):**

- `CURRENT_SCHEMA_VERSION = "v1"` and `SUPPORTED_SCHEMA_VERSIONS = ["v1"]` exported from `automation/routines/__init__.py`.
- `Routine.schema_version` field validated against supported list; unknown future versions raise `ValidationError` with clear upgrade message.
- `_migrate_to_current(data)` stub added to `loader.py`, called before `Routine.model_validate` in both `load_routine` and `list_routines`. Documents migration pattern for future schema bumps.
- `mosdat routines version` subcommand prints current and supported versions.

**Scenario conversion (Part B):**

- `shared/scenarios/functional/3325-master-toggle.yaml`: **236 → 76 lines (-160, -68%)**.
- Expanded step count: 32 (same behavior, just expressed via routine calls).
- Inline shell config-staging + launch + kill blocks replaced with `cleanup-rocketchat`, `launch-rocketchat`, `dispatch-tel-link`, `verify-app-alive` routine calls.
- Also fixed pre-existing bug in `dispatch-tel-link.yaml`: `- wait: "{{ settle_seconds }}"` (string, never rendered) → `- wait: 8` (int, validated correctly).

Touches:

- `automation/routines/__init__.py` — `CURRENT_SCHEMA_VERSION`, `SUPPORTED_SCHEMA_VERSIONS`
- `automation/routines/schema.py` — `_schema_version_supported` field validator
- `automation/routines/loader.py` — `_migrate_to_current` stub + wired into `load_routine` / `list_routines`
- `automation/commands/routines.py` — `_cmd_version` + `version` dispatch
- `automation/commands/parser.py` — `version` subparser wired
- `shared/routines/dispatch-tel-link.yaml` — `wait: 8` fix
- `shared/scenarios/functional/3325-master-toggle.yaml` — routines-first rewrite
- `tests/test_routines_versioning.py` — 7 versioning tests
- `docs/routines.md` — schema versioning + worked example sections

### F2. `mosdat recipes` — platform-constraint corpus + pivot browser — IMPLEMENTED 2026-05-17

Searchable corpus of known platform constraints and ordered workaround strategies (pivots). Eliminates re-discovery of constraints that have already been hit and documented.

```
mosdat recipes list                      # slug + title for every recipe
mosdat recipes show settings-electron-linux
mosdat recipes search "alt+key swallowed"
```

Seed corpus (6 recipes in `shared/recipes/`):

- `settings-electron-linux` — RC Electron has no accelerator for Settings on Linux; 3 pivots
- `webview-focus-stealing` — Chromium webview swallows alt+key when input focused; 4 pivots
- `vnc-keysyms-syntax` — VNC key syntax: single char or \_KEYSYMS name, not word aliases; 3 pivots
- `vnc-framebuffer-virtio` — Proxmox vga=virtio breaks compositor/Electron framebuffer; 3 pivots
- `redux-persist-migrations-version` — missing migrations.version resets all config on launch; 3 pivots
- `second-instance-ipc-xauthority` — deep-link dispatch silently drops without XAUTHORITY; 3 pivots

Schema: `automation/recipes/schema.py` — Pydantic `Recipe` + `Pivot` models. Required fields: slug (kebab-case), title, symptoms, constraint, pivots, sources. Pivot.cost: low|medium|high|external.

`mosdat recipes list` skips and warns on invalid files (does not abort).

Touches:

- `shared/recipes/*.yaml` — 6 seed recipe files
- `automation/recipes/__init__.py` + `automation/recipes/schema.py` — Pydantic schema
- `automation/commands/recipes.py` — list / show / search implementation
- `automation/commands/parser.py` — recipes subparser wired
- `automation/main.py` — `run_recipes` added to dispatch dict
- `tests/test_recipes.py` — 10 unit tests (schema x 6, list, show, search x 3, load_all invalid-skip)

---

## Tier 4 — pivot-faster tooling (post-PR3325 retrospective)

### F1a. `mosdat lint <scenario>` — static YAML analyzer — IMPLEMENTED 2026-05-17

Static anti-pattern scanner for functional scenario YAML files. Runs offline (no VM, no VLM).
Exits 0 if no WARN, 1 if any WARN.

```
mosdat lint shared/scenarios/functional/3325-master-toggle.yaml
```

Seven WARN rules:

1. **key-combo** — split on `+`, check main key is in `_KEYSYMS`, `_MODIFIERS`, or single char (catches `ctrl+plus`, `alt+comma` spelled as word).
2. **coord-drift** — `xdotool mousemove \d+ \d+` → "coord drift risk; use VNC native or VLM localize".
3. **transient-localize** — `localize:` referring to kebab / dropdown / menu / popup → "VLM hallucinates transient popups".
4. **settings-toggle-no-verify** — Settings nav → toggle → no UI verify before kill+relaunch → "prefer --inject-config pre-stage".
5. **heredoc-in-yaml** — `cat > ... <<'EOF'` inside YAML literal → "heredoc breaks YAML; use printf".
6. **tel-dispatch-xauth** — `nohup ... "tel:` not preceded by `XAUTHORITY=` within 5 lines.
7. **config-json-no-migrations** — config.json write without `__internal__.migrations.version` in ±20-line window.

Optional: if scenario has `requires_capabilities:` block with `asar_sha`, lint consults capability manifest (F1c) for mismatch warnings.

Touches:

- `automation/commands/lint.py` (new) — 7 check functions + `run_lint(args) -> int`
- `automation/commands/parser.py` — `lint` subparser
- `automation/main.py` — `run_lint` added to dispatch dict
- `tests/test_lint.py` — 12 unit tests (7 WARN rules × pass+fail + 2 integration)

### F1b. `mosdat trace <toml> --vms <vm>` — input capability probe — IMPLEMENTED 2026-05-17

Launches the app binary on a VM, exercises common input methods via VNC + screenshot diff, and reports which work.

```
mosdat trace examples/rocketchat.toml --vms ubuntu2204 [--write-manifest]
```

Probes:

- Menu accelerators: `alt+f`, `alt+e`, `alt+v`, `alt+w`, `alt+h`, `F10`
- Common shortcuts: `ctrl+,`, `ctrl+f`, `ctrl+r`
- Webview focus stealing: `alt+f` in form-focused vs title-bar-focused context
- Sidebar kebab via VLM localize (reports confidence + transient dismiss timing)
- xdotool windowactivate + ctrl+f repeat

Output per probe: `OPEN` | `SWALLOWED (webview focus)` | `NO-ACCEL` with diff score and workaround hint.
Exit 0 (probe complete), 1 (unexpected SWALLOWED results), 2 (setup error).

Use `--write-manifest` to persist results to `shared/binary_capabilities/<sha>.json`.

Touches:

- `automation/commands/trace.py` (new) — `run_trace(args) -> int`
- `automation/commands/parser.py` — `trace` subparser
- `automation/main.py` — `run_trace` added to dispatch dict
- `tests/test_trace.py` — 6 unit tests (screenshot diff, manifest model, probe-with-mock-VNC)

### F1c. Capability manifest — IMPLEMENTED 2026-05-17

Helper module for storing and querying binary input probe results.

```python
from automation.setup.capability import manifest_path, load_manifest, write_manifest, get_for_vm
```

Schema (`shared/binary_capabilities/<asar_sha>.json`):

```json
{
  "asar_sha": "abc123...",
  "captured_at": "ISO date",
  "vm": "ubuntu2204",
  "accelerators": {
    "alt+w": "swallowed_in_webview",
    "ctrl+,": "no_accel",
    "F10": "open"
  },
  "popups": { "sidebar_kebab": "transient_800ms" },
  "persisted_state_keys": ["isTelephonyEnabled"],
  "test_ids_present": false
}
```

`get_for_vm(ssh)` computes SHA-256 of `app.asar` on the VM (first 16 hex chars used as key).
`mosdat trace --write-manifest` calls `write_manifest(sha, data)` automatically.
`mosdat lint` consults `load_manifest(sha)` when scenario has `requires_capabilities: {asar_sha: ...}`.

Touches:

- `automation/setup/capability.py` (new) — `manifest_path`, `load_manifest`, `write_manifest`, `get_for_vm`, `build_manifest`
- `tests/test_capability.py` — 5 unit tests (round-trip, missing, sha-mismatch, path, get_for_vm)

## Routines authoring discipline

Authoring guidance lives in `docs/AUTO-AUTHORING.md` § Routines-first authoring workflow. New scenarios should be composed from tested routines rather than written as monolithic step lists.

---

## Implementation order recommendation

Parallelizable (Wave 1, no file overlap):

- I1 inject-config
- I2 preflight
- I3 build
- I5 replay
- I6 VLM cache
- I13 doctor

Serial after Wave 1 (shared scenario.py touch):

- I4 implicit X11
- I7 accept_any
- I8 jinja vars
- I9 named phases

Polish (Wave 2):

- I10 step labels
- I11 imports
- I12 author diff
- I14 HTML report
- I15 negative fixtures

Each Tier-1 item: ~1-2 days. Total tier-1 ≈ 2 weeks single-dev or 3-4 days fanned out.

---

## Tier 4 — pivot-faster tooling

### Scenario authoring skill: state-first testing

Guidance for scenario authors on when to pre-stage Redux state via `--inject-config`
vs when UI navigation is genuinely required. Includes decision checklist, anti-patterns,
correct patterns, and the PR3325 before/after worked example.

→ [`docs/skills/state-first-testing.md`](skills/state-first-testing.md)

### Upstream asks: RocketChat/Rocket.Chat.Electron

Formal proposal to RC Electron maintainers covering three asks ranked by ROI:
(A) `CmdOrCtrl+,` accelerator on Settings (1-line fix), (B) `data-testid` attrs on
key telephony UI elements, (C) optional `--load-state` / `--export-state` CLI.
Suitable for filing as a GitHub issue or PR cover letter.

→ [`docs/upstream-rc-electron-asks.md`](upstream-rc-electron-asks.md)

---

## Tier 4 — cursor motion (Mn series)

### M1. Cursor motion research — IMPLEMENTED 2026-05-17

Algorithm survey (WindMouse, Bezier-with-jitter, PyAutoGUI tweens, minimum-jerk). Recommendation: Bezier with jitter.
Code: `docs/research/cursor-motion.md`.

### M2. `automation/transport/cursor_motion.py` — IMPLEMENTED 2026-05-17

`generate_path(start, end, *, profile, duration_ms, frame_count, jitter_amplitude, control_offset_ratio, emit_cap_ms, seed) -> list[Step]`. Four profiles: `instant`, `linear`, `bezier` (default), `windmouse`. Distance-scaled frame count (8–16) and duration (80–300 ms). Pins landing point exactly on target.
Code: `automation/transport/cursor_motion.py`.

### M3. Wire `generate_path` into `InputInjector.move_smooth` — IN PROGRESS

`automation/vlm/input.py` `InputInjector` to call `generate_path` for every cursor move, tracking current position for the start argument.

### M4. Step schema: `motion:` + `dwell_ms:` fields — IMPLEMENTED 2026-05-17

Per-step overrides on `click:` and `hover:` steps. `motion: instant|linear|bezier|windmouse`, `dwell_ms: <int>` (ms cursor rests on element before click fires).

### M5. `CursorConfig` TOML `[cursor]` block — IMPLEMENTED 2026-05-17

Global defaults: `profile`, `duration_ms`, `hover_dwell_ms`, `seed`. Pydantic model with range validators.
Code: `automation/config.py` `CursorConfig`.

### M6. `--cursor-instant` CLI flag — IMPLEMENTED 2026-05-17

Forces `profile = "instant"` for the run. For fast CI where hover-sensitive interactions are not exercised.

### M7. F2 recipe `cursor-teleport-misses-hover-handlers` — IMPLEMENTED 2026-05-17

Symptoms, root cause (no intermediate `pointermove` events), and three ordered pivots.
Code: `shared/recipes/cursor-teleport-misses-hover-handlers.yaml`.

### M8. F1 `mosdat trace --probe-hover <x,y>` — IMPLEMENTED 2026-05-17

Detects whether a UI element at given coordinates requires cursor motion to activate (fires a pointer-enter handler). Adds `motion_required: true` to the capability manifest for that coordinate.

---

## Tier 5 — documentation drift found during 2026-08-10 usage audit

Found while auditing what real test usage revealed vs what `lessons.md` still
claims. No code changes needed for T1/T2 — the fixes already shipped
(2026-05-01, commit `363cb62`); only the doc is stale. T3 is a genuine gap.

### T1. Close stale "GPU passthrough exclusivity" open loop in `lessons.md`

`lessons.md`'s "Open loops / known limitations" section (around line 30) still
lists "GPU passthrough exclusivity not enforced framework-side... See task #43
for fix" as open. It was fixed 2026-05-01 in commit `363cb62`:
`automation/proxmox/gpu.py` has `_host_gpu_lock()` (per-Proxmox-host,
`fcntl.flock`) and `gpu_lock()` (per-PCI-address), wired into
`GPUManager.attach_to_vm()` / `detach_from_current()`, with
`ProxmoxLockTimeout` and passing concurrency tests in
`tests/test_concurrent_safety.py` (contention, timeout, exception-safe
release).

Fix: verify the lock code and tests still pass, then rewrite the "Open loops"
entry to record it as closed (keep a one-line historical note + commit ref,
don't just delete — the framework-design rationale is worth keeping).

### T2. Correct "Visual regression is opt-in only" open loop in `lessons.md`

Same section lists visual regression (SSIM-diff) as not integrated / task #42.
The tooling shipped: `automation/visual.py`, `mosdat visual --capture/--check`
(SSIM, default threshold 0.95), CLI-wired in
`automation/commands/dispatchers.py` and `automation/main.py`, documented in
`docs/runbooks/visual-regression.md`, tested in `tests/test_visual.py`.

What's actually still true: it is a manually-invoked side command, not an
automatic gate inside `mosdat functional` / `mosdat confirm`.

Fix: reword the `lessons.md` entry from "not yet integrated" to "shipped
2026-05-01, remains opt-in by design — not auto-run inside functional/confirm."
If auto-running it by default is wanted, that's a _new_ decision to make
explicitly, not a leftover bug to close silently.

### T3. Promote "verify diagnosis before committing" lesson out of the VNC post-mortem

`docs/post-mortems/2026-05-18-vnc-stale-capture.md` (Postscript section)
documents that after the real VNC capture-race fix landed, two more
root-cause diagnoses were committed in sequence before the actual bug
(bool `str()` coercion in `automation/runners/var_subst.py:85`) was found —
one diagnosis was later shown to be fabricated: it asserted a Redux-persist
SIGTRAP that does not exist anywhere in Rocket.Chat.Electron's source or in
any captured crash dump.

This is currently only visible to someone who reads that specific
post-mortem file. It's a repeatable risk for any agent doing root-cause work
in this project, not a one-off.

Fix: add a new entry to `lessons.md` (not buried in a post-mortem) stating
the rule plainly: a subagent's root-cause claim is a hypothesis, not a
verified finding, until it's grepped against actual source / crash artifacts.
Require that check before the claim is asserted in a commit message or
lessons.md entry. Cross-reference the VNC post-mortem for the worked example
instead of duplicating its narrative.

### T4. Confirm whether the 2026-07-08 `HARNESS_ERROR` (issue #3308) recurs

The most recent captured real test run (`results/`, issue #3308,
2026-07-08) returned `HARNESS_ERROR` with environment-detection fields
(app_version, OS, display_server, ozone backend) all unpopulated (`—`)
rather than a scenario assertion failure. Only one instance was sampled —
not yet confirmed as a recurring pattern, and it may be an expected
limitation of environment detection inside a Flatpak sandbox rather than a
regression.

Fix: not a code fix yet. Re-run the #3308 scenario (or check for more recent
runs since 2026-07-08) and confirm whether the same fields come back
unpopulated. If it recurs, file it as a proper lesson with root cause; if
it was a one-off (e.g. a VM that hadn't finished booting), close this note
without a code change.
