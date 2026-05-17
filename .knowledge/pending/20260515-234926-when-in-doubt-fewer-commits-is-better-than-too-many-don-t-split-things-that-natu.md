---
date: "2026-05-15"
project: mosdat
topic: When in doubt, fewer commits is better than too many. Don't split things that naturally belong together just because
kind: decision
scope: project-shared
confidence: low
---

Use your judgment. If a test file was added alongside the feature code it tests, they belong in the same commit — they serve the same purpose. But if someone fixed a flaky spec that's unrelated to the feature work, that's a separate commit.

When in doubt, fewer commits is better than too many. Don't split things that naturally belong together just because they're in different directories.

### 3. Create commits

For each group, stage the relevant files and commit. Use native git commands only — this ensures the user's configured git identity (name, email, GPG signing) is used correctly.
