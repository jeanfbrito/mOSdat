---
date: "2026-05-23"
project: mosdat
topic: Per-part re-stage + relaunch (Telephony OFF for step1; fresh-zero-workspaces for step2; ON for steps 3-6;
kind: decision
scope: project-shared
confidence: low
---

**Key decisions**:
- Mirrored `tel-qa-002` template verbatim: inline shell launch + XAUTH fallback, AT-SPI pointer-mode default, `via: hint` only on Telephony ToggleSwitch, `mosdat_atspi_worker.py` ops-array probes for absence checks, per-part userData pre-stage, `cleanup-rocketchat` bookends.
- Per-part re-stage + relaunch (Telephony OFF for step1; fresh-zero-workspaces for step2; ON for steps 3-6; stale-preferred for step7) — required because step 2 spec explicitly demands a fresh profile.
- Step 6 dedupe assertion is best-effort: worker grammar has no `findall`, so we assert at-least-one-modal + pgrep app-alive. Noted in findings.
- Did not touch `3325-edge-cases.yaml`.

**Findings**: appended `# tel-qa-014 authoring (2026-05-22)` to `/home/jean/projects/linux-testing/mOSdat/.claude/mytasks/findings.md`.</result>
<usage><total_tokens>61641</total_tokens><tool_uses>10</tool_uses><duration_ms>226577</duration_ms></usage>
