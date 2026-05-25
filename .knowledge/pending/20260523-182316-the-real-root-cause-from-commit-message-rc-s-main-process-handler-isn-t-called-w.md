---
date: "2026-05-23"
project: mosdat
topic: 'The real root cause** (from commit message): "RC''s main-process handler isn''t called when the URL arrives via'
kind: war-story
scope: project-shared
confidence: medium
---

The brief's SetUserFTA approach was predicated on fixing OS protocol dispatch. But the latest commit already tried direct argv dispatch and that also failed. The dispatch method is not the issue — **RC's application logic doesn't show the workspace picker when dispatched from second-instance IPC**.

**The real root cause** (from commit message): "RC's main-process handler isn't called when the URL arrives via second-instance IPC while Settings is open, OR the single-instance IPC channel itself is misconfigured."

This is an RC application bug that SetUserFTA cannot fix. SetUserFTA would only fix OS-level protocol routing, not what RC does after receiving the URL.

**Assessment: The SetUserFTA approach cannot unblock these scenarios.** The failure is post-dispatch at the RC application layer, not at the OS dispatch layer. The brief's "final angle" assumption was wrong about the root cause.
