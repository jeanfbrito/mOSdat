---
date: "2026-05-23"
project: mosdat
topic: 'Generic pattern proven**: UIA→VLM fallback works for Chromium''s stale-popover problem. The pattern is `atspi: {…}`'
kind: pattern
scope: project-shared
confidence: medium
---

- `/home/jean/projects/linux-testing/mOSdat/shared/scenarios/functional/windows10/tel-qa-013-localization-layout.yaml`
- `/home/jean/projects/linux-testing/mOSdat/.claude/mytasks/findings.md` (findings appended)

**Generic pattern proven**: UIA→VLM fallback works for Chromium's stale-popover problem. The pattern is `atspi: {…}` + `localize: "&lt;spatial-anchored description&gt;"`. The brief's Phase 1 hypothesis was already implemented in code; the unblock was purely scenario-level (add localize hint with correct spatial anchor).

**Blocker for full green**: PR 3325 win-x64 build must be installed on win10 before V&amp;V tab assertions can pass. Documented in findings under "Next session".</result>
<usage><total_tokens>84718</total_tokens><tool_uses>66</tool_uses><duration_ms>590296</duration_ms></usage>
</task-notification>
