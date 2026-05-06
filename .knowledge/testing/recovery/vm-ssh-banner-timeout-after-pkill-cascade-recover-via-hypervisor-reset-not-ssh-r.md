---
date: "2026-05-02"
project: mOSdat
tags:
  - mosdat
  - ssh
  - wedged-vm
  - proxmox
  - recovery
  - pkill
topic: VM SSH banner timeout after pkill cascade — recover via hypervisor reset, not SSH retries
kind: war-story
scope: project-shared
category: testing/recovery
confidence: high
accessed: 2
last_accessed: "2026-05-03"
---

## War story
Smoke-test cleanup script ran `pkill -9 gnome-keyring-d` + multiple `pkill -9` on RC processes across 7 consecutive runs. After the 8th run attempted, ubuntu2404 became unreachable: TCP port 22 accepted connections but sshd never sent a banner, hangs at "banner exchange" then times out. Ping responded, network fine.

## Cause
Cumulative `pkill -9` against user-session daemons (keyring, dbus children) wedged the user-session at PAM/dbus level. sshd's per-connection user-session setup blocked indefinitely. Cleanup ran fine in isolation but each run leaves resource debt that compounds.

## Recovery
SSH retries are useless. Reboot via hypervisor:
```python
api.stop_vm(vmid); api.wait_for_status(vmid, 'stopped'); api.start_vm(vmid)
api.wait_for_ip(vmid, timeout=120)
```
Within ~60s SSH banner returns immediately. RC binary preserved (only userdata is wiped on cleanup).

## Lesson
- TCP-open + no-banner ≠ healthy SSH. Hypervisor reset is faster than diagnosing a wedged user-session.
- Aggressive `pkill -9` cascades are not idempotent over many runs even though each run individually succeeds. Consider `pkill -TERM` then escalate, or skip targets that aren't load-bearing for the test.
- Build VM-reset-on-stale into smoke harness pre-flight: if SSH banner takes >5s, reset before scenario.
