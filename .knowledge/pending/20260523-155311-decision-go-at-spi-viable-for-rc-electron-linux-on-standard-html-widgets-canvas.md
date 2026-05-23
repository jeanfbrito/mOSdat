---
date: "2026-05-23"
project: mosdat
topic: 'Decision: GO.** AT-SPI viable for RC.Electron Linux on standard HTML widgets. Canvas-rendered regions (chat message'
kind: decision
scope: project-shared
confidence: medium
---

Artifacts: `tests/_debug_atspi_poc_out/{tree.json,before.png,after.png,summary.json}`.

**Decision: GO.** AT-SPI viable for RC.Electron Linux on standard HTML widgets. Canvas-rendered regions (chat message list, emoji panel) still need VLM — keep as fallback. Next epic: wire AT-SPI driver into routine engine with click fallback chain (`atspi_id` → `coords` → VLM).

All 4 tasks done.

User: awesome, lets integrate everything then, making this the swiss knife so we have everything we need to test desktop app ultrathink
