---
date: "2026-05-23"
project: mosdat
topic: 'Conclusion: `isTelephonyEnabled=false` gating was NOT the root cause of picker not appearing. RC processes past the'
kind: war-story
scope: project-shared
confidence: medium
---

**2-STRIKE HALT — Live sweep result:**
- Strike 2 failed: All 12 scenarios still FAIL with same `wait_for_timeout` pattern
- Execution confirmed: persist step ran at correct position (step count shifted by insert count), dispatch ran, but RC picker/dialog did not appear
- Conclusion: `isTelephonyEnabled=false` gating was NOT the root cause of picker not appearing. RC processes past the telephony gate with the fix applied, but the workspace picker modal is still not rendered after the deeplink arrives
- Root constraint unknown — the Auditor must trace the RC `processDeepLink` → `openTelephonyDialpad` → picker render path to find why the picker does not surface even when telephony is enabled and the deeplink is correctly dispatched</result>
<usage><total_tokens>132465</total_tokens><tool_uses>50</tool_uses><duration_ms>1005279</duration_ms></usage>
</task-notification>

Assistant: Two-strike halt on the fix. Even with `isTelephonyEnabled=true` written to disk, picker doesn't render. **The wall is `openTelephonyDialpad` itself, not the gate**.
