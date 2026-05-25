---
date: "2026-05-25"
project: mOSdat
tags:
  - triage
  - functional-tests
  - windows
  - vlm
topic: Diagnose mOSdat scenarios stepwise before running full sweeps
kind: pattern
scope: project-shared
category: testing/triage
confidence: high
---

## Pattern
For scenario failures, avoid repeated full-scenario runs during diagnosis. Use step-bounded execution and targeted evidence first.

## Workflow
- Run up to the suspect step with `--until-step N` and save screenshots when available.
- Compare VNC framebuffer captures with AT-SPI/UIA tree dumps when semantic automation reports missing controls.
- Tail the app log for diagnostic markers during the exact action under test.
- Use full scenario or matrix sweeps only after the failure layer is isolated and a fix is ready for regression validation.

## Why
Full runs cost 5-15 minutes and obscure whether the failure is product behavior, scenario setup, accessibility exposure, stale binary, or runner liveness logic.
