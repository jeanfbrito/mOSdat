---
date: "2026-05-23"
project: mosdat
topic: '`overridden-settings.json` on the VM has `isTelephonyEnabled: false`. The URL arrives via **startup argv scan**, not'
kind: decision
scope: project-shared
confidence: low
---

Assistant: 🎯 **BREAKTHROUGH!** Bug is NOT in RC. It's our scenario pre-staging.

`overridden-settings.json` on the VM has `isTelephonyEnabled: false`. The URL arrives via **startup argv scan**, not second-instance IPC (because first-instance lock hasn't been acquired yet when we Start-Process). Startup reads disk config = OFF → silently returns.

**Fix is mosdat-side**:

Assistant: Two patterns:
- **002/010/011/014**: pre-stage `isTelephonyEnabled = $false` (correct per scenario semantics)
