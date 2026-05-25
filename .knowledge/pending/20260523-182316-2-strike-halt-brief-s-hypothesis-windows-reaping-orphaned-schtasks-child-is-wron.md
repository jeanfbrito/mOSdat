---
date: "2026-05-23"
project: mosdat
topic: 2-STRIKE HALT.** Brief's hypothesis (Windows reaping orphaned schtasks child) is **wrong**. Two different launch
kind: decision
scope: project-shared
confidence: low
---

<summary>Agent "Fix RC Windows launch detach" completed</summary>
<result>## Report

**2-STRIKE HALT.** Brief's hypothesis (Windows reaping orphaned schtasks child) is **wrong**. Two different launch patterns fail identically because RC is crashing on its own startup, not being killed.

**Real root cause (captured via stdout redirect):**
`C:\Users\jean\AppData\Roaming\Rocket.Chat\config.json` starts with bytes `EF BB BF` (UTF-8 BOM). `electron-store`/`conf` does `JSON.parse` without stripping the BOM → `SyntaxError: Unexpected token '���'` → RC main process throws in `mergePersistableValues` → exits ~700ms after launch. Stack trace landed at `app.asar/app/main.js:12709 (start)`.

**Strikes:**
