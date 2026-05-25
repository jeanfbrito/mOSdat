---
date: "2026-05-23"
project: mosdat
topic: 7. **Windows VMs skip sampler** at the wiring layer (wmctrl/xdotool are Linux-only); kept this in the call site, not
kind: decision
scope: project-shared
confidence: low
---

4. **`timestamp_ns` always present when sampler enabled** (not gated on having a sample). Useful for sequencing even when window-state is unknown.
5. **Backward compat verified**: when feature is OFF, the line is `{"frame": ..., "ts": ...}` byte-identical to the historical schema — `_load_index_timestamps` already tolerates unknown keys, so old readers still work.
6. **Constructor coerces `record_window_state=True, ssh=None` → False** so callers passing the flag without an SSH handle silently degrade instead of NPE'ing.
7. **Windows VMs skip sampler** at the wiring layer (wmctrl/xdotool are Linux-only); kept this in the call site, not the recorder, because the recorder has no `vm` object.

**Performance assumption documented** in the new module docstring at top of `session_recorder.py` (10 lines): full-fps capture is preserved; window/cursor metadata lags by up to one sampler period (~200 ms); a future ControlMaster optimization can drop sampler latency further.

**Deferred:** nothing. Definition of Done met.</result>
<usage><total_tokens>128408</total_tokens><tool_uses>61</tool_uses><duration_ms>740331</duration_ms></usage>
