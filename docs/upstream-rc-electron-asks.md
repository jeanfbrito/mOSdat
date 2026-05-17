# Upstream Asks: RocketChat/Rocket.Chat.Electron

*Formal proposal for RC Electron maintainers — suitable as a GitHub issue or PR cover letter.*

---

## 1. Context

We maintain **mOSdat** — an automated multi-OS desktop UI test harness for
RocketChat/Rocket.Chat.Electron. We run full end-to-end functional scenarios against
built Electron packages on Ubuntu 22.04, Ubuntu 24.04, and Fedora 40 VMs, driven by a
VLM (vision-language model) for visual verification and `xdotool`/`xdg-open` for
interaction.

The catalyst for these asks is the **PR3325 telephony feature** test campaign
(2026-05-16), which exposed three systemic friction points that cost us ~3.5 person-hours
of debug across 6 rerun cycles. We went 0/5 → 5/5 green only after building a
workaround layer (`--inject-config`, ~340 LOC + 27 tests) to bypass the Settings UI
entirely. The asks below would let us delete that layer.

---

## 2. Problem Statement

| Issue | Cost we paid | Repro |
|---|---|---|
| No `Ctrl+,` accelerator on Linux | ~2h debug + 4 failed test passes | `menuBar.ts` Settings item has no `accelerator:` field; keypress silently ignored on Linux |
| Sidebar kebab popup transient | ~1h debug | VLM `localize` call returns coords; by the time `xdotool click` fires, popup has dismissed; scenario retries loop |
| Webview steals `alt+w` | ~30min debug | Chromium consumes `alt+menu` sequence when a form (login) holds focus; Settings never opens |
| No state-seed CLI | Forced manual config.json wrangling | Required us to build the entire `mosdat functional --inject-config` layer (I1: 5 flags, ~340 LOC, 27 tests) to write Redux-persisted values before app launch |

All four issues share a root cause: **the only supported path to set persisted
Electron state is through the in-app Settings UI**, which is too transient and
keyboard-inaccessible for automated testing on Linux.

---

## 3. Three Asks (Ranked by ROI)

### A. Register `CmdOrCtrl+,` accelerator on the Settings menu item

**Impact**: Eliminates the `Ctrl+,`-has-no-binding footgun on Linux. macOS users
already expect this convention; Linux gets it as a free backfill.

**Effort**: 1 line in `menuBar.ts` (or equivalent menu registration file). No behavior
change — the click handler stays identical.

**Blocks** our current `alt+w → Down → Return` fallback nav, which we had to abandon
because `alt+w` is consumed by the webview when a form is focused.

### B. Add `data-testid` attributes to key interactive elements

**Impact**: Eliminates VLM coordinate guessing for stable UI elements. Current
workflow: VLM `localize` is asked to find an element, returns pixel coordinates, then
`xdotool click` fires. For transient elements (kebab popup, toggle buttons) this races.
With stable `data-testid` selectors we can use `xdotool search --name` or Playwright
`page.getByTestId()` instead.

**Effort**: Approximately 1 day — add `data-testid` strings, no logic changes.

Elements needed (intended DOM target in parentheses):

- `sidebar-kebab-button` — the `…` / kebab icon in the sidebar header
- `settings-menu-item` — the Settings entry in the kebab/app menu
- `telephony-toggle` — the master enable/disable toggle in Telephony settings
- `telephony-shortcut-field` — the global shortcut input field
- `telephony-server-dropdown` — the preferred-server `<select>` or custom dropdown trigger
- `telephony-server-select-modal` — the root node of the server-selection modal
- `telephony-server-option` — each server row inside that modal (index or name variant)
- `remember-this-choice-checkbox` — the checkbox in the server-select modal

### C. Optional state-seed CLI: `--load-state <json>` and `--export-state <path>`

**Impact**: Would eliminate the entire `mosdat --inject-config` layer. Instead of
writing `config.json` before launch (which requires knowing the correct
`migrations.version` to avoid a migration wipe), we could pass Redux state directly
to the Electron binary.

**Effort**: Medium. Requires hooking into the Redux store boot sequence before the
first `persistReducer` rehydration. Main risk: ordering — state must be seeded before
the store hydrates, not after.

**`--load-state <json>`**: Merge the given JSON object into the persisted Redux state
at startup, before the store rehydrates. Allows tests to declare initial state without
touching the filesystem.

**`--export-state <path>`**: After the app reaches ready state, write the current
Redux-persisted slice to `<path>` as JSON. Allows snapshot-based assertions.

This is the biggest ask and we understand it may not fit near-term roadmap. A and B
alone would meaningfully reduce our test friction.

---

## 4. Concrete Code Suggestion for Ask A

In `menuBar.ts` (or wherever the Settings `MenuItem` is constructed), add the
`accelerator` field:

```ts
{
  id: 'preferences',
  label: t('preferences'),
  accelerator: 'CmdOrCtrl+,',
  click: () => dispatch({ type: SIDE_BAR_SETTINGS_BUTTON_CLICKED }),
}
```

`CmdOrCtrl` maps to `Command` on macOS and `Control` on Linux/Windows.
No platform guard needed — Electron handles the mapping.

If a global shortcut conflict exists on any platform, `CommandOrControl+,` is the
standard Electron convention for Preferences (used by VS Code, Slack, Discord on macOS
and increasingly on Linux).

---

## 5. Concrete `data-testid` Targets for Ask B

| Attribute value | Intended DOM target |
|---|---|
| `sidebar-kebab-button` | `<button>` that opens the sidebar overflow / kebab menu |
| `settings-menu-item` | `<li>` or `<button>` for the Settings entry in that menu |
| `telephony-toggle` | `<input type="checkbox">` or toggle root for the Enable Telephony setting |
| `telephony-shortcut-field` | `<input type="text">` for the keyboard shortcut accelerator string |
| `telephony-server-dropdown` | Trigger element of the preferred-server dropdown |
| `telephony-server-select-modal` | Root `<div>` of the TelephonyServerSelectModal |
| `telephony-server-option` | Each server row `<li>` or `<button>` inside the modal |
| `remember-this-choice-checkbox` | `<input type="checkbox">` for the "remember this choice" option |

We are not prescribing attribute format — `data-testid`, `data-qa-id`, or any
consistent naming convention your codebase already uses would work.

---

## 6. What We'll Do If Accepted

If asks A and B are merged upstream we will open a follow-up mOSdat PR that:

1. Removes the `alt+w` menu-navigation fallback from all PR3325-class scenarios
   (currently present in `3325-master-toggle.yaml`, `3325-global-shortcut.yaml`, and
   `3325-cold-start.yaml`).
2. Replaces VLM `localize` calls for kebab + Settings item with deterministic
   `xdotool search --classname` or Playwright `getByTestId` steps.

If ask C is accepted we will open a further PR that replaces the entire
`automation/setup/inject_config.py` layer (I1, ~340 LOC) with a thin wrapper that
emits `--load-state` instead of writing `config.json` directly, removing the
`migrations.version` detection complexity entirely.

---

## 7. Contact

- Repository: <https://github.com/RocketChat/mOSdat> *(link to your public repo here)*
- Improvement roadmap: [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) — items I1–I15
- Maintainer: Jean Brito (jean.brito@rocket.chat)

We are happy to assist with any of these PRs if the direction is approved.
