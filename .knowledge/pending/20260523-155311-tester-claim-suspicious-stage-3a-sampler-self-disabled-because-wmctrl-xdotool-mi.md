---
date: "2026-05-23"
project: mosdat
topic: Tester claim suspicious — Stage 3a sampler "self-disabled" because wmctrl/xdotool missing. Wmctrl/xdotool live on the
kind: decision
scope: project-shared
confidence: low
---

<usage><total_tokens>71460</total_tokens><tool_uses>69</tool_uses><duration_ms>891634</duration_ms></usage>
</task-notification>

Assistant: Tester claim suspicious — Stage 3a sampler "self-disabled" because wmctrl/xdotool missing. Wmctrl/xdotool live on the VM (POC used them via SSH). Verify directly.

Assistant: Tools present. Sampler fails because no DISPLAY exported in SSH session. Real bug, not "tool unavailable". Verify with DISPLAY.

Assistant: Strike 2 (tester sidestep). Real bug: Stage 3a sampler doesn't export DISPLAY/XAUTHORITY. Dispatch reviewer fix.
