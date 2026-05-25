---
date: "2026-05-23"
project: mosdat
topic: 'Targeted attack on actual root cause: close Settings (Escape) before dispatch so RC IPC fires the picker. Sonnet'
kind: war-story
scope: project-shared
confidence: medium
---

Assistant: CRITICAL INSIGHT from diagnostic: RC's second-instance IPC handler doesn't surface workspace picker when **Settings is already open**. Scenarios keep Settings open through dispatch → picker suppressed. **Fix**: close Settings (Escape) before tel: dispatch.

Assistant: Targeted attack on actual root cause: close Settings (Escape) before dispatch so RC IPC fires the picker. Sonnet running; systemd-run sweep + poll. ~25 min. Real shot — first attempt targeting the actual application behavior instead of OS routing.

User: 25 minutes is terrible, this is not acceptable, ultrathink to review whats wrong

Assistant: Real review:
