---
date: "2026-05-23"
project: mosdat
topic: Push deeper. Two real fixes for the 2 root causes
kind: war-story
scope: project-shared
confidence: medium
---

User: Stop hook feedback:
[make all the TEL-QA flows work in windows 10 and windows 11 and pass green, the two VMs exist in proxmox, work on them but take care of the proxmox RAM to not make them slow, you can control all, ultrathink fan out subagents, the organization now is to make subfolders and copy the flows to each OS, not a problem have some similar flows in different folders, we need to assure it works on each OS without messing with another when changes are done]: Condition requires 'all TEL-QA flows work...pass green' on both win10 and win11. Transcript shows 10/24 scenarios GREEN (42%), with 14/24 blocked on Windows shell association cache and widget timing issues. These are not iteration fixes — they require deeper Windows arcana investigation. Assistant explicitly states: 'Remaining 14 blocked on 2 deeper issues (not bandaging anymore)' and 'Need live interactive sessions on the VMs to trace.' The per-OS folder structure and isolation requirement is SATISFIED (12 scenarios per OS, changes to one OS don't affect the other), and infrastructure is solid (daemon, port-forward, 1248 pytest). However, 'all TEL-QA flows work green' is NOT satisfied — 58% of scenarios remain blocked on genuine technical investigations beyond the current session scope.

Assistant: Push deeper. Two real fixes for the 2 root causes.

Assistant: Sonnet dispatched: SHChangeNotify + system-picker dismissal pattern for 12 scenarios; toggle-in-session refactor for tel-qa-013. Detached re-sweep + 25min poll. Notify on complete.

User: <task-notification>
<task-id>a2f6106dce2b50901</task-id>
