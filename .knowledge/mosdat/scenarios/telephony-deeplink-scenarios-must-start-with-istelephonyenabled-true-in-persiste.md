---
date: "2026-05-25"
project: mOSdat
tags:
  - telephony
  - deeplink
  - windows
  - scenarios
  - rocketchat
topic: Telephony deeplink scenarios must start with isTelephonyEnabled true in persisted settings
kind: lesson
scope: project-shared
category: mosdat/scenarios
confidence: high
---

## What
A Windows TEL-QA diagnostic run showed `processDeepLink` parsing `tel:+15551234567` correctly, then returning because main-process Redux state had `isTelephonyEnabled: false`. `openTelephonyDialpad` never ran.

## Cause
Rocket.Chat read persisted/overridden settings at startup. If scenario pre-stage leaves `isTelephonyEnabled` false or relies on an in-session toggle racing the deeplink dispatch, the product correctly gates the deep link.

## Rule
For TEL deeplink scenarios, pre-stage telephony enabled state before dispatch. Validate the actual persisted file or diagnostics surface, then dispatch the `tel:`/`callto:` link. Do not treat this as an RC source bug unless the persisted setting is true and `processDeepLink` still gates incorrectly.
