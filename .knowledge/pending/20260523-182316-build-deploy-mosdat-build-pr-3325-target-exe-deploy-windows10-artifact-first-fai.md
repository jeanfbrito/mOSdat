---
date: "2026-05-23"
project: mosdat
topic: 'Build/deploy**: `mosdat build --pr 3325 --target exe --deploy windows10 --artifact-first` failed because'
kind: decision
scope: project-shared
confidence: low
---

**Gates**: 1/2/3 all PASS — `build-artifacts` label present, CI green on headSha `e42cad2c`, Windows x64 artifact URL in PR comments.

**Build/deploy**: `mosdat build --pr 3325 --target exe --deploy windows10 --artifact-first` failed because `pick_artifact_url()` in `automation/commands/build.py:376` picks `matches[0]` which alphabetises **arm64 ahead of x64**. Downloaded wrong arch, NSIS install left no `Rocket.Chat.exe`. Worked around by manually aria2c-ing the x64 .exe and silent-installing.

**Install verified**: `C:\Users\jean\AppData\Local\Programs\Rocket.Chat\Rocket.Chat.exe` v4.14.1, LastWriteTime 2026-05-22 19:45:02 (fresh, replacing the 2026-04-08 stale binary).

**Asar symbols** (all present in bundled JS):
- VoiceVideoTab: 2, Diagnostics: 67, telephony: 468, isTelephonyEnabled: 47, telephonyGlobalShortcutConfig: 43.
