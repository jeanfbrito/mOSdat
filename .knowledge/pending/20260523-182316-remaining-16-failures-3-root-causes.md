---
date: "2026-05-23"
project: mosdat
topic: 'Remaining 16 failures, 3 root causes**:'
kind: war-story
scope: project-shared
confidence: medium
---

- W10: tel-qa-001, 003, 005, 008
- W11: tel-qa-001, 003, 005, 008

**Remaining 16 failures, 3 root causes**:

| Cat | Scenarios | Cause | Fix |
|---|---|---|---|
| A | tel-qa-004 ×2 | UIA hit-test returns Static text child instead of push button | `via: action` for that step |
| B | tel-qa-002/006/007/013/014 ×2 = 10 | wait_for dialog requires OS tel: default handler not yet registered | Pre-register RC as tel: handler in Windows registry via pre-stage step |
