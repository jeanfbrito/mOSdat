---
date: "2026-05-23"
project: mosdat
topic: 'SetUserFTA approach is blocked and would not fix the root cause anyway. Full diagnostic:'
kind: war-story
scope: project-shared
confidence: medium
---

---

SetUserFTA approach is blocked and would not fix the root cause anyway. Full diagnostic:

**Why SetUserFTA is blocked:**
- VMs have no internet (Invoke-WebRequest fails)
- Host wget/curl is blocked by CLAUDE.md hooks
- No local copy of SetUserFTA.zip exists
