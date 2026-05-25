---
date: "2026-05-23"
project: mosdat
topic: 1. The PR's tsx files exist on disk but are **not wired into the bundle entry point** at this SHA (dead source —
kind: decision
scope: project-shared
confidence: low
---

### Root finding (1 of 2 possibilities — needs Auditor to disambiguate)

Either:
1. The PR's tsx files exist on disk but are **not wired into the bundle entry point** at this SHA (dead source — perhaps `SettingsView.tsx` import path mismatch, or a renderer entry config doesn't pull them in), and the build succeeds because TS files are compiled but the resulting modules are tree-shaken when nothing references them at runtime.
2. The CI base-branch merge dropped `VoiceVideoTab` due to a conflict resolution against current master (`c2b584ba`); the `--json files` shows files as added by the PR but the merge result may differ.

Either way, the deployed artifact lacks the UI surface for TEL-QA-004 even though the PR's source tree carries diagnostics + Voice &amp; Video files. **Uncommenting the diagnostics steps would fail at step 1 (no "Voice &amp; Video" tab to click).**

### Strike count
