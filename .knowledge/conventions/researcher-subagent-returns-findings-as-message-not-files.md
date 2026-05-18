---
date: "2026-05-17"
project: mosdat
topic: Researcher subagent returns findings as final message text, not as written files
kind: pattern
scope: project-shared
confidence: high
---

The `researcher` subagent role is read-only by contract. When a parent agent asks for a research summary (canonical pseudocode, parameters, library behavior, etc.), the researcher must return the full content **as its final assistant message** — NOT by writing a `.md` file under `docs/research/`, `findings.md`, or anywhere else.

Reasoning: the parent orchestrator reads the subagent's text output. Files written by the subagent are not automatically read back by the parent, and double-writing (researcher writes + orchestrator re-writes) creates drift between the two copies.

Exception: `.claude/mytasks/findings.md` is the project-wide cross-agent shared scratchpad. Append to it only when multiple sibling agents need to read the same finding asynchronously. Even then, the primary delivery is via the final message; findings.md is a cache.

Rule: researcher subagents return content, not artifacts. If the orchestrator wants a file, the orchestrator writes it.
