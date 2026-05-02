---
date: "2026-05-02"
project: mOSdat
tags:
  - mosdat
  - vnc
  - kde
  - wayland
  - xdotool
  - scenario
topic: VNC-native scenario input is display-server agnostic — beats SSH+xdotool on KDE
kind: pattern
scope: project-shared
category: testing/desktop-automation
confidence: high
---

## Pattern (2026-05-02, validated on opensuse Leap KDE X11 + manjaro KDE Wayland)
Use mosdat scenario primitives `if_visible:`, `localize:`, `then_type:`, `then_key:` with a `{vm_password}` template var instead of SSH-driven `xdotool` shell steps for desktop input.

## Why this matters
SSH-driven xdotool fails silently on KDE distros for two compounding reasons:
1. xauth cookie mismatch — SSH session cannot authorize to display `:0`. xdotool exits 0 but no input registers.
2. xdotool not always installed (e.g. opensuse Leap minimal).

VNC-native input goes through the QEMU VNC framebuffer's keyboard/mouse channel — works on X11, Wayland, Windows, anywhere QEMU is rendering a display.

## Implementation
- mosdat exposes `{vm_password}` template var sourced from `DEFAULT_VM_PASSWORD` env (commit f432649).
- Lock-screen unlock pattern:
```yaml
- if_visible: "KDE password prompt"
  then:
    - localize: "the password input field"
      then_type: "{vm_password}"
      then_key: "enter"
```

## Takeaway
For multi-DE GUI testing, keep input on the VNC channel. SSH-injected X11 commands are a portability trap.
