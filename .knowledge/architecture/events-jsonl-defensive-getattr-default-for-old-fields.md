---
date: "2026-05-17"
project: mosdat
topic: Old events.jsonl files (missing all new fields) render cleanly because all new field reads use `.get(field, default)`
kind: decision
scope: project-shared
confidence: low
---

- `shell_result()` is additive — `shell()` still returns `None` so all existing callers are unaffected. The step runner calls `shell_result()` directly after the existing `shell` emit, keeping the old `shell` event for backwards compat.
- `verify_with_meta()` duplicates the body of `verify()` rather than refactoring it — avoids touching the hot path used everywhere.
- Config snapshots are opt-in only (default off) — the SSH call costs ~50ms per shell step and would slow down every run unnecessarily.
- Old events.jsonl files (missing all new fields) render cleanly because all new field reads use `.get(field, default)`.

**Tests:** 16 in `tests/test_report_enhancements.py` — all pass. Smoke test: existing `results/functional/2026-05-16_190131_functional/ubuntu2204/report.html` regenerated successfully (37,997 bytes, no errors).</result>
<usage><total_tokens>113113</total_tokens><tool_uses>68</tool_uses><duration_ms>464009</duration_ms></usage>
</task-notification>
