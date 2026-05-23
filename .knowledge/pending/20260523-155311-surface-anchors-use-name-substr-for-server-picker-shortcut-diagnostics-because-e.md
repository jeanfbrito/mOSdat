---
date: "2026-05-23"
project: mosdat
topic: Surface anchors use `name_substr`** for server picker / shortcut / diagnostics because exact PR-build labels haven't
kind: insight
scope: project-shared
confidence: medium
---

- **AT-SPI pointer mode default**: all interaction steps use `atspi:` / `verify_atspi:` / `wait_for:`. Zero VLM fallback. Default pointer mode (implicit).
- **Tab = Voice &amp; Video**: matches authoring rule 4 + PR3325 head label drift fix.
- **Layout step 5**: `wmctrl -e` resize to `820x720` (after unmaximize) + re-verify all four surface anchors. Clipped widgets drop out of the AT-SPI tree → re-find failure IS the TEL-QA-013 "Failure Signals" detector.
- **Surface anchors use `name_substr`** for server picker / shortcut / diagnostics because exact PR-build labels haven't been live-discovered yet — fragmentary `any:` matchers tolerate copy drift while still failing on absence.

**Tests**: none added — author-only, validator-only verification per brief.

**Deferred**: pt-BR + de-DE live assertions (single-locale constraint, documented).
