---
date: "2026-05-23"
project: mosdat
topic: For malformed input dispatches (014 steps 3e, 4a), inserted `$true` because telephony IS enabled in that scenario part
kind: decision
scope: project-shared
confidence: low
---

**Key decisions:**
- Used text-based insertion (not ruamel.yaml rewrite) to preserve all existing YAML formatting — diff is pure additions only (798 lines added, 0 deleted across 12 files)
- Inserted `$false` persist step before OFF-expected dispatches (steps1e/4a in 002, step1e in 014) — not just `$true` everywhere, per brief
- For malformed input dispatches (014 steps 3e, 4a), inserted `$true` because telephony IS enabled in that scenario part

**Tests added/updated:** None — existing pytest suite (1233 passed, 5 skipped, 3 xfailed) unchanged.

**2-STRIKE HALT — Live sweep result:**
- Strike 2 failed: All 12 scenarios still FAIL with same `wait_for_timeout` pattern
