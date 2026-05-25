---
date: "2026-05-23"
project: mosdat
topic: 'Actual root cause** (from `6b6b733` commit message, confirmed by registry state): RC''s main-process `second-instance`'
kind: war-story
scope: project-shared
confidence: medium
---

**But**: The current scenario (commit `6b6b733`) already bypasses OS protocol dispatch entirely — step2a calls `Rocket.Chat.exe "tel:+15551234567"` directly. The commit message states this also failed: "protocol/dialog never reaches RC's surface even with direct argv." SetUserFTA fixes OS dispatch, which is already bypassed.

**Actual root cause** (from `6b6b733` commit message, confirmed by registry state): RC's main-process `second-instance` IPC handler doesn't surface the workspace picker when Settings is already open. This is an RC application behavior, not an OS routing issue.

**Resweep4 failure context**: The 5-second scenario runs in resweep4 were schema validation failures (pydantic `extra_forbidden` on uncommitted `if_visible` blocks that weren't yet valid per the schema at that moment). These errors are now moot — the current HEAD has both valid schemas and committed `if_visible` blocks, and local validation confirms both pass.

**Current score**: 10/24 GREEN (tel-qa-001, 003, 004, 005, 008 × both OSes). 14/24 blocked on RC application behavior requiring interactive Spy++/AccessibilityInsights debugging on the VM to diagnose what RC does (or doesn't do) when receiving `tel:` URLs via second-instance IPC.
