---
date: "2026-05-25"
project: Rocket.Chat.Electron
tags:
  - electron
  - windows
  - deeplink
  - telephony
  - argv
topic: Windows protocol launch must filter Electron/internal argv before deep-link parsing
kind: war-story
scope: project-shared
category: electron/deeplink
confidence: high
---

## What
Windows TEL QA failures exposed that installed Rocket.Chat could receive non-URL process arguments around protocol activation.

## Why
Deep-link handling must ignore Electron/bootstrap/internal argv and only pass real `tel:`/`callto:` URLs into telephony routing. Otherwise installed product behavior can diverge from dev/test dispatch.

## Fix
Product fix added filtered deep-link arg extraction (`getDeepLinkArgs`) and was pushed as `39382ceb6 fix: filter deep link process arguments` on `feat/telephony-deeplink`.

## Takeaway
For Electron protocol handlers, test installed Windows builds and parse argv defensively; never assume every argv item is a user deep link.
