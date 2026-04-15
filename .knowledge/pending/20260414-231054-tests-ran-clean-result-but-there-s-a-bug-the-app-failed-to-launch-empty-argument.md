---
date: "2026-04-14"
project: mosdat
topic: Tests ran. Clean result but there's a bug — the app **failed to launch** (empty `ArgumentList` error) yet reported
kind: decision
scope: project-shared
confidence: low
---

Assistant: App is at `C:\Users\jean\AppData\Local\Programs\Rocket.Chat\Rocket.Chat.exe`. Now transfer tests and run:

Assistant: Tests ran. Clean result but there's a bug — the app **failed to launch** (empty `ArgumentList` error) yet reported `PASS:1` because exit code 1 isn't in the crash list. Two fixes needed in `common.ps1`:

1. Don't pass `-ArgumentList` when args are empty
2. Exit code from a failed start should be `FAIL`, not `PASS`

Assistant: Now re-upload the fixed scripts and rerun:
