---
date: "2026-05-16"
project: mOSdat
tags:
  - mosdat
  - author
  - tooling
  - vnc
  - vlm
topic: `mosdat author` provides interactive VNC-driven scenario authoring with capture/click/verify/export
kind: pattern
scope: project-shared
category: mosdat/scenarios
confidence: high
---

## Discovery (2026-05-16)
`mosdat author start <config> --vm <name>` opens a stateful VNC authoring session with subcommands:

| Subcommand | Purpose |
|---|---|
| `capture` | Screenshot current screen, return PNG path |
| `localize` / `prompt-click` / `prompt-hover` / `prompt-type` | VLM-locate a target, optionally act |
| `click` / `hover` / `type` / `key` | Run a confirmed action at coords / with text |
| `verify` | Yes/no VLM screen check |
| `shell` / `launch` | Run a shell command, or launch a binary on VM |
| `step` | Record an action as a scenario step |
| `validate` / `export` | Emit YAML, validate |
| `session` / `close` | Show / end session |

## Implication
When a new scenario fails on a UI element and you don't know coords/labels, drop into `mosdat author`. Walk the real UI screenshot-by-screenshot before re-running a 15-min full scenario. Faster than guessing.

## Caveat
Single-VM contention: author sessions and full scenario runs against the same VM serialize. Don't run author + tester in parallel on the same VM.
