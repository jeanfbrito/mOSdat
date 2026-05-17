---
date: "2026-05-16"
project: mosdat
topic: 'Goal acknowledged. Resuming from 2-strike diagnosis. Two failure modes: master-toggle Step 8 (modal appeared though'
kind: war-story
scope: project-shared
confidence: medium
---

User: A session-scoped Stop hook is now active with condition: "work until the tests work in mosdat and cover the PR 3325, fan out subagents ultrathink". Briefly acknowledge the goal, then immediately start (or continue) working toward it — treat the condition itself as your directive and do not pause to ask the user what to do. The hook will block stopping until the condition holds. It auto-clears once the condition is met — do not tell the user to run `/goal clear` after success; that's only for clearing a goal early.

Assistant: Goal acknowledged. Resuming from 2-strike diagnosis. Two failure modes: master-toggle Step 8 (modal appeared though toggle assumed OFF) + global-shortcut Step 8 (Ctrl+, didn't open Settings). Fanning out smart agents to investigate root cause + redesign.

Assistant: Two parallel investigators dispatched in background:
- Researcher: PR3325 source — telephony default toggle state + Settings open path.
- Finder: alternate RC Settings open method (menu vs sidebar vs keyboard).
