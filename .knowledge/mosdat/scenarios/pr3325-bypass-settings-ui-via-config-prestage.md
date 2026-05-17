---
date: "2026-05-16"
project: mOSdat
tags:
  - mosdat
  - scenarios
  - electron
  - rocketchat
  - vlm
topic: PR3325 telephony scenarios — bypass in-app Settings UI by pre-staging Redux state in config.json
kind: war-story
scope: project-shared
category: mosdat/scenarios
confidence: high
---

## War story (2026-05-16 PR3325)
Spent 6 rerun cycles trying to drive Rocket.Chat's Settings panel via VLM `localize` clicks (sidebar kebab → Settings item), VNC `key: "ctrl+,"`, and menu nav (`alt+w; Down; Return`). All flaky:
- RC Electron has no `Ctrl+,` accelerator on Linux (menuBar.ts registers Settings menu item without `accelerator:` field).
- Sidebar kebab popup is transient; VLM hallucinates click coords.
- `alt+w` swallowed by webview when login form holds focus.

## Fix
Pre-stage all telephony Redux state directly in `~/.config/Rocket.Chat (development)/config.json` between scenario phases. Kill RC, rewrite config, relaunch. Skip Settings UI entirely. Reduced 5 PR3325 scenarios from 1488 → 695 lines and went 0/5 → 5/5 green.

## Rule
For mOSdat scenarios that toggle persisted RC state, write `config.json` directly. Reserve VLM `localize` for transient runtime UI (modals, dial pad) — never for navigation through stable Settings UI.

Related keys per PR3325 PersistableValues_4_14_0:
- `isTelephonyEnabled: boolean`
- `telephonyPreferredServer: string | null`
- `telephonyGlobalShortcutConfig: { enabled: boolean, accelerator: string | null }`
