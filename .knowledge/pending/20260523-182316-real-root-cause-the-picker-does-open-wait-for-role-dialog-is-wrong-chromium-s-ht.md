---
date: "2026-05-23"
project: mosdat
topic: "\U0001F3AF **REAL root cause**: The picker DOES open. `wait_for {role: dialog}` is wrong — Chromium's HTML `<dialog>` maps"
kind: decision
scope: project-shared
confidence: low
---

<usage><total_tokens>98799</total_tokens><tool_uses>86</tool_uses><duration_ms>912568</duration_ms></usage>
</task-notification>

Assistant: 🎯 **REAL root cause**: The picker DOES open. `wait_for {role: dialog}` is wrong — Chromium's HTML `<dialog>` maps to UIA `Custom` or `Pane`, NOT `Dialog`. Scenarios timeout because the role search misses.

Trigger picker → dump UIA tree in 120s window → identify actual role → fix scenarios.

Assistant: Final fix dispatched: dump UIA tree while picker open → identify actual Chromium role → replace `dialog` wait_for across all 12 affected scenarios → re-sweep both VMs. ~30 min wall total. Expected: most/all 14 flip GREEN.
