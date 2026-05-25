---
date: "2026-05-23"
project: mosdat
topic: BOM bug discovered + fixed across all scenarios + new regression test
kind: insight
scope: project-shared
confidence: medium
---

- Windows infrastructure: VM provisioning + Python + pywinauto + VC++ all installed on win10
- UIA driver (`automation/uia/`): worker + client + Session 0/1 fix + role normalization + verify-skip + visibility filter + IsOffscreen telemetry — 6 architectural fixes shipped
- 12 windows10 scenarios authored (498 steps)
- BOM bug discovered + fixed across all scenarios + new regression test
- per-OS routine library (cleanup-rocketchat + launch-rocketchat for Windows)
- per-OS scenario subdir resolver (linux/windows10/windows11 routing)
- 1208 pytest passing (+70 since session start)
- tel-qa-001 reaches step 10/20 reliably on live win10
