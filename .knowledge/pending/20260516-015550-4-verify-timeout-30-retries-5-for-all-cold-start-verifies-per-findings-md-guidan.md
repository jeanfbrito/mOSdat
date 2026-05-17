---
date: "2026-05-16"
project: mosdat
topic: '4. **`verify_timeout: 30, retries: 5`** for all cold-start verifies per findings.md guidance ("For launch:'
kind: decision
scope: project-shared
confidence: low
---

3. **Negative assertion** — `nohup &lt;binary&gt; &lt;url&gt;` always launches the binary, so the negative test asserts behavioral ignoring (no dial pad, no modal, normal login page) rather than "RC does not start." Matching what the PR actually gates: when toggle OFF, `setAsDefaultProtocolClient` was never called and the deep-link handler is unregistered — the URL arg is simply dropped.

4. **`verify_timeout: 30, retries: 5`** for all cold-start verifies per findings.md guidance ("For launch: `verify_timeout: 25-30 + retries: 0` (deterministic)" note overridden here with retries: 5 because cold start on QEMU has high variance, not because the outcome is nondeterministic).

5. **Assumption on Redux key** — Not embedded in config.json directly. The pre-flight approach means we never need to know the exact field name (`isTelephonyEnabled` or similar). If a future scenario needs config.json pre-population, the key would need verification from `src/app/PersistableValues.ts` in the PR diff.</result>
<usage><total_tokens>42535</total_tokens><tool_uses>7</tool_uses><duration_ms>136515</duration_ms></usage>
</task-notification>
