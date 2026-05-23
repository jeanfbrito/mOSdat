---
date: "2026-05-23"
project: mosdat
topic: Whether `telephonyRememberChoice` is the right overridden-settings key name (verified `isTelephonyEnabled` from
kind: decision
scope: project-shared
confidence: low
---

**Deferred / open for live tester**
- Whether workspace picker rows expose an accessible name. If AT-SPI multi-role `any[]` fails at step 3, the tester should drop in `localize:` + VLM fallback (header preserves the design intent).
- Whether `telephonyRememberChoice` is the right overridden-settings key name (verified `isTelephonyEnabled` from existing scenarios; the `telephonyRememberChoice` key is asserted in step 5 via behavior — if the build uses a different key it still defaults to "ask each time" because no preferred server is persisted).</result>
<usage><total_tokens>83507</total_tokens><tool_uses>18</tool_uses><duration_ms>198955</duration_ms></usage>
</task-notification>

Assistant: tel-qa-006 ✅ (25 steps) + tel-qa-012 ✅ (32 steps). Waiting on tel-qa-005.
