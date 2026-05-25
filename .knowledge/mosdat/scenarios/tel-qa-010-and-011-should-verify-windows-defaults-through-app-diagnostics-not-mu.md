---
date: "2026-05-25"
project: mOSdat
tags:
  - windows
  - default-apps
  - msi
  - telephony
topic: TEL-QA 010 and 011 should verify Windows defaults through app diagnostics, not mutate OS UI
kind: decision
scope: project-shared
category: mosdat/scenarios
confidence: high
---

## Decision
For Windows default-app and MSI-policy TEL flows, verify protocol registration through Rocket.Chat's Phone-link diagnostics surface instead of driving the Windows Settings default-app UI.

## Why
Windows Settings is localized, layout-variable, and hard to automate reliably. The product diagnostics panel exposes the actual `tel:`/`callto:` default-handler state.

## Implementation
Use the Phone-link handler diagnostics accordion, refresh/copy diagnostics, and assert clipboard JSON values.

## Outcome
QA010 and QA011 passed on both Windows VMs with product build validation.
