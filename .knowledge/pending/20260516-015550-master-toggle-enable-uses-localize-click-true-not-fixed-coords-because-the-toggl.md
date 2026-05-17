---
date: "2026-05-16"
project: mosdat
topic: 'Master toggle enable uses `localize:` + `click: true` (not fixed coords) because the toggle position depends on'
kind: decision
scope: project-shared
confidence: low
---

- Phase B (single server): steps B1a–B8 — 28 steps covering single-server config, dropdown absent, shortcut row present, tel: dispatch with no modal.

Key decisions:
- Master toggle enable uses `localize:` + `click: true` (not fixed coords) because the toggle position depends on Settings panel scroll state.
- Telephony Server dropdown in A8a uses `localize: "... showing 'Mobile RC' as the current selection"` so VLM can identify it by current value — robust to layout shifts.
- B4 uses `verify_not:` with an explicit description of what the dropdown would look like if erroneously present — gives VLM a clear false-positive target to reject.
- B7b accepts either dial pad OR healthy Workspace page — server-side telephony may not be provisioned in CI, so insisting on dial pad would create a flaky assertion.
- Phase B cleanup wipes userData at the end to prevent state leaking into subsequent scenarios.
