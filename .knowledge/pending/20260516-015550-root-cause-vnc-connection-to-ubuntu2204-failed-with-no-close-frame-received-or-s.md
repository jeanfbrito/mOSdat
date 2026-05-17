---
date: "2026-05-16"
project: mosdat
topic: 'Root cause**: VNC connection to ubuntu2204 failed with "no close frame received or sent". The VM is listed as available'
kind: war-story
scope: project-shared
confidence: medium
---

**FAIL — All 5 scenarios**

**Root cause**: VNC connection to ubuntu2204 failed with "no close frame received or sent". The VM is listed as available but VNC connectivity is down.

**Evidence**:
- All background tasks completed with exit code 0 (shell succeeded, but mosdat logic failed)
- `mosdat list-vms` shows ubuntu2204 online at 192.168.13.81
- mosdat error on first direct run: `VncClientError: Failed to open VNC session after retries: no close frame received or sent`
