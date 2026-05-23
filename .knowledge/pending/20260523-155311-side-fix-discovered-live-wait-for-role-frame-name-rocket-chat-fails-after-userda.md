---
date: "2026-05-23"
project: mosdat
topic: 'Side-fix discovered live**: `wait_for {role: frame, name: "Rocket.Chat"}` fails after userData pre-stage because the'
kind: insight
scope: project-shared
confidence: medium
---

## Key decisions
- **Fix 2 — probe-by-modal**: chose behavioural verification (no worker change, no keyboard fallback). The one-shot "Got it" modal fires ONLY on OFF→ON, so its appearance proves state. Shell step calls the worker directly with find+do_action+wait_for batch; retries once if the modal doesn't appear within 5 s (covers the case where toggle was persisted ON).
- **Side-fix discovered live**: `wait_for {role: frame, name: "Rocket.Chat"}` fails after userData pre-stage because the frame title becomes the workspace title (`"Login - Southlogic"`). Relaxed to role-only — scoped to the rocketchat-desktop AT-SPI app filter already, so this is unambiguous.
- Inlined the userData config writes (servers.json + config.json + overridden-settings.json) in the scenario rather than splitting between scenario + routine — keeps the scenario self-contained and removes the cross-file XAUTH dependency.

## Verification
- Live run: PASS 27/27.
- Pytest: 1056 passed, 5 skipped, 3 xfailed (baseline preserved).
