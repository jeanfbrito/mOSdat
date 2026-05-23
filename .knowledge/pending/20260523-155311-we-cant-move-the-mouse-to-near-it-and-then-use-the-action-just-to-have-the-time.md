---
date: "2026-05-23"
project: mosdat
topic: we cant move the mouse to near it and then use the action? just to have the time and the mouse action, so the watcher
kind: decision
scope: project-shared
confidence: low
---

Want me to dispatch?

User: we cant move the mouse to near it and then use the action? just to have the time and the mouse action, so the watcher of the video will understand what happened, because now it just toggles too fast and then the popup appears from nothing, at least moving the mouse will give time and understanding to the reviewer human

Assistant: Smart. Visual context for the reviewer is the actual goal. Implement: new `via: hint` mode — cursor moves to widget area, dwells, THEN action fires. Reviewer sees the cursor's intent before the state change.

Assistant: Builder running. Will: worker exposes `clickable_extents` (ancestor walk for zero-extent widgets) → client `_click_via_hint` (move + dwell 300ms + do_action) → swap tel-qa-002 + 3325-diagnostics-panel toggle to `via: hint` → live verify cursor visible before state change. Notify on complete.
