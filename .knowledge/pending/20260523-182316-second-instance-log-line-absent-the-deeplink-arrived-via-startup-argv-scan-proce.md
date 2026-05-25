---
date: "2026-05-23"
project: mosdat
topic: '`second-instance` log line absent — the deeplink arrived via startup argv scan (`processDeepLinksInArgs`), not the'
kind: war-story
scope: project-shared
confidence: medium
---

## Deferred

- `second-instance` log line absent — the deeplink arrived via startup argv scan (`processDeepLinksInArgs`), not the running-instance IPC path. This is expected if the second `Start-Process` invocation launched before the first instance acquired the single-instance lock. Does not change the root cause diagnosis.
- Instrumentation left in place on disk per brief constraints. Not committed or pushed.</result>
<usage><total_tokens>95277</total_tokens><tool_uses>71</tool_uses><duration_ms>545090</duration_ms></usage>
</task-notification>

Assistant: 🎯 **BREAKTHROUGH!** Bug is NOT in RC. It's our scenario pre-staging.
