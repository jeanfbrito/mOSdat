---
date: "2026-05-23"
project: mosdat
topic: Tools present. Sampler fails because no DISPLAY exported in SSH session. Real bug, not "tool unavailable". Verify with
kind: decision
scope: project-shared
confidence: low
---

Assistant: Tester claim suspicious — Stage 3a sampler "self-disabled" because wmctrl/xdotool missing. Wmctrl/xdotool live on the VM (POC used them via SSH). Verify directly.

Assistant: Tools present. Sampler fails because no DISPLAY exported in SSH session. Real bug, not "tool unavailable". Verify with DISPLAY.

Assistant: Strike 2 (tester sidestep). Real bug: Stage 3a sampler doesn't export DISPLAY/XAUTHORITY. Dispatch reviewer fix.

Assistant: Reviewer patched. Run live verify myself + check actual pytest baseline.
