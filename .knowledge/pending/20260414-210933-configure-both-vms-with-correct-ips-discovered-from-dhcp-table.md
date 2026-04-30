---
date: "2026-04-14"
project: mosdat
topic: Configure both VMs with correct IPs discovered from DHCP table
kind: insight
scope: project-shared
confidence: medium
---

Summary:
1. Primary Request and Intent:
   - Continue work from a previous session on creating Windows 10 and Windows 11 VMs for the mosdat test framework
   - Configure both VMs with correct IPs discovered from DHCP table
   - Install OS on both VMs, set up SSH access, and run tests via both standalone `test.sh` scripts and the `mosdat test` CLI
   - Fix any issues along the way to make Windows testing work the same way as Linux

2. Key Technical Concepts:
   - **mosdat**: Python-based multi-OS desktop app testing framework (`mOSdat/`) with Proxmox API integration
