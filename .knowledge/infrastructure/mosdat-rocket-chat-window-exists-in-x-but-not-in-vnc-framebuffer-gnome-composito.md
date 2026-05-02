---
date: "2026-05-01"
project: mOSdat
tags:
  - mosdat
  - rocketchat
  - gnome
  - x11
  - compositor
topic: 'mosdat: Rocket.Chat window exists in X but not in VNC framebuffer (GNOME compositor)'
kind: war-story
scope: project-shared
category: infrastructure
confidence: high
accessed: 37
last_accessed: "2026-05-02"
---

## Finding (2026-05-01 ubuntu2204)
After `injector.launch("/opt/Rocket.Chat/rocketchat-desktop --no-sandbox")` on Ubuntu 22.04 GNOME X11:

- Process tree shows RC running (`pgrep -af rocketchat-desktop` finds 4+ procs with --ozone-platform=x11)
- `/proc/<pid>/environ` confirms DISPLAY=:0, XAUTHORITY=/run/user/1000/gdm/Xauthority
- `xwininfo -root -tree` finds the Rocket.Chat window at `+10+45 1000x600`
- BUT VNC framebuffer (Proxmox console) shows only the GNOME purple desktop
- BUT `_NET_ACTIVE_WINDOW` returns `0x0` (no active window)

## Hypothesis
gnome-shell creates the window but doesn't composite/raise it. Possibly because:
1. RC was launched from an SSH-spawned process inheriting tty-session DISPLAY (works for X but not for shell-driven activation)
2. Override-redirect flag bypasses WM
3. First-launch dialog rendering quirk

## Implication for smoke tests
The YAML smoke uses `launch: "{app_path}"` for direct binary execution. Switching to the Super-key launcher flow may render correctly:
```yaml
- key: "super"
  wait: 1
- type: "Rocket"
- key: "enter"
  wait: 10
```
GNOME's launcher activates the desktop file properly and the WM raises/composites the window.

## Workaround tried
- Mouse jiggle to wake DPMS: works for screen wake, doesn't raise the RC window
- xset -dpms: confirms DPMS is disabled, screen stays awake
- wmctrl -a Rocket.Chat: would raise the window, but wmctrl not installed by default on Ubuntu 22.04 GNOME

## Suggested follow-up
1. Switch Linux smoke yaml to launcher flow (super → type → enter)
2. OR install xdotool/wmctrl on test VMs and add a "raise window" step
