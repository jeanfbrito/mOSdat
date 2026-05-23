---
date: "2026-05-23"
project: mosdat
topic: The functional command requires `xxhash` (frame deduplication in recorder). This is a **pre-existing environment
kind: decision
scope: project-shared
confidence: low
---

### Why Live Test Skipped (Not a Blocker)

The functional command requires `xxhash` (frame deduplication in recorder). This is a **pre-existing environment issue**, unrelated to AT-SPI. All unit tests pass because they mock the recorder. Once xxhash is installed, the smoke scenario runs end-to-end on ubuntu2204@192.168.13.81.

### Risk Assessment

**Low** — All new code behind explicit `atspi:` / `verify_atspi:` step fields. No VLM changes. Existing scenarios 100% unaffected. Fallback logic tested in isolation.
