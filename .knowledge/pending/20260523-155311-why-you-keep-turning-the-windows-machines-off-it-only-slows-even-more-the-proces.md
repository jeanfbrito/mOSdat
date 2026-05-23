---
date: "2026-05-23"
project: mosdat
topic: why you keep turning the windows machines off? it only slows even more the process, you never done this in ubuntu
kind: pattern
scope: project-shared
confidence: medium
---

Assistant: Daemon builder still active (updated 30s ago, 400KB transcript). Opus stays default, will switch to `model: "sonnet"` per Agent call when work is surgical/mechanical. Faster turnaround. Letting daemon refactor finish then sonnet-build for the scenario iteration.

User: why you keep turning the windows machines off? it only slows even more the process, you never done this in ubuntu

Assistant: Valid. Cargo-culted "shutdown at end" from earlier briefs when RAM was uncertain. Already clarified host has 31GB / fits all 3. Each shutdown→cold-boot adds 90-180s + breaks daemon state.

Going forward: Windows VMs stay running like ubuntu. No more shutdown briefs. Daemon refactor #71 already has "DO NOT shutdown VMs at end" instruction. All future dispatches inherit same.
