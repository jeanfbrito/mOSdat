---
date: "2026-05-23"
project: mosdat
topic: 'Key decision**: pointer-mode click on the Telephony check box failed because the AT-SPI node is zero-extent (1×1 px'
kind: decision
scope: project-shared
confidence: low
---

- `/home/jean/projects/linux-testing/mOSdat/shared/scenarios/functional/3325-diagnostics-panel.yaml` — same conversion at the Voice &amp; Video toggle location.
- `/home/jean/projects/linux-testing/mOSdat/.claude/mytasks/findings.md` — appended findings under `# Telephony toggle pointer-mode (2026-05-22)`.

**Key decision**: pointer-mode click on the Telephony check box failed because the AT-SPI node is zero-extent (1×1 px hidden `&lt;input&gt;` — the Fuselage track widget has no a11y role). Per docs caveat, fell back to `via: action` for THIS one click. Every other click in both scenarios remains pointer-mode (visible cursor in recording). Added `- wait: 1` settle after action click because it returns in ~100ms vs pointer's ~2.5s — modal portal needs time to mount.

**Live results**: both scenarios PASS.
**Pytest**: `1067 passed, 5 skipped, 3 xfailed` (matches floor).

**User caveat**: user's literal ask (visible cursor on the toggle click itself) is not achievable without targeting a non-check-box ancestor or pixel coords — Fuselage AT-SPI node has no geometry. Documented in YAML comments + findings with 3 follow-up options.
