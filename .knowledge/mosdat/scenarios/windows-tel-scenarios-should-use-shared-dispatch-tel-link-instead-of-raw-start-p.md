---
date: "2026-05-25"
project: mOSdat
tags:
  - windows
  - telephony
  - scenarios
  - deeplink
topic: Windows TEL scenarios should use shared dispatch-tel-link instead of raw Start-Process ArgumentList
kind: pattern
scope: project-shared
category: mosdat/scenarios
confidence: high
---

## What
Windows TEL QA stabilized after replacing scenario-local `Start-Process -FilePath $rc -ArgumentList ...` calls with the shared `dispatch-tel-link` routine.

## Why
Raw PowerShell argument dispatch did not reliably match real OS protocol activation semantics and made failures look like product bugs.

## Pattern
Use one tested routine for `tel:`/`callto:` dispatch across Win10 and Win11. Keep per-OS scenario copies, but share the activation mechanics.

## Result
After product deployment and scenario fixes, all 24 Windows TEL-QA scenarios passed.
