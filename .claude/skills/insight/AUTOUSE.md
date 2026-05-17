# Insight Autouse — After-3-Failures Trigger

## Concept

After 3 failures at the same step number (or same verify prompt) within a single session's agent dispatches, the insight system:

1. Detects the pattern from the PostToolUse transcript.
2. Dispatches a bounded finder against knowledge stores.
3. Injects matching slugs + summaries into the next planner brief.

This prevents agents from re-running the same broken approach a 4th time when a recorded lesson already explains the fix.

## Trigger Condition

```
step_failures[step_N] >= 3
  AND same session
  AND --no-autoinsight NOT passed
```

"Same step" is identified by either:
- The step number in the agent brief (`step: N`), OR
- The first 60 chars of the verify/check prompt (normalized, lowercased).

## Knowledge Search Targets

In priority order:

1. `<cwd>/.knowledge/**/*.md` — project-specific lessons (highest relevance)
2. `~/.knowledge/**/*.md` — user personal lessons
3. `<cwd>/shared/recipes/*.yaml` — F2 recipe corpus (if directory exists)

Search strategy: grep for tags and topic keywords extracted from the failing step's context. Return top 3 matches by recency and tag overlap.

## Injection Format

Prepend to next planner brief:

```
[insight] related lessons found after 3 failures at step N:
- ~/.knowledge/electron/no-ctrl-comma-accelerator.md — "Electron: no Ctrl+, on Linux; drive via config.json instead"
- .knowledge/mosdat/scenarios/pr3325-settings-bypass.md — "Bypass RC Settings UI via config.json pre-staging"
```

If no matches: silent. Do not inject noise.

## Opt-Out

Pass `--no-autoinsight` anywhere in the user prompt or agent brief to suppress for that dispatch.

Set `INSIGHT_DISABLED=1` in environment to suppress globally for the session.

## Implementation

The trigger is implemented via `check_repeated_failures.sh` (see INSTALL.md), wired as a PostToolUse hook on the `Agent` matcher. The script:

1. Reads the most recent tool output block from stdin (Claude Code passes this as JSON).
2. Parses `step_number` and `result: failed` fields.
3. Increments a counter in `/tmp/insight_failures_<session_id>.json`.
4. When threshold hit: searches knowledge dirs, emits formatted stderr message.
5. Claude Code surfaces stderr from hooks as context injections into the next turn.

## Limitations

- Counter resets on session end (tmp file).
- Step-number parsing is heuristic — depends on brief format consistency.
- No fuzzy dedup: two briefs with slightly different step phrasing count as separate steps.
- F2 recipe YAML search is keyword-only (no semantic search).
