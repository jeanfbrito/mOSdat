---
date: "2026-05-23"
project: mosdat
topic: That's ~24x speedup on subsequent calls — matches the brief's ~1s -&gt; ~50ms target (we beat it; saw ~17ms because
kind: decision
scope: project-shared
confidence: low
---

- Non-persistent (3 calls): 430ms, 405ms, 403ms.
- Persistent (3 calls): 417ms (master open), 17ms, 16ms.

That's ~24x speedup on subsequent calls — matches the brief's ~1s -&gt; ~50ms target (we beat it; saw ~17ms because the VM is on a low-latency LAN).

### Nothing deferred. No 2-strike events.</result>
<usage><total_tokens>94601</total_tokens><tool_uses>60</tool_uses><duration_ms>914213</duration_ms></usage>
</task-notification>
