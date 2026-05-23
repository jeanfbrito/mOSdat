---
date: "2026-05-23"
project: mosdat
topic: 'Upstream bug discovered**: `TypeError: Cannot read properties of null (reading ''url'')` at `setupServers` (main.js:6581)'
kind: insight
scope: project-shared
confidence: medium
---

**Pytest**: 1077/5/3/0 preserved.

**Upstream bug discovered**: `TypeError: Cannot read properties of null (reading 'url')` at `setupServers` (main.js:6581) when launching with zero workspaces — RC silently exits. Documented in `docs/KNOWN_ISSUES.md`. tel-qa-014 step2 sub-scenario commented out, re-enable when upstream patched.

Ready to commit Round 5.

User: what is PASS 55/55 (step2 SKIPPED — upstream PR bug)?
