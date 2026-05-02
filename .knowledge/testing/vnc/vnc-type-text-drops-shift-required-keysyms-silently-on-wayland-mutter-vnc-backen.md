---
date: "2026-05-02"
project: mOSdat
tags:
  - mosdat
  - vnc
  - wayland
  - keysym
  - type_text
  - canary
topic: VNC type_text drops shift-required keysyms silently on Wayland mutter VNC backend
kind: war-story
scope: project-shared
category: testing/vnc
confidence: high
---

## What
Single-char VNC `type_text` of shift-required ASCII (`~`, `§`, `!`, `@`, etc.) produces no output on the Proxmox-QEMU → Wayland-mutter chain. No error, no warning — the keypress disappears. Multi-char strings of unmodified ASCII (lowercase letters, digits, `.`, `,`) work fine in the same code path.

## Why
`automation/transport/vnc.py::type_text` wraps `Shift_L` press/release around shifted chars, but the wrap is dropped somewhere between QEMU and the Wayland compositor. Unmodified-keysym path is the only reliable single-char route on this stack.

## Lesson
- Canary chars / probe keystrokes for VLM-driven tests: pick **no-shift ASCII** (lowercase letters, digits). Default canary char is `q` — not in any RC placeholder, distinctive, lowercase, no shift required.
- When debugging "typing not appearing" on a Wayland VM: first hypothesis = shift-required keysym, not focus loss or VLM blindness.
- If shifted input is essential, send `injector.key("shift+...")` explicitly — bypass `type_text`'s shift wrap.
