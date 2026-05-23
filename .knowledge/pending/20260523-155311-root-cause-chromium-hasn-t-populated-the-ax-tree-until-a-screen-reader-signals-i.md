---
date: "2026-05-23"
project: mosdat
topic: 'Root cause**: Chromium hasn''t populated the AX tree until a screen reader signals interest'
kind: war-story
scope: project-shared
confidence: medium
---

**Lazy tree initialization — CRITICAL:**
- **Symptom**: First call to `Atspi.Registry.get_desktop(0)` returns empty or sparse tree.
- **Root cause**: Chromium hasn't populated the AX tree until a screen reader signals interest.
- **Workaround**: 
  1. Always pass `--force-renderer-accessibility` at launch.
  2. After app launch, wait 1-2 seconds, then enumerate tree.
  3. If tree still empty, call `Atspi.Registry.is_enabled()` to verify AT-SPI bridge is live. If false, start `at-spi-bus-launcher`.
  4. Retry tree walk; should now be populated.
