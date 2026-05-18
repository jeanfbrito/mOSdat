---
date: "2026-05-18"
project: mosdat
topic: Multiple pop+reimport of PIL.Image creates distinct instances — isinstance(crop, Image.Image) returns False
kind: insight
scope: project-shared
confidence: high
---

Even after fixing the stub-install chain, `test_runner_features.TestDiffClickVerify` still failed inside `composite.paste(crop_before, (0, 0))` with `ValueError: cannot determine region size; use 4-item box`.

Discovery: `sys.modules.pop("PIL.Image")` + later `import PIL.Image` creates a NEW module object — but existing name-bindings (other tests' `from PIL import Image as X`) still reference the OLD module. Each module instance has its own `Image` class. Inside PIL's paste implementation:

```python
if isinstance(im, Image.Image):  # uses THIS module's Image class
    ...
else:  # color-fill path, needs 4-tuple box
    ...
```

When `crop_before` came from one PIL instance and `composite` from another, isinstance was False → fell through to color-fill path → "needs 4-item box".

Takeaway: module identity is process-global. Pop+reimport patterns create copies; downstream cross-module isinstance silently switches semantics. The fix is to stop pop+reimport entirely on real library modules.
