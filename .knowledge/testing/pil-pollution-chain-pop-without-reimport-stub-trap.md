---
date: "2026-05-18"
project: mosdat
topic: PIL pollution chain across 5 test files broke 13 tests in full-suite only
kind: war-story
scope: project-shared
confidence: high
---

Full pytest suite had 13 failures that all passed in isolation. Tests failed with `AttributeError: module 'PIL.Image' has no attribute 'new'` and `ValueError: cannot determine region size; use 4-item box`.

Three-file chain:
1. `test_build_cmd / test_doctor / test_inject_config / test_replay / test_x11_preamble` popped every `PIL*` entry from `sys.modules` at their module top.
2. `test_chaos_infra` collected next, saw PIL.Image absent, INSTALLED a `types.ModuleType("PIL.Image")` stub (with `Image = object`).
3. `test_cursor_motion_integration / test_runner_features` bound their local `Image` via `from PIL import Image` → STUB. `Image.new` did not exist.

Fix: drop `or _name.startswith("PIL")` from the 5 pop blocks. Real PIL lives in the venv; it never needs to be popped or stubbed. Also add `_PIL_WAS_REAL` guard to `test_if_visible` (was missing while `test_negative` already had it).

Takeaway: NEVER pop a real library module from `sys.modules` unless immediately re-imported. The window between pop and re-import is when a sibling can install a destructive stub. Fixed in commit `2efcebd`.
