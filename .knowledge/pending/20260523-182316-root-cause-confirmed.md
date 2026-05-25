---
date: "2026-05-23"
project: mosdat
topic: Root cause — confirmed
kind: war-story
scope: project-shared
confidence: medium
---

[MOSDAT-DIAG] RETURNING — telephony disabled
```

## Root cause — confirmed

The failure point matches Step 4, case 3 from the brief: **`parseTelephonyLink` succeeded, but `isTelephonyEnabled: false` caused immediate return** before `openTelephonyDialpad` was called.

Verified from `overridden-settings.json` on the VM: `"isTelephonyEnabled": false`.
