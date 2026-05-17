---
date: "2026-05-16"
project: mosdat
topic: Fixed shared root cause
kind: war-story
scope: project-shared
confidence: medium
---

Assistant: Five scenarios validate. Rerunning tests with qwen model after shortcut fix.

Assistant: Fixed shared root cause.

- Patched `ctrl+comma` → native `key: "ctrl+,"`.
- Replaced flaky `xdotool key ctrl+comma` Settings opens in five new 3325 scenarios.
- Patched templates + `automation/draft.py` so future generated scenarios use valid syntax.
- Validated all five scenarios: OK.
