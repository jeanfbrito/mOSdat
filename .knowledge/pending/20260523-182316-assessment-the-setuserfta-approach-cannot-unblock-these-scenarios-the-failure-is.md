---
date: "2026-05-23"
project: mosdat
topic: 'Assessment: The SetUserFTA approach cannot unblock these scenarios.** The failure is post-dispatch at the RC'
kind: war-story
scope: project-shared
confidence: medium
---

This is an RC application bug that SetUserFTA cannot fix. SetUserFTA would only fix OS-level protocol routing, not what RC does after receiving the URL.

**Assessment: The SetUserFTA approach cannot unblock these scenarios.** The failure is post-dispatch at the RC application layer, not at the OS dispatch layer. The brief's "final angle" assumption was wrong about the root cause.

**Per the brief's own escape hatch**: "If SetUserFTA doesn't unblock: accept defeat for protocol activation."

Additionally, the brief constraints say: "Single-shot. Don't iterate further on this."
