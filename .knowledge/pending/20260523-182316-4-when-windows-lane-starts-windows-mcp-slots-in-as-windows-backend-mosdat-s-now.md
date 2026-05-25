---
date: "2026-05-23"
project: mosdat
topic: 4. **When Windows lane starts:** Windows-MCP slots in as Windows backend. mOSdat's now-AT-SPI-aware Linux backend = its
kind: decision
scope: project-shared
confidence: low
---

1. **Now (Linux flake reduction):** AT-SPI snapshot driver + click fallback chain. Single highest-ROI change. Cuts VLM dependence on Rocket.Chat.Electron's accessible UI (most of it).
2. **Soon:** unified WaitFor, enriched capture payload.
3. **Later:** display selection, tool gating.
4. **When Windows lane starts:** Windows-MCP slots in as Windows backend. mOSdat's now-AT-SPI-aware Linux backend = its mirror. Platform adapter from prior plan integrates cleanly because both sides speak same `snapshot + screenshot + waitfor + element-id-click` vocabulary.

**Net:** Linux scenarios get less flaky immediately, Windows adoption becomes drop-in later. Best of both, no premature Windows code, no third-party Linux dependency.

User: before doing this our main way, test if it works as intended so then we wire it inside the mosdat
