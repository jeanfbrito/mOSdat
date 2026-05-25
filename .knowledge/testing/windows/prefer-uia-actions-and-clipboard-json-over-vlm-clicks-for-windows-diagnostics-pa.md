---
date: "2026-05-25"
project: mOSdat
tags:
  - windows
  - uia
  - vlm
  - diagnostics
topic: Prefer UIA actions and clipboard JSON over VLM clicks for Windows diagnostics panels
kind: pattern
scope: project-shared
category: testing/windows
confidence: high
---

## What
Windows TEL diagnostics scenarios became stable after using UIA `via: action` on controls and validating copied diagnostics JSON from the clipboard.

## Why
VLM/localize clicks on Chromium buttons and localized labels were flaky; UIA hit-testing can land on child `Static` text instead of the parent button.

## Pattern
Open the stable diagnostics accordion, trigger `Refresh`/`Copy diagnostics` via action, then assert JSON fields like `isDefault.tel` and `isDefault.callto`.

## Takeaway
For Windows Electron diagnostics, assert machine-readable clipboard output when the UI exposes it.
