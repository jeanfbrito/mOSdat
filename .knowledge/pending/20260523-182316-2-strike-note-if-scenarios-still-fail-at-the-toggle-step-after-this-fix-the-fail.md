---
date: "2026-05-23"
project: mosdat
topic: '2-strike note**: If scenarios still fail at the toggle step after this fix, the failure mode will be a VLM localize'
kind: war-story
scope: project-shared
confidence: medium
---

for f in /tmp/resweep-w{10,11}-*.summary; do echo "=== $f ==="; cat $f; echo; done
```

**2-strike note**: If scenarios still fail at the toggle step after this fix, the failure mode will be a VLM localize error (can't find the toggle visually), not `_invoke_action`. That would be a different root cause requiring prompt tuning, not further toggle-pattern iteration.</result>
<usage><total_tokens>110199</total_tokens><tool_uses>66</tool_uses><duration_ms>406816</duration_ms></usage>
</task-notification>

Assistant: Toggle pattern WORKED — tel-qa-002 advanced step 25→31. Failure now at picker-dialog wait. Sweep got killed when agent died. Restart detached.
