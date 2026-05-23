---
date: "2026-05-23"
project: mosdat
topic: 'Win10 sweep done. **2/12 GREEN** (tel-qa-001 + tel-qa-005), 10 blocked on TWO clean root causes:'
kind: war-story
scope: project-shared
confidence: medium
---

<usage><total_tokens>75580</total_tokens><tool_uses>71</tool_uses><duration_ms>1271078</duration_ms></usage>
</task-notification>

Assistant: Win10 sweep done. **2/12 GREEN** (tel-qa-001 + tel-qa-005), 10 blocked on TWO clean root causes:

- **A (8 scenarios)**: UIA `_invoke_action` fails on Fuselage ToggleSwitch (`<check box>`). Same class of bug as Linux AT-SPI epic — we solved it there with `via: hint` (cursor motion + dwell + action). Windows worker has hint mode but scenarios may not use it for the toggle.

- **B (4 scenarios)**: downstream of A. No telephony enabled → dialogs never appear.
