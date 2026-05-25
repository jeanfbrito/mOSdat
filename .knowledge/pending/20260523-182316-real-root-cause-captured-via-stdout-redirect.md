---
date: "2026-05-23"
project: mosdat
topic: Real root cause (captured via stdout redirect):**
kind: war-story
scope: project-shared
confidence: medium
---

**2-STRIKE HALT.** Brief's hypothesis (Windows reaping orphaned schtasks child) is **wrong**. Two different launch patterns fail identically because RC is crashing on its own startup, not being killed.

**Real root cause (captured via stdout redirect):**
`C:\Users\jean\AppData\Roaming\Rocket.Chat\config.json` starts with bytes `EF BB BF` (UTF-8 BOM). `electron-store`/`conf` does `JSON.parse` without stripping the BOM → `SyntaxError: Unexpected token '���'` → RC main process throws in `mergePersistableValues` → exits ~700ms after launch. Stack trace landed at `app.asar/app/main.js:12709 (start)`.

**Strikes:**
1. `New-ScheduledTaskAction -Execute Rocket.Chat.exe` (direct), `ExecutionTimeLimit=PT0S`, `Interactive` principal. Task ran, t=1s: 4 RC procs visible, t=2s: state=Ready, LastResult=1, 0 procs. Misdiagnosed as Task Scheduler job-object killing children.
2. WMI `Win32_Process.Create` inside schtasks action (truly detached — provider hosts it under wmiprvse.exe, not the task's job). Task succeeded LastResult=0. Procs=0 at every t=1..15s. Same death. Proves the launch mechanism is fine; the binary crashes itself.
