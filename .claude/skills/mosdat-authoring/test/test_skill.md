# mosdat-authoring Skill — Manual Verification Checklist

Run these checks after installing or modifying the skill.

---

## 1. Skill Discovery

- [ ] Open a fresh Claude Code session inside the mOSdat project directory.
- [ ] Invoke `/mosdat-authoring` or ask "write tests for X in mOSdat".
- [ ] Verify the skill body loads: you see the Pre-flight Checks section and the Routines-First Workflow numbered steps.
- [ ] Verify the frontmatter name matches: `name: mosdat-authoring`.

```bash
grep "name: mosdat-authoring" /home/jean/projects/linux-testing/mOSdat/.claude/skills/mosdat-authoring/SKILL.md
```

Expected: one match on the frontmatter line.

---

## 2. Routine References Resolve

- [ ] The skill references `mosdat routines list`. Run it and confirm output is non-empty.

```bash
# Run from project root
mosdat routines list
```

Expected: at least 9 routines listed (cleanup-rocketchat, launch-rocketchat, dispatch-tel-link,
open-settings, verify-app-alive, enable-telephony-toggle, disable-telephony-toggle,
select-telephony-server-from-modal, set-telephony-shortcut).

- [ ] Pick one routine name from the list and run `mosdat routines show <name>`.

Expected: full YAML dump for that routine.

---

## 3. Recipe References Resolve

- [ ] The skill references `mosdat recipes search`. Run it and confirm it works.

```bash
mosdat recipes list
```

Expected: at least 6 recipe entries listed.

```bash
mosdat recipes search "notification"
mosdat recipes search "accelerator"
```

Expected: either relevant matches or "no matches" — no crash, no command-not-found.

---

## 4. Canonical Worked Example Exists

- [ ] The skill references `shared/scenarios/functional/3325-master-toggle.yaml`.

```bash
ls -la /home/jean/projects/linux-testing/mOSdat/shared/scenarios/functional/3325-master-toggle.yaml
```

Expected: file exists, size around 76 lines.

```bash
wc -l /home/jean/projects/linux-testing/mOSdat/shared/scenarios/functional/3325-master-toggle.yaml
```

Expected: 76 lines (or close — comments may vary).

---

## 5. Synthetic PR Workflow

Run through the skill against a synthetic "PR #9999 adds a server-list search bar" task.

- [ ] List atomic interactions (expected: ~5: cleanup, launch, open-sidebar, type in search, verify results).
- [ ] Run `mosdat routines list` and identify which atomic interactions have existing routines.
- [ ] Identify which need new routines (expected: `type-in-sidebar-search`, `verify-search-results`).
- [ ] Draft one new routine YAML in `shared/routines/` following the authoring checklist.
- [ ] Run `mosdat routines test <new-routine> --vms <vm> --fixture rc-launched-1-server-telephony-on`.
- [ ] Compose a minimal scenario in `shared/scenarios/functional/9999-search-bar.yaml`.
- [ ] Validate schema with `python3 -c "from pathlib import Path; from automation.runners.scenario_loader import load_test_yaml; load_test_yaml(Path('shared/scenarios/functional/9999-search-bar.yaml'))"`.
- [ ] Run `mosdat lint shared/scenarios/functional/9999-search-bar.yaml` — expect no errors.
- [ ] Run `mosdat functional <toml> --vms <vm> --test 9999-search-bar`.
- [ ] Run `mosdat routines report --format md` — confirm new routine appears with test history.

---

## 6. Anti-Pattern Detection

- [ ] Invoke the skill and ask it to review a scenario that navigates Settings via `Ctrl+,`.
- [ ] Confirm the skill identifies this as the "Settings nav for persisted state" anti-pattern.
- [ ] Confirm it recommends `--inject-config` (I1) or `launch-rocketchat` with an input parameter.

---

## 7. Definition-of-Done Gate

For the synthetic PR #9999 scenario created in check 5:

- [ ] `mosdat lint` clean
- [ ] `mosdat routines test` green for each new routine
- [ ] `mosdat routines report` shows new routines (not `untested`)
- [ ] `mosdat functional` passes end-to-end
- [ ] No `# TODO:` markers in the scenario YAML
- [ ] Help-drift snapshot passes (no unintended CLI changes from test run)

---

## 8. Global Symlink (optional — cross-project use)

```bash
# From project root
ln -s "$(pwd)/.claude/skills/mosdat-authoring" ~/.claude/skills/mosdat-authoring
ls -la ~/.claude/skills/mosdat-authoring/SKILL.md
```

Expected: symlink resolves to project-tracked file.

- [ ] Open a Claude Code session outside the mOSdat project.
- [ ] Invoke `/mosdat-authoring`.
- [ ] Verify the skill body loads from the global symlink.
