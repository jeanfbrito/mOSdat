---
date: "2026-05-23"
project: mosdat
topic: Root cause B (4 scenarios):** All downstream of A — dialogs requiring telephony to be enabled never appear because
kind: decision
scope: project-shared
confidence: low
---

**Root cause A (8 scenarios):** Windows UIA `_invoke_action` fails on the Fuselage `&lt;ToggleSwitch&gt;` (check box role) — same class of bug as the Linux AT-SPI known issue. The worker finds the element but the invoke/toggle pattern raises an exception. Fix: use probe-by-modal pattern (same as `3325-diagnostics-panel.yaml` on Linux) or fall back to VLM-localize click on the toggle track.

**Root cause B (4 scenarios):** All downstream of A — dialogs requiring telephony to be enabled never appear because the toggle can't be flipped.

**Key decision:** 2-strike rule applied per-scenario. Scenarios sharing the same root cause as an already-2-struck scenario (002) counted as one-strike (same failure, same widget, no ambiguity).</result>
<usage><total_tokens>75580</total_tokens><tool_uses>71</tool_uses><duration_ms>1271078</duration_ms></usage>
</task-notification>
