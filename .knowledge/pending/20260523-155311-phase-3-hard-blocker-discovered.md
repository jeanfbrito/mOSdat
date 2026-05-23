---
date: "2026-05-23"
project: mosdat
topic: Phase 3 — Hard blocker discovered**
kind: insight
scope: project-shared
confidence: medium
---

3. **psutil installed** on win10 VM (process-name fallback in `_matches_app` was dead).
4. **schtasks InteractiveToken launch** in `launch-rocketchat.yaml`: SSH runs in Session 0, GUI in Session 1. After fix, RC confirmed running in SessionId=1.

**Phase 3 — Hard blocker discovered**
`pywinauto.Desktop(backend='uia').windows()` returns 0 from Session 0 even with RC running in Session 1. UIA is bound to caller's window station. The UIA worker (`automation/uia/client.py::run_batch`) is invoked via plain `ssh.run` → Session 0 → cannot enumerate Session 1 windows. ALL `wait_for`/`find`/UIA ops fail with `app_not_found`.

**Phase 4 — Scenario results**

| Scenario | Result |
