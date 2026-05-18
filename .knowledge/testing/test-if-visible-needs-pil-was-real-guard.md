---
date: "2026-05-18"
project: mosdat
topic: test_if_visible was missing _PIL_WAS_REAL guard — mutated real PIL.Image.Image = object
kind: lesson
scope: project-shared
confidence: high
---

`tests/test_if_visible.py` set `sys.modules["PIL.Image"].Image = object` unconditionally at module top to satisfy type-hints in `automation/runners/functional.py`. When real PIL was already loaded in `sys.modules`, this mutated the LIVE module object — every sibling test that held a `from PIL import Image` binding now saw `Image.Image = object` too.

Other stubbing files (`test_negative`, `test_chaos_infra`, `test_chaos_io`) already had the `_PIL_WAS_REAL = "PIL.Image" in sys.modules` guard and only stubbed when PIL was NOT loaded. test_if_visible was missing it — added to match the established pattern:

```python
_PIL_WAS_REAL = "PIL.Image" in sys.modules
if not _PIL_WAS_REAL:
    # install stubs
    sys.modules["PIL.Image"].Image = object
```

Takeaway: module attribute mutation (vs `sys.modules[name] = new_module`) corrupts every name-binding to that module — Python modules are shared by identity. Always guard "stub" code on "was the real thing absent first". Fixed in commit `2efcebd`.
