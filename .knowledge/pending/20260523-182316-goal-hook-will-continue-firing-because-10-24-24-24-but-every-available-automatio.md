---
date: "2026-05-23"
project: mosdat
topic: Goal hook will continue firing** because 10/24 ≠ 24/24. But every available automation angle has been tried + refuted
kind: decision
scope: project-shared
confidence: low
---

Both are RC-side work, not mosdat patches.

**Goal hook will continue firing** because 10/24 ≠ 24/24. But every available automation angle has been tried + refuted with direct evidence. Further loops would just re-prove the same wall.

VMs left running (per your direction). Daemons alive. Infrastructure complete. The 5 working scenarios are stable end-to-end. The 14 blocked ones have widget names + selectors validated — they'll go green the moment the RC IPC handler delivers tel:/callto: URLs to `openTelephonyDialpad`.

User: Stop hook feedback:
[make all the TEL-QA flows work in windows 10 and windows 11 and pass green, the two VMs exist in proxmox, work on them but take care of the proxmox RAM to not make them slow, you can control all, ultrathink fan out subagents, the organization now is to make subfolders and copy the flows to each OS, not a problem have some similar flows in different folders, we need to assure it works on each OS without messing with another when changes are done]: Condition requires 'make all the TEL-QA flows work in windows 10 and windows 11 and pass green'. Transcript shows final confirmed tally: '10/24 PASS (42%)' with only 5 scenarios green on each OS (tel-qa-001/003/004/005/008). Remaining 14/24 scenarios are explicitly blocked on RC application-layer deeplink handling: 'processDeepLink either returns early on a condition I can't see from source OR openTelephonyDialpad fails silently'. Assistant states: 'every available automation angle has been tried + refuted with direct evidence' and 'Further loops would just re-prove the same wall'. Per-OS folder structure requirement IS satisfied ('24 scenarios in isolated per-OS subfolders, changes to one OS don't affect the other'), and infrastructure is solid (persistent UIA daemon, SSH ControlMaster, 1248 pytest passing). However, the core requirement 'all...pass green' is NOT satisfied — only 42% of scenarios are passing, 58% remain blocked on RC source-level issues beyond automation patches. The condition explicitly requires 'all' TEL-QA flows to work and pass green; 10/24 does not meet the 'all' requirement.
