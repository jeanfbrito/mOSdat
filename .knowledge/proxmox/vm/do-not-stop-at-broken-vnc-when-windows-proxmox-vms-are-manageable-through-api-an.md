---
date: "2026-05-25"
project: mOSdat
tags:
  - proxmox
  - windows
  - vnc
  - qa
topic: Do not stop at broken VNC when Windows Proxmox VMs are manageable through API and guest commands
kind: war-story
scope: project-shared
category: proxmox/vm
confidence: high
---

## What
Windows TEL work initially looked blocked when VNC/console access appeared dead.

## Why
The VMs were still controllable through Proxmox and guest-side command paths, so calling VNC dead was not a valid stop condition.

## Action
Manage VM lifecycle through Proxmox, deploy artifacts directly, and use Windows-side commands/logs to verify installed state and scenario behavior.

## Takeaway
For Proxmox-backed QA, VNC is one transport, not the source of truth. If VNC fails, switch control planes before declaring infrastructure blocked.
