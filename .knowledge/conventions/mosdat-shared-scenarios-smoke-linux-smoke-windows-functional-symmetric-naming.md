---
date: "2026-05-01"
project: mOSdat
tags:
  - mosdat
  - scenarios
  - conventions
  - refactor
topic: 'mosdat: shared/scenarios/{smoke-linux,smoke-windows,functional}/ symmetric naming'
kind: decision
scope: project-shared
category: conventions
confidence: high
accessed: 21
last_accessed: "2026-05-01"
---

## Decision (commit ff2cb2c)
Old (drift):
- `shared/tests/`            — Linux smoke .sh
- `shared/tests-functional/` — YAML
- `shared/tests-windows/`    — PowerShell

Three different naming patterns for parallel concepts.

New (symmetric):
- `shared/scenarios/smoke-linux/`
- `shared/scenarios/smoke-windows/`
- `shared/scenarios/functional/`

## Refs updated
- `automation/config.py`: `tests_path`, `tests_windows_path` properties + auto-discovery + functional `tests_dir`
- `automation/main.py`: functional fallback `tests_dir`
- `AGENTS.md`: scp paths in runbook
- `docs/TROUBLESHOOTING.md`

## In-VM destination preserved
SCP source path changed to `shared/scenarios/smoke-linux`, but destination still `/tmp/tests` for backwards compat with in-VM scripts.

## Takeaway
Symmetric naming = lower cognitive load. Don't half-rename.
