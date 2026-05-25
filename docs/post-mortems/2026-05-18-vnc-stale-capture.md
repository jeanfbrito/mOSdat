# Post-mortem: VNC stale-framebuffer race masquerading as VLM flake

**Date:** 2026-05-18
**Severity:** High — caused systemic mis-clicks across all functional scenarios.
**Resolution:** Two-line protocol fix + timing defaults bumped.

## TL;DR

`VncClient.capture()` was returning screenshots 1–N frames behind real screen state because the RFB reader consumed stale buffered bytes instead of the response to the new `FramebufferUpdateRequest`. Every post-click popup was invisible to the VLM. We chased prompt quality for half the session before noticing the captured screenshot literally did not contain the UI the user was watching live. Fix: drain stale buffer before every `FBUR`, read exactly one `FramebufferUpdate` message, done.

## Symptom

Open a popup via kebab click. The next `capture()` call should show the popup. Instead it returns the pre-click frame. VLM can't localize an element that isn't in the image, so it falls back to the nearest-looking thing — usually the trigger element itself. Three retries at 10 s each, step fails. On VNC viewer the popup is clearly visible the whole time.

Reference run `2026-05-18_093407`: click at 09:35:06 hit kebab at (185, 692). Popup appeared on VM. Capture at 09:35:14 returned the pre-popup frame. VLM re-localized the kebab and returned (197, 692). Step failed 3×.

## Investigation timeline

- **First hypothesis: prompt quality.** Run logged `feedback_routine_localize_prompts.md`. The prompt `"the 'Settings' ... menu item in the popup that just opened from the sidebar kebab button"` contained three prompt-quality violations: temporal reference ("just opened"), trigger named in target prompt ("kebab button"), and a disjunction. That lesson is real and was saved as a reusable rule. It was just at the wrong layer — no prompt rewording can localize an element that isn't in the frame.

- **Second look: compare capture vs live VNC.** Captured screenshot at 09:35:14 showed the pre-click sidebar. Live VNC viewer showed the popup. The frames were definitively different. Prompt quality ruled out; input pipeline under investigation.

- **Auditor dispatched.** Confirmed two compounding bugs in `_grab_framebuffer()` (pre-fix line 441). Stale buffer leak plus wrong completion gate. See findings entry "VNC stale-framebuffer race (auditor-confirmed)".

- **Fix scoped to `automation/transport/vnc.py`.** Drain + single-message read. Implemented same session.

- **Live re-run.** Post-fix, Settings step clicked (284, 671) — the actual Settings row. Prior failed run had clicked (197, 692) — the kebab. PASS on first attempt.

- **Complementary timing.** Protocol fix alone was necessary but not sufficient. `cursor duration_ms` was 150 — visually indistinguishable from teleport and too fast for interfaces mid-render. `hover_dwell_ms` was 0 — instant press on arrival. Bumped to 1000 ms / 250 ms respectively. Added 1 s post-action settle inside `click()` / `type_text()` / `key()` (opt-out via `settle=False`).

## Root cause (technical)

Two bugs compounded. First: between `capture()` calls the QEMU VNC server emits unsolicited `FramebufferUpdate` messages for cursor moves and dirty regions. These accumulate in `_Reader._buf` and any pending WebSocket frames. When `capture()` sent a new `FramebufferUpdateRequest`, `_grab_framebuffer()` read from `_reader` immediately — consuming the stale queued bytes as if they were the fresh response. If those stale bytes satisfied the completion gate, the loop exited with old pixel data; the actual response to the new request sat buffered for the following call.

Second: the completion gate on pre-fix line 441 was `while painted < target` where `target = W * H`. QEMU frequently sends overlapping rectangles (redrawing the same region twice in one update). Each rectangle added `w * h` to `painted` regardless of overlap, so the gate could fire after consuming only a subset of the message's rectangles. Late-arriving small rects — exactly the kind a popup produces — were dropped.

The timing dimension was separate: even with correct frames, actions completing in 150 ms gave widgets no time to settle hover/focus state. The cursor arrived and clicked before the target element finished rendering its interactive state.

