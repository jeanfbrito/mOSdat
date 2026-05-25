---
date: "2026-05-23"
project: mosdat
topic: Telephony dispatch surface uses `wait_for any:` with four candidate widgets because the exact tel-handler UI hasn't
kind: decision
scope: project-shared
confidence: low
---

**Key decisions**:
- Used `load_test_yaml` as the canonical loader (entry per `automation/runners/scenario_loader.py:506`). Direct `ScenarioModel.model_validate()` fails on raw `routine:` keys — by design.
- Telephony dispatch surface uses `wait_for any:` with four candidate widgets because the exact tel-handler UI hasn't been live-confirmed; tester may need to feed exact names back if all four miss.
- Protocol BLOCKED uses both `xdg-mime` (registration check) + AT-SPI absence (surface check) for defense-in-depth.

**Deferred**: live execution (per brief — author-only).

**Findings appended** to `/home/jean/projects/linux-testing/mOSdat/.claude/mytasks/findings.md`.</result>
