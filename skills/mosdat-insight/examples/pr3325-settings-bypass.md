---
date: "2026-05-16"
project: mOSdat
tags:
  - mosdat
  - electron
  - rocketchat
  - vlm
  - settings-ui
topic: "Bypass RC Settings UI by pre-staging Redux state in config.json — faster and reliable"
kind: war-story
scope: project-shared
category: mosdat/scenarios
confidence: high
cost: "6 attempts × ~20min = ~2hr"
---

## Context
Running PR3325 telephony scenarios. Needed to toggle persisted RC settings (isTelephonyEnabled, telephonyPreferredServer) between scenario phases via the in-app Settings UI.

## Root cause
RC Electron on Linux has no `Ctrl+,` keyboard accelerator (menuBar.ts registers the item without `accelerator:`). Sidebar kebab popup is transient — VLM hallucinates click coordinates. `alt+w` is swallowed by the webview when login form holds focus. All three UI-navigation paths are flaky by design.

## Pivot
Pre-stage all telephony Redux state directly in `~/.config/Rocket.Chat (development)/config.json` between scenario phases. Kill RC, rewrite config, relaunch. Skip the Settings UI entirely.

Result: 5 PR3325 scenarios went 0/5 → 5/5 green. Scenario files shrank from 1488 → 695 lines.

## Rule
For mOSdat scenarios that toggle persisted RC state, write `config.json` directly. Reserve VLM `localize` for transient runtime UI (modals, dial pad) — never for navigating stable Settings UI.

Relevant PersistableValues_4_14_0 keys:
- `isTelephonyEnabled: boolean`
- `telephonyPreferredServer: string | null`
- `telephonyGlobalShortcutConfig: { enabled: boolean, accelerator: string | null }`
