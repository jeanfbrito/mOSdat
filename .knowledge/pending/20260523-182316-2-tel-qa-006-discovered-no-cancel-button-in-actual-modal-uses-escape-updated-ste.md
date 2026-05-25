---
date: "2026-05-23"
project: mosdat
topic: '2. tel-qa-006: discovered no Cancel button in actual modal — uses Escape; updated step4b/4c to assert picker-closed'
kind: insight
scope: project-shared
confidence: medium
---

**Key decisions**:
1. tel-qa-012: chose `wait_for any:` (brief option 1) since the preceding wait_for already asserted with longer timeout — short re-check matches presence intent.
2. tel-qa-006: discovered no Cancel button in actual modal — uses Escape; updated step4b/4c to assert picker-closed (deterministic) rather than dialpad-rendered (auth-dependent).
3. tel-qa-005: dialpad requires authenticated workspace which test env can't guarantee. Replaced VLM dialpad-with-number checks with the QA flow's core invariant — "single-workspace = no picker shown" — verified via AT-SPI absence-check. Aggregate step6 also converted to AT-SPI (frame alive + no picker).

**Deferred / not done**:
- Did not run any functional scenario (tester does serial re-verify per brief).
- Trade-off in tel-qa-005: the original "dialpad shows normalized number" assertion is no longer made. It was the unverifiable assertion in this test env. All other QA-005 invariants retained.
