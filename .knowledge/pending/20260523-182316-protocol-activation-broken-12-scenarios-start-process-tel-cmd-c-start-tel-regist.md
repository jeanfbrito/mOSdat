---
date: "2026-05-23"
project: mosdat
topic: 'Protocol activation broken** (12 scenarios): `Start-Process tel:`, `cmd /c start tel:`, registry pre-reg,'
kind: war-story
scope: project-shared
confidence: medium
---

### Remaining 14 blockers — real Windows arcana, beyond mosdat scope

**Protocol activation broken** (12 scenarios): `Start-Process tel:`, `cmd /c start tel:`, registry pre-reg, SHChangeNotify, picker-dismissal — none route to RC. Root cause needs:
- RC source investigation: single-instance IPC may drop deeplinks when Settings is the active view
- OR SetUserFTA / native admin Windows tools to bypass UserChoice hash anti-tamper
- OR Process Monitor session to trace where the URL goes

**tel-qa-013 widget timing** (2 scenarios): Preferred Server widget not in UIA tree even after ON→OFF→ON toggle. Needs interactive UI inspector to identify actual role+name or render condition.
