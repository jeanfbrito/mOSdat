# Changelog

All notable changes to this project are documented in this file.

The format follows Keep a Changelog and uses reverse chronological ordering.

## [Unreleased] Tier 4 (2026-05-17)

### Added

### Added (Mn series — 2026-05-17)

- **M1–M8 cursor motion** — VNC cursor no longer teleports. Bezier-with-jitter
  path generator (`automation/transport/cursor_motion.py`) with profiles:
  instant, linear, bezier (default), windmouse. Fires hover handlers + keeps
  transient popups alive between sequential clicks.
- New TOML `[cursor]` config (profile, duration_ms, hover_dwell_ms, seed).
- Per-step `motion: instant|linear|bezier` + `dwell_ms: <int>` on click/hover.
- `--cursor-instant` CLI flag on `mosdat functional` for fast CI runs.
- F2 recipe `cursor-teleport-misses-hover-handlers` documents the class.
- F1 trace gains `--probe-hover <coords>` to detect motion-required elements.
- `open-settings` routine fallback updated to use `dwell_ms: 250` on the kebab
  popup interaction.
- References: `docs/research/cursor-motion.md`, `automation/transport/cursor_motion.py`,
  `shared/recipes/cursor-teleport-misses-hover-handlers.yaml`, `docs/routines.md`.

- R1-R7 routines framework shipped for reusable scenario composition via `- routine: <name>` and `- routine: { name, with }`.
- Routines library lives in `shared/routines/*.yaml` with parameterized preconditions, steps, postconditions, and fallbacks.
- `mosdat routines` CLI surfaced with: `list`, `show`, `test`, `trace`, `explain`, `fixtures`, `report`, `version`.
- Seed routine coverage includes cleanup, launch, and dispatch workflows (R2), plus Settings/telephony flows (R5).
- Capability-aware fallback routing added to routine execution path (R4).
- Routine fixtures/testing scaffolding added for deterministic validation (R3).
- Routine coverage reporting support added for authoring feedback loops (R6).
- References: `docs/IMPROVEMENTS.md` (Tier 4, R1 section), `docs/routines.md`.

- F1 `mosdat lint <scenario>` added as static analyzer for known scenario footguns.
- Lint rules focus on repeated failure patterns documented during PR #3325 execution and retrospective loops.
- References: `docs/IMPROVEMENTS.md` (F1a), `docs/retrospectives/pr3325-saga.md` (traps and lessons).

- F1 `mosdat trace <toml> --vms <vm>` added as live input-capability probe.
- Capability manifest path standardized at `shared/binary_capabilities/<asar_sha>.json`.
- Manifest stores accelerator behavior, popup behavior, and related capability signals for authoring and lint checks.
- References: `docs/IMPROVEMENTS.md` (F1b, F1c).

- F2 `mosdat recipes` added as searchable platform-constraint and workaround corpus.
- Seed corpus includes 6 recipes covering Linux/Electron constraints discovered during PR #3325 execution.
- References: `docs/IMPROVEMENTS.md` (F2), `docs/retrospectives/pr3325-saga.md` (pivots/traps).

- F3 `/insight` skill added with project autouse hook at `.claude/skills/mosdat-insight/`.
- Purpose: surface relevant project lessons after repeated failure patterns.
- References: `docs/IMPROVEMENTS.md` (F3), git `a637eae`, `0d39453`.

- F4 strict auditor brief contract documented at `~/.claude/agents/auditor.md`.
- Purpose: prevent wrong-clone/wrong-binary audits that previously caused false negatives and stalled work.
- References: `docs/IMPROVEMENTS.md` (F4), `docs/retrospectives/pr3325-saga.md` (Trap 3).

- F5 state-first authoring guide published at `docs/skills/state-first-testing.md`.
- RC.Electron upstream request pack documented at `docs/upstream-rc-electron-asks.md`.
- References: `docs/IMPROVEMENTS.md` (F5), git `9c42f43`.

### Changed

