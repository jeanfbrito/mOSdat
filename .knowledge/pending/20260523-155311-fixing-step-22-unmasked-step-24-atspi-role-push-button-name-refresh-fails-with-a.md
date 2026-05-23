---
date: "2026-05-23"
project: mosdat
topic: 'Fixing step 22 unmasked step 24: `atspi: {role: "push button", name: "Refresh"}` fails with `at_point` returning'
kind: decision
scope: project-shared
confidence: low
---

**windows11/tel-qa-004 — New blocker (not a re-strike):**

Fixing step 22 unmasked step 24: `atspi: {role: "push button", name: "Refresh"}` fails with `at_point` returning `Dialog` (the RC main window frame) at the button center. Same root cause as step 22 (pointer-mode `reject_mismatch` on a deeply-nested button). Fix is the same (`via: "action"`), but this is a new observation from sweep 3 — I did not attempt it because the brief's scope was step 22 specifically. The Planner should decide whether to add `via: "action"` to the Refresh and Copy diagnostics buttons in both windows11 files.

**windows11/tel-qa-010 — Different fail point:**

Sweep 3 shows fail at step 18 (`wait_for Got it prompt`) instead of step 32 (`isDefault.tel` label). This is earlier in the scenario — the Telephony toggle VLM-localize click did not register. This is a flaky VLM localize issue on windows11 that's unrelated to protocol registration. Distinct from the windows10 behavior.</result>
<usage><total_tokens>151020</total_tokens><tool_uses>103</tool_uses><duration_ms>1574148</duration_ms></usage>
