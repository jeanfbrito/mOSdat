---
date: "2026-05-25"
project: mOSdat
tags:
  - windows
  - telephony
  - qa
  - war-story
topic: Windows TEL QA reached 24 of 24 green only after separating product bugs from scenario flake
kind: war-story
scope: project-shared
category: mosdat/scenarios
confidence: high
---

## What
Earlier sweeps stalled around 10/24 green and mixed true product issues with scenario instability.

## Root split
Product: Windows deep-link argv filtering in Rocket.Chat.Electron. Scenario: dispatch mechanics, UIA action clicks, stale localization/layout assertions, and diagnostics verification method.

## Fix set
Desktop commit `39382ceb6`; mOSdat commit `08deb88 fix: stabilize Windows tel QA scenarios`; pushed to `origin/main` after explicit approval.

## Takeaway
When a QA suite has broad failures, isolate product activation semantics first, then stabilize assertions against durable product surfaces.
