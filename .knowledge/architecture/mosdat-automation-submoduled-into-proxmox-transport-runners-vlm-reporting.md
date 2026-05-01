---
date: "2026-05-01"
project: mOSdat
tags:
  - mosdat
  - python
  - refactor
  - submodules
topic: 'mosdat: automation/ submoduled into proxmox/transport/runners/vlm/reporting'
kind: decision
scope: project-shared
category: architecture
confidence: high
accessed: 38
last_accessed: "2026-05-01"
---

## Decision (2026-04-30, commit a6be353)
Flat `automation/` (15 files) reorganized into themed submodules. Public API preserved.

```
automation/
├── main.py + config.py + state.py     ← unchanged top level
├── proxmox/  {api,vm,gpu}.py
├── transport/{ssh,vnc}.py             ← vnc_client.py renamed → vnc.py
├── runners/  {smoke,functional}.py    ← runner.py → smoke.py; functional_runner.py → functional.py
├── vlm/      {client,input,screenshot}.py
└── reporting/{report,aggregate}.py
```

Each subpackage `__init__.py` re-exports primary symbols so `from automation.proxmox import ProxmoxClient` works.

## Migration impact
- 12 files moved via `git mv` (history preserved)
- 17 imports updated across 9 files
- `tests/test_if_visible.py` broke due to `spec_from_file_location` absolute path — patched in 552a52e
- All 5 deps had to move to base (see related "pyproject base deps" entry)

## Public CLI unchanged
`python -m automation.main run|test|functional` still works. Post-pyproject: `mosdat <cmd>`.

## Takeaway
Submodule reorg = git mv + update imports + audit __init__.py re-exports + audit dynamic loaders + check optional deps survive. Five-step checklist.