## What we shipped

- **`0436e51`** — Recording: xxh3_64 pixel hash dedupe + 1s replay hold cap (same session; unrelated pipeline cleanup).
- **`5b24a5a`** — VNC: drain stale framebuffer + read exactly one FB update per request. Core fix. Adds `tests/test_vnc_capture.py` with three regression tests.
- **`97f64b5`** — Cursor: visible motion defaults (duration 1000ms, hover dwell 250ms). Complementary timing fix.

## Lessons

1. **Always inspect the actual captured input before debating model behavior.** VLM can't localize what isn't in the frame. Prompt tuning is downstream of a corrupt input pipeline.
2. **"Instant" is a smell in real-time pipelines.** UIs have render cycles. Distributed transports have latency. Match the machine's pace to the system you're driving.
3. **2-strike rule paid off.** After two failed attempts (different prompts, different scenarios) we stopped and dispatched the auditor. That reframe (prompt-quality → pipeline) was the unlock.
4. **Cheap waits beat expensive retries.** 2 s of strategic settle delay saves 30 s of VLM retry cycles when the input pipeline is solid.

## Open questions

- Does QEMU sometimes split a single `FramebufferUpdate` message across multiple WebSocket frames? The fix reads one RFB message with `n_rects` rectangles; if a partial-frame response surfaces in integration, we'd need a bounded retry loop rather than the current single-read path.
- `MOSDAT_VNC_SETTLE_MS` is configurable via env var. Should it also be a key in `[vnc]` within the TOML config so per-VM overrides don't require env injection?
- Parallel pytest under `-n auto` has one module-import race (unrelated to this fix). Worth investigating before enabling by default.

## References

- Auditor diagnosis is summarized in this post-mortem; the original `.claude/mytasks/findings.md` scratch file is intentionally not tracked.
- `docs/KNOWN_ISSUES.md` — "RFB capture used pixel-count completion gate (fixed)" RESOLVED entry.
- Memory: `feedback_routine_localize_prompts.md` — still valid for popup prompt authoring; not the proximate cause here.
- Commits: `0436e51`, `5b24a5a`, `97f64b5`.

## Postscript: the sister incident (same day)

After the VNC capture fix landed, `3325-master-toggle` kept failing at the modal-verify step. Three more commits attributed the failure to three wrong causes in sequence:

1. **PR3325 PersistableValues SIGTRAP** — a builder claimed `isTelephonyEnabled` was missing from RC's Redux-persist allowlist, that pre-staging the field caused a SIGTRAP. Commit `7bd9b52` shipped with that claim asserted in the message.
2. **config.json vs `overridden-settings.json`** — a follow-up corrected the previous diagnosis: "top-level config.json writes are silently ignored by Redux-persist; the canonical mechanism is `overridden-settings.json`". Routine rewritten; UI workaround removed. Still failed.
3. **Bool serialization in mosdat's jinja layer** — the actual cause. `automation/runners/var_subst.py:85` coerced Python bool inputs via `str(v)` → `'True'` (capital T) → jinja `tojson` → `'"True"'` (JSON-quoted) → downstream `sys.argv[1] == 'true'` always returned False. Every scenario passing `telephony_enabled: true` had been writing `isTelephonyEnabled: false`. One-line carve-out in the `isinstance` filter fixed it. `3325-master-toggle` now passes 32/32.

Verification of (1): PR3325 source DOES include `isTelephonyEnabled` in `PersistableValues_4_14_0` with a proper migration. RC logs show no SIGTRAP. Crashpad dumps empty. The diagnosis was fabricated.

Lesson — captured as `feedback_verify_diagnosis_before_commit.md` in project memory: subagent root-cause reports are hypotheses, not findings. Before writing a commit message that asserts a specific upstream symbol / crash / protocol behavior, grep the actual source. The orchestrator's job is to verify, not relay. Two wrong diagnostic layers stacked on top of each other; both shipped before the truth surfaced.

Sister-incident commits: `7bd9b52` (wrong diagnosis), the overridden-settings.json rewrite (right pattern, didn't unblock), `58e802d` (actual fix).
