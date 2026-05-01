---
date: "2026-05-01"
project: mOSdat
tags:
  - mosdat
  - documentation
  - runbook
  - refactor
topic: 'mosdat: AGENTS.md split into docs/runbooks/{matrix-run,gpu-rotation,completion-criteria,vm-setup,triage}.md'
kind: decision
scope: project-shared
category: docs
confidence: high
---

## Decision (commit dd900d0)
Old: 735-line `AGENTS.md` mega-runbook. Slow to navigate.

New: 90-line slim `AGENTS.md` (TOP RULE, Project Overview, Architecture, Key Files, Code Style, Don't, Runbooks index) + five focused runbooks under `docs/runbooks/`:

- `matrix-run.md` (304 lines) — execution waves, parallel ops, build/deploy/test commands per OS
- `gpu-rotation.md` (159 lines) — GPU constraint, attach/detach lifecycle, troubleshooting
- `completion-criteria.md` (87 lines) — package format coverage, GPU configs, display scenarios
- `vm-setup.md` (49 lines) — VM provisioning, ISO storage, adding new OS
- `triage.md` (43 lines) — exit code interpretation, output reading, agent delegation

## Why
- Faster navigation — skim AGENTS.md, jump to specific runbook
- Reduces context cost when only one runbook is relevant

## Takeaway
600+ line runbooks rot. Split when adding a new H2 means scrolling for 30 seconds.
