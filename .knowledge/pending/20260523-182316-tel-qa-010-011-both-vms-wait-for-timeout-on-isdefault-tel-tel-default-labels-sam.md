---
date: "2026-05-23"
project: mosdat
topic: '`tel-qa-010, 011` (both VMs) — `wait_for_timeout` on `isDefault.tel` / `tel: default` labels — same root cause'
kind: war-story
scope: project-shared
confidence: medium
---

- `tel-qa-003`, `tel-qa-008` — PASS on both VMs (4/20 PASS)
- `tel-qa-004` (both VMs) — `pointer-mode reject_mismatch` at step 22: UIA at-point returns `role='Static'` at the button center instead of the `push button`. The button's UIA path is empty in `get_at_point`, triggering the "OTHER widget" rejection. This is a UIA tree structure issue in the PR3325 diagnostics panel — the `push button` wraps a Static text child, and Chromium's hit-test returns the child, not the button. Fix: either use `via='action'` for that step, or relax the match to accept ancestor/parent matches when actual path is empty.
- `tel-qa-002, 006, 007, 013, 014` (both VMs) — `wait_for_timeout` on dialogs/combo-boxes that require the OS-level `tel:` deeplink handler to be registered as default. These VMs don't have RC as the default `tel:` handler yet. Not a transport or scenario logic bug.
- `tel-qa-010, 011` (both VMs) — `wait_for_timeout` on `isDefault.tel` / `tel: default` labels — same root cause.

**Key decision**: the retry loop in `_run_batch_daemon` resets `_daemon_setup_done=False` before calling `_ensure_daemon_setup` so the setup function doesn't short-circuit. This is correct because a dead tunnel means the daemon may also have been restarted and needs re-registration.</result>
<usage><total_tokens>72683</total_tokens><tool_uses>20</tool_uses><duration_ms>884364</duration_ms></usage>
</task-notification>
