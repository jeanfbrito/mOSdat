---
date: "2026-05-25"
project: mOSdat
tags:
  - windows
  - uia
  - electron
  - vlm
  - telephony
topic: Windows Electron BrowserView modals may render visually but be absent from UIA tree
kind: lesson
scope: project-shared
category: testing/windows
confidence: high
---

## What
During Windows TEL-QA debugging, the telephony workspace picker rendered visibly in VNC screenshots, but the mOSdat UIA tree did not expose it. The tree only showed the main Rocket.Chat window/login surface; no dialog, checkbox, or picker controls appeared.

## Why
Chromium/Electron BrowserView or webview surfaces on Windows can be visually reachable from VNC while not being exposed through the desktop UIA daemon tree walked from the SSH session.

## How to handle
When UIA cannot see an expected Windows Electron modal, capture VNC screenshots before assuming the product failed to render. Prefer durable product diagnostics or clipboard JSON where available; otherwise use VLM verification against the visual framebuffer for that surface.
