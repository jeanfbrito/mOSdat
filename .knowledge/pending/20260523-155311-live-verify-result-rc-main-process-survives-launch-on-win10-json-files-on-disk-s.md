---
date: "2026-05-23"
project: mosdat
topic: Live verify result:** RC main process survives launch on win10. JSON files on disk start with `7B` (`{`), no BOM. RC
kind: war-story
scope: project-shared
confidence: medium
---

- `tests/test_windows_scenario_bom.py::test_no_bom_emitting_writers_in_windows_routines` (new)
- Full suite: **1144 passed, 5 skipped, 3 xfailed** (≥ 1142/5/3/0 threshold)

**Live verify result:** RC main process survives launch on win10. JSON files on disk start with `7B` (`{`), no BOM. RC reaches the login screen ("Welcome to Southlogic workspace") — screenshot at `results/functional/2026-05-23_012107_functional/windows10/012152_step7_final_fail.png`. Step 7 still fails but **on a different root cause**: atspi `{role:"frame"}` does not match the RC window on Windows. New failure mode is now the next builder's concern (windows atspi/uia role mapping).

**Deferred:** Step-7 atspi role mismatch on Windows. Next builder should `mosdat atspi-dump --vms windows10` against running RC to discover the actual surfaced role/name, then update `wait_for` in win10 scenarios.

**VM shutdown:** Proxmox API ACPI shutdown sent to vmid 104 (200 OK).</result>
<usage><total_tokens>97760</total_tokens><tool_uses>94</tool_uses><duration_ms>1053755</duration_ms></usage>
