---
date: "2026-05-25"
project: mOSdat
tags:
  - windows
  - electron
  - deployment
  - asar
topic: Deploy Windows Rocket.Chat product fixes by replacing app.asar and verifying mtime plus strings
kind: pattern
scope: project-shared
category: mosdat/deployment
confidence: high
---

## What
The installed Windows Rocket.Chat version was updated by replacing `%LocalAppData%\Programs\Rocket.Chat\resources\app.asar` on both Win10 and Win11 VMs.

## Verification
Check the file mtime on each VM and inspect `app.asar` contents: expected fix symbols present, temporary diagnostic strings absent.

## Why
This proves the running installed product has the fix, not a stale build or diagnostic fork.

## Takeaway
When testing Windows installed Electron apps, verify the exact deployed resource file before trusting scenario results.
