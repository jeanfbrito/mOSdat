---
date: "2026-05-23"
project: mosdat
topic: Root cause:** `TELEPHONY_SERVER_SELECT_OPEN` dispatches correctly and IS forwarded to 2 live (non-destroyed) renderers.
kind: war-story
scope: project-shared
confidence: medium
---

[DIAG-DP] finally block, inProgress reset
```

**Root cause:** `TELEPHONY_SERVER_SELECT_OPEN` dispatches correctly and IS forwarded to 2 live (non-destroyed) renderers. The main process then waits 120s for `TELEPHONY_SERVER_SELECT_CLOSE`. The picker modal (`TelephonyServerSelectModal`) is in `rootWindow-BVGdJM48.js` and renders via `dialogs.telephonyServerSelect.isOpen`. The 120s timeout fires (no user/automation interaction), returning `null`. The dialpad never opens.

**The failure is at the renderer level** — the `TelephonyServerSelectModal` receives the Redux action but the HTML `&lt;dialog&gt;` element's `showModal()` result is either:
- Not visible in the VNC framebuffer (rendering in a non-foreground window context), OR  
- Not exposed as `role=dialog` in the UIA/AT-SPI tree (Chromium's HTML `&lt;dialog&gt;` → UIA role mapping)
