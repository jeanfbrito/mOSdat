---
name: mosdat-qa-pr-status-comment
description: Update or create a GitHub PR comment that reports the current mOSdat QA-flow status per configured OS target. Use when asked to post, refresh, or show the actual QA matrix state for PR tests, including which systems are passing or still failing.
---

# mOSdat QA PR Status Comment

Use this when the user asks to post, refresh, or inspect the PR QA status matrix for mOSdat scenario runs.

## Workflow

1. Identify the PR and repository. Prefer the GitHub connector for PR metadata; use `gh` only when connector coverage is insufficient.
2. Read the current mOSdat state from local evidence, not memory:
   - `examples/rocketchat.toml` for configured VM targets.
   - `shared/scenarios/functional/**` for scenario coverage by OS.
   - `results/functional/**/events.jsonl`, `summary.json`, or run reports for pass/fail evidence.
3. Build a compact status table by OS target. Include only states backed by local run artifacts or explicit command output.
4. If evidence is missing, mark that target as not run or unknown. Do not infer pass from absence of failures.
5. Update an existing bot/status comment when one exists; otherwise create a new PR comment.
6. Preserve unrelated human discussion on the PR.

## Comment Shape

Keep the comment short:

- Header naming the PR/test scope and timestamp.
- Table: OS/VM, scenario or flow, status, evidence path or run id, notes.
- Short next-action list for failing or missing targets.

## Guardrails

- Do not claim green matrix unless every required target has fresh passing evidence.
- Do not run long VM waits inline; start monitoring separately and continue useful work.
- Do not paste large logs into PR comments. Link or reference artifact paths instead.
- Before posting, show the computed matrix if the evidence is ambiguous.
