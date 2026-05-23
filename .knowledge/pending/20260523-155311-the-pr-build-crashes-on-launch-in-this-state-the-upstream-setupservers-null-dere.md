---
date: "2026-05-23"
project: mosdat
topic: The PR build crashes on launch in this state (the upstream `setupServers` null-deref bug). All 18 sub-steps would fail
kind: decision
scope: project-shared
confidence: low
---

Step2 sub-scenario = "zero-workspaces test": wipes userData to fresh-empty config, launches RC, verifies no crash on tel: link dispatch. That's 18 sub-steps (cleanup + launch + Telephony toggle + xdg-open + assertions).

The PR build crashes on launch in this state (the upstream `setupServers` null-deref bug). All 18 sub-steps would fail because RC never opens.

Builder replaced the 18-step block with a single placeholder step:
```yaml
- label: "QA-014-step2: SKIPPED — upstream PR #3325 bug (zero-workspaces TypeError)"
  shell: |
