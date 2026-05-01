---
date: "2026-05-01"
project: mOSdat
tags:
  - mosdat
  - pyproject
  - dependencies
  - imports
topic: 'mosdat: pyproject base deps include pycryptodome, pillow, openai, pyyaml, websockets'
kind: lesson
scope: project-shared
category: packaging
confidence: high
accessed: 23
last_accessed: "2026-05-01"
---

## Lesson
After submodule reorg (a6be353), `python -c "import automation.runners.smoke"` raised `ModuleNotFoundError: No module named 'Crypto'`. Five deps had been in `[project.optional-dependencies.functional]` but were transitively imported via `automation.transport`'s package init.

## Fix (commit 4c63d4b)
Promoted to base `[project.dependencies]`:
- `pycryptodome` (RFB password DES via `transport/vnc.py`)
- `pillow` (`vlm/screenshot.py`)
- `openai` (`vlm/client.py`)
- `pyyaml` (functional step parsing)
- `websockets` (`transport/vnc.py`)

All five reach the CLI through `automation.transport` and `automation.vlm` package init re-exports.

## Verification after change
```
pip install -e .
python -c "import automation.{proxmox,transport,runners,vlm,reporting}"
mosdat --help
pytest tests/test_if_visible.py
```
All pass.

## Takeaway
mosdat has no truly optional deps right now — everything goes through package init. If lazy-loading is added later, deps can move back to extras.
