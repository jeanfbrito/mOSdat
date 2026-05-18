---
date: "2026-05-17"
project: mosdat
topic: Module-level skip (not per-test) because `_collect_fixtures()` runs at import time and needs `_VM` set to form
kind: decision
scope: project-shared
confidence: low
---

Key decisions:
- `retries: 0` on the failure-expected verify steps so they fail on first attempt without burning VLM retries.
- `localize` step in wrong-selector uses `ClickStep` shape which ScenarioModel validates correctly (field is `localize`, optional `click`).
- Module-level skip (not per-test) because `_collect_fixtures()` runs at import time and needs `_VM` set to form parametrize IDs correctly.
- `corrupt-config` uses a verify that asserts the logged-in state to trigger failure; RC with broken config shows "Add a server" screen, not the login page with email/password fields.</result>
<usage><total_tokens>54930</total_tokens><tool_uses>32</tool_uses><duration_ms>250070</duration_ms></usage>
</task-notification>

Assistant: I15 negative fixture suite shipped: 5 fixtures + runner + opt-in marker + AGENTS.md docs. 1 wave-2a agent still in flight (I7).
