---
name: mosdat-insight
description: "Use when the user invokes /insight, says 'capture insight', or after repeated failures to surface relevant project and personal lessons. Captures structured insight entries and surfaces existing knowledge to prevent repeated mistakes."
---

# /insight — Capture & Surface Engineering Insights

Triggers: `/insight`, "insight", "capture insight"

## When to Invoke

- User explicitly calls `/insight` or "capture insight"
- After 3+ failed attempts at the same step (autouse — see AUTOUSE.md)
- Before a planner brief, to surface any existing lessons on the topic
- End of a debugging session where a non-obvious fix was found

## Workflow

### A. Surfacing existing insights (do this FIRST)

1. Identify the current problem domain (keywords from session context or user input).
2. Search for relevant lessons:
   - `<project>/.knowledge/**/*.md` — project-specific lessons
   - `~/.knowledge/**/*.md` — personal cross-project lessons
   - `<project>/shared/recipes/*.yaml` — F2 recipe corpus (if present)
3. If matches found: emit a "[insight] related lessons found:" block listing slugs + 1-line summaries into the next planner brief or response. Skip if `--no-autoinsight` passed.

### B. Capturing a new insight

1. **Elicit or infer from session context:**
   - Symptoms: what broke or wasted time?
   - Root cause: the actual reason it happened.
   - Pivot: what fixed it?
   - Cost: attempts × estimated time (e.g., "3 attempts × ~20min = ~1hr").

2. **Determine scope:**
   - `project-shared` → `<project>/.knowledge/` — specific to this codebase/tooling.
   - `user-personal` → `~/.knowledge/<tech>/` — reusable across projects for this tech.
   - `tech-specific` → `~/.knowledge/<tech>/` — platform/framework behavior.

3. **Determine kind:**
   - `war-story` — painful failure with root cause and fix
   - `lesson` — non-obvious discovery about how something works
   - `pattern` — reusable approach that worked
   - `insight` — principle or observation with broad applicability

4. **Write the entry** (see format below). Keep body under 15 lines — dense, not verbose.

5. **Write path:**
   - project-shared: `<project>/.knowledge/<category>/<slug>.md`
   - user-personal: `~/.knowledge/<tech>/<slug>.md`

6. If scope = `user-personal`: append entry path to `~/.knowledge/index.md` (create if missing).

7. Report the written path to the user.

## Entry Format

```markdown
---
date: "YYYY-MM-DD"
project: <project-name or "general">
tags:
  - <tag1>
  - <tag2>
topic: <one-line summary — what future-you will search for>
kind: war-story|lesson|pattern|insight
scope: project-shared|user-personal|tech-specific
category: <tech/subtopic — e.g., electron/ipc, git/rebase>
confidence: high|medium|low
cost: "<N attempts × ~Xmin = ~Yhr>"
---

## Context
<1-2 sentences: what were you doing, what went wrong or what were you exploring>

## Root cause
<The actual reason — not symptoms>

## Pivot
<What fixed it or what the discovery was>

## Rule
<Concrete, actionable rule: one sentence a future agent can apply immediately>
```

## Mastermind Integration

If mastermind is active (check: `mastermind --help`), use `mm_write` instead of writing raw markdown for `user-personal` entries. Mastermind's `extract` hook (PreCompact) reads the conversation and can auto-extract insights — `/insight` ensures deliberate, structured capture with frontmatter that matches `.knowledge` conventions.

For `project-shared` entries, write raw markdown to `.knowledge/` directly — mastermind's `suggest` hook (PostToolUse on Read/Edit/Write) will surface them automatically on next relevant file touch.

## Mastermind extract and .knowledge

`mastermind extract` reads conversation transcripts and writes to `~/.knowledge/`. Structured `.knowledge/*.md` entries (with frontmatter) are read by `mastermind suggest --from-hook`, which fires on every Read/Edit/Write and surfaces matching lessons. Insight entries written here ARE auto-pulled into the knowledge surface — no extra wiring needed.

## Notes

- One entry per distinct lesson. Do not bundle multiple root causes into one entry.
- Topic should be searchable — "Electron: no Ctrl+, accelerator on Linux" beats "settings bug".
- Set confidence=low if the root cause is inferred, not confirmed.
- Cross-reference related entries in the body if they exist.
