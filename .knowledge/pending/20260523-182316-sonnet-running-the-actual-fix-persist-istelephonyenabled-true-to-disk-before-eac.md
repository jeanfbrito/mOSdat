---
date: "2026-05-23"
project: mosdat
topic: 'Sonnet running the actual fix: persist `isTelephonyEnabled=true` to disk before each dispatch. Re-sweep all 12'
kind: war-story
scope: project-shared
confidence: medium
---

Real fix: scenarios that DISPATCH tel: must guarantee `isTelephonyEnabled: true` is on disk BEFORE dispatch (either via static pre-stage OR by writing it explicitly between toggle-ON and dispatch).

Assistant: Sonnet running the actual fix: persist `isTelephonyEnabled=true` to disk before each dispatch. Re-sweep all 12 toggle-dependent scenarios both VMs. ~25 min wall. Expected: 10+ scenarios flip GREEN based on root cause diagnosis. Goal achievable now.

User: <task-notification>
<task-id>ac5394ead2cd2de3e</task-id>
<tool-use-id>toolu_016cV4Y54sHZbMMp6prAoFEk</tool-use-id>
<output-file>/tmp/claude-1000/-home-jean-projects-linux-testing-mOSdat/3e4e7683-2b2b-4030-bb3e-80c24f99ca9c/tasks/ac5394ead2cd2de3e.output</output-file>
