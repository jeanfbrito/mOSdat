---
date: "2026-04-14"
project: mosdat
topic: 'The key insight for reliability: **don''t use navigation mode**. Use localization mode per step, and handle the "what to'
kind: decision
scope: project-shared
confidence: low
accessed: 3
last_accessed: "2026-05-01"
---

**2. Navigation** — full agentic multi-step reasoning (this is the 39.9% OSWorld score)

The key insight for reliability: **don't use navigation mode**. Use localization mode per step, and handle the "what to do next" logic yourself in the test script. The 4B model is excellent at localization specifically because that's what it was explicitly trained for.

---

## The Architecture That Makes 4B Reliable
