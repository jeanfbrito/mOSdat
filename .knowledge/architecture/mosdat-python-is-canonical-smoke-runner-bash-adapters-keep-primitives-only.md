---
date: "2026-05-01"
project: mOSdat
tags:
  - mosdat
  - architecture
  - runner
  - decision
topic: 'mosdat: Python is canonical smoke runner, bash adapters keep primitives only'
kind: decision
scope: project-shared
category: architecture
confidence: high
accessed: 21
last_accessed: "2026-05-01"
---

## Decision (2026-04-30)
mosdat had THREE smoke runners coexisting:
- bash `os/<distro>/full-test.sh` (matrix orchestrator)
- bash `os/<distro>/test.sh` (single-test runner)
- python `automation/runner.py` (newer, with state.py + resumable runs)

Picked Python as canonical. Reasons:
- Already owns `automation/functional_runner.py` (VLM)
- Has `state.py` resumability across crashes
- Uses VNC/RFB channel via `transport/vnc.py` (bash only had SSH)

## What's kept
- bash `os/<distro>/build.sh`, `deploy.sh`, `gpu-control.sh`, `config.sh` — primitives invoked from Python
- python `automation/runners/{smoke,functional}.py` — orchestration
- shared/scenarios/{smoke-linux,smoke-windows,functional}/ — scenario files

## What's gone (commit 2d3a800)
- `os/*/full-test.sh` × 7
- `os/*/test.sh` × 7

## Takeaway
Python orchestrates, bash primitives. `mosdat run|test|functional` (post-pyproject) is the single entry.
