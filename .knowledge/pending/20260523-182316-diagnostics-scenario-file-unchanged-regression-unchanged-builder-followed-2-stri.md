---
date: "2026-05-23"
project: mosdat
topic: Diagnostics scenario file unchanged. Regression unchanged. Builder followed 2-strike rule — diagnosed root cause,
kind: war-story
scope: project-shared
confidence: medium
---

- **(B)** Bisect PR commits to find when (if ever) diagnostics shipped to the bundle.
- **(C)** Leave the scenario as-is (13/13 PASS against current widgets, diagnostics block stays gated) and push the diagnostics fix on the PR side first.

Diagnostics scenario file unchanged. Regression unchanged. Builder followed 2-strike rule — diagnosed root cause, didn't keep trying to make a broken binary work.

User: your build is wrong, did you made a clean build from the actual origin PR? just tested here and is working

Assistant: User confirms PR build works. Our deploy fetched stale cached artifact OR wrong URL. Force clean rebuild.
