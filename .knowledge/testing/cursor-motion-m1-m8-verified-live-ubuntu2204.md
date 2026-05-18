---
date: "2026-05-18"
project: mosdat
topic: M1-M8 human-like cursor motion verified live on ubuntu2204 GNOME-X11
kind: insight
scope: project-shared
confidence: high
---

M1-M8 bezier cursor motion + `dwell_ms` was shipped in commit `38440e8` but never exercised end-to-end on a live VM until this session. Two minimal test scenarios were authored to exercise it:

- `shared/scenarios/functional/test-cursor-motion.yaml` — single localize+click+dwell_ms=250 on RC email input field. Smallest possible M1-M8 exercise. PASS.
- `shared/scenarios/functional/test-kebab-mouse.yaml` — mouse-only path to open Settings via sidebar kebab (forces fallback path of `open-settings` routine, bypassing alt+w primary). PASS — visual proof via session.gif recording.

Click event → step_end gap = 1175 ms for a single click step with dwell_ms=250 and motion=bezier (default). That's bezier path travel + 250 ms dwell. Instant+no-dwell would be ~0 ms. Confirms motion executed end-to-end.

Takeaway: cursor motion is a CLICK-PATH feature, not a routine feature. Any `localize: ... click:` step exercises it automatically. To isolate-test motion without hitting routine-engine bugs, use raw click steps not routine wrappers.
