---
date: "2026-05-16"
project: mOSdat
tags:
  - mosdat
  - scenarios
  - electron
  - userdata
topic: PR3325 RC build writes userData to `Rocket.Chat (development)/` not `Rocket.Chat/`
kind: lesson
scope: project-shared
category: mosdat/scenarios
confidence: high
---

## Lesson (2026-05-16)
PR3325 build is packaged as Electron dev-type. Its userData dir is `~/.config/Rocket.Chat (development)/`. Production .deb writes to `~/.config/Rocket.Chat/`. Earlier scenarios that wrote servers.json/config.json only to `Rocket.Chat/` had their config silently ignored on PR3325 → RC booted to "Add a server".

## Fix
Scenarios write to BOTH paths in a loop:
```bash
for d in "$HOME/.config/Rocket.Chat" "$HOME/.config/Rocket.Chat (development)"; do
  printf '%s\n' '{...}' > "$d/config.json"
done
```
Harmless redundancy; covers both prod and dev builds.

## How to discover the right dir for any Electron build
Launch with empty `~/.config/`, then `ls ~/.config/ | grep -i <app>`.

## Rule
For PR/dev builds, expect a `(development)` suffix. Always confirm actual userData dir via the post-launch grep before writing pre-staged state.
