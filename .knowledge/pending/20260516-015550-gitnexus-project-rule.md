---
date: "2026-05-16"
project: mosdat
topic: 'GitNexus project rule:'
kind: pattern
scope: project-shared
confidence: medium
---

- use context-mode MCP for large outputs.
       - Bash only for git/mkdir/rm/mv/navigation/short outputs.
       - native Write/Edit for file modification.
     - GitNexus project rule:
       - run impact before editing symbols.
       - run detect_changes before committing.
       - stale index → run `npx gitnexus analyze`.
     - User-added feedback:
       - when stale GitNexus hook appears, always run `npx gitnexus analyze` in background; don’t ask.
