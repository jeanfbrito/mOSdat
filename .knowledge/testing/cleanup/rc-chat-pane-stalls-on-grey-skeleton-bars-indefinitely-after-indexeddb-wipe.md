---
date: "2026-05-02"
project: mOSdat
tags:
  - mosdat
  - rocketchat
  - cleanup
  - indexeddb
  - skeleton
  - cache
topic: RC chat pane stalls on grey skeleton bars indefinitely after IndexedDB wipe
kind: war-story
scope: project-shared
category: testing/cleanup
confidence: high
---

## War story
Smoke-test login PASSED, sidebar rendered correctly with channel `general` highlighted. Chat pane right-side stayed stuck on grey skeleton placeholder bars >200s across multiple runs. Step 8 ("General channel finished loading") failed 3× per run.

## Cause
Cleanup script Stage 4 wiped `~/.local/share/Rocket.Chat*`. That directory holds Meteor's IndexedDB / message cache. After fresh wipe, RC must initial-sync the entire workspace from the server — on a heavy workspace, the chat pane hangs on skeleton placeholders waiting for subscriptions to complete. Auth state lives in `~/.config/Rocket.Chat/Local Storage` and the libsecret keyring (both still wiped) — so logout is preserved without touching IndexedDB.

## Fix
Drop the `rm -rf "$HOME/.local/share/Rocket.Chat*"` lines from Stage 4 cleanup. Logout is enforced via `.config` + keyring wipe alone. Subsequent runs keep the cache and paginate channel content fast.

## Lesson
- Cleanup that "enforces logged-out state" should target auth artifacts only, not data caches.
- First run on a fresh VM will always be slow on first channel-load — there's no cache yet. Subsequent runs reuse it. Pre-warm cache on CI runners.
- Generalizable: distinguish "logout state" from "fresh-install state" in test cleanup. Most smoke tests want logout, not fresh-install.
