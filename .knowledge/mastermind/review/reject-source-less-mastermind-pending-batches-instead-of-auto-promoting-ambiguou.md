---
date: "2026-05-25"
project: mOSdat
tags:
  - mastermind
  - review
  - pending
  - knowledge
topic: Reject source-less mastermind pending batches instead of auto-promoting ambiguous extraction fragments
kind: lesson
scope: project-shared
category: mastermind/review
confidence: high
---

## What
`mm-review` found 131 pending entries, and a queue-wide audit showed 131/131 lacked a `## Source` section.

## Why
Per review policy, entries without source commits/files are AMBIGUOUS and cannot be auto-promoted. Reviewing them one by one wastes time when the whole batch is unverifiable.

## Action
After human approval (`reject source-less batch`), delete only pending `.md` files under the project `.knowledge/pending` directory and confirm count is zero.

## Takeaway
If a pending batch is entirely source-less, treat it as extraction noise unless the human explicitly chooses manual review.