- VNC session recording default changed to ON.
- New opt-out flag: `--no-record-session`.
- Prior mode was opt-in with `--record-session`.
- References: git `1a36296`, `docs/IMPROVEMENTS.md` context notes, `docs/retrospectives/pr3325-saga.md`.

### Notes

- This tier is a post-retrospective pivot-faster tooling wave focused on preventing repeat authoring/debugging traps from the PR #3325 saga.
- Primary roadmap source: `docs/IMPROVEMENTS.md` (Tier 4 sections, captured 2026-05-17).

## [Unreleased] Tier 1-3 (2026-05-16)

### Added

- I1: `--inject-config` / `--inject-servers` for declarative pre-stage of Electron userData before scenario run.
- I2: `mosdat preflight` for scenario schema + VM dependency + symbol + dry-run checks.
- I3: `mosdat build --pr <NUMBER>` for reproducible PR build/deploy/verify flow.
- I4: implicit X11 environment injection via `[vm.NAME] x11 = "auto"`.
- I5: `mosdat replay <result-dir>` for verify iteration on cached screenshots.
- I6: VLM verify cache + `mosdat vlm-cache` commands + `MOSDAT_VLM_NOCACHE` bypass env.
- I7: native `verify.accept_any` step union for robust state assertions.
- I8: Jinja `vars:` block + CLI `--var key=value` for workspace-agnostic scenarios.
- I9: named `phases` with `--from-phase` / `--until-phase`.
- I10: step labels derived from inline YAML comments.
- I11: `- import: FRAGMENT` directive for reusable step fragments.
- I12: `mosdat author export --base <SCENARIO>` for session-to-scenario diffs.
- I13: `mosdat doctor` for per-VM connectivity/dependency checklist.
- I14: report enhancements + optional `--config-snapshots` capture path.
- I15: negative-fixture suite with `MOSDAT_NEG_FIXTURES_VM` opt-in execution.
- References: `docs/IMPROVEMENTS.md` (I1-I15), git `a499109`, `95077da`, `a42d2c5`.

### Testing

- Per-feature tests are documented in `docs/IMPROVEMENTS.md` under each I/F/R item.
- Tier 1-3 adds broad unit and integration coverage across command wiring, scenario loading, report events, and negative fixtures.
- Requested aggregate rollups: `~318` tests across I1-I15, `~46` across F1-F2, `~80` across R1-R7.
- Aggregate rollups are included here as release notes targets; per-file and per-feature counts remain the auditable source of truth.
- References: `docs/IMPROVEMENTS.md` test callouts, git `95077da`.

### Retrospective Context

- Full narrative: `docs/retrospectives/pr3325-saga.md`.
- Outcome documented: 5/5 PR #3325 scenarios green after repeated pivots and tooling hardening.
- Root traps captured: Linux Settings accelerator assumptions, transient UI localization noise, and wrong-clone auditing.
- Improvements I1-I15 map directly to repeated pain points hit during the PR #3325 run.
- References: `docs/retrospectives/pr3325-saga.md`, `docs/IMPROVEMENTS.md` preamble and cross-reference table.

### Upstream

- RC.Electron draft PR #3336 noted in project retrospective context.
- Pending/target asks documented: `data-testid` attributes and optional state seed/export CLI flags.
- References: `docs/retrospectives/pr3325-saga.md` (Open work), `docs/upstream-rc-electron-asks.md`.

## [0.2.0] Pre-PR3325 baseline

### Baseline

- Baseline state before Tier 1 improvements. See `docs/AGENTS.md` for pre-existing capabilities.

### Context from available docs

- Project already operated as a multi-OS Electron/desktop testing harness targeting Linux/Windows VMs on Proxmox.
- Core runner orchestration existed with VM-driven functional execution flows.
- Scenario authoring workflow existed and was actively used before I1 rollout.
- Session recording and dashboard/authoring primitives existed before the I1-I15 wave.
- These points are conservative and derived from AGENTS overview + pre-I1 git history context.
- References: `AGENTS.md`, git history entries before `a499109`, `docs/retrospectives/pr3325-saga.md` timeline.

[Unreleased]: https://example.invalid/unreleased
[0.2.0]: https://example.invalid/0.2.0
