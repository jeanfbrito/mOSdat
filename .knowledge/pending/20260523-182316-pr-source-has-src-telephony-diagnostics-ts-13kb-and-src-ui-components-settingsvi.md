---
date: "2026-05-23"
project: mosdat
topic: PR source has `src/telephony/diagnostics.ts` (13KB) and `src/ui/components/SettingsView/VoiceVideoTab.tsx`. They exist
kind: war-story
scope: project-shared
confidence: medium
---

- `isDefault.tel`, `isDefault.callto`
- Settings tabs found in i18n: only `General`, `Certificates`, `Developer`

PR source has `src/telephony/diagnostics.ts` (13KB) and `src/ui/components/SettingsView/VoiceVideoTab.tsx`. They exist on disk but **don't reach the renderer bundle**. Likely root causes:

1. `SettingsView.tsx` imports `VoiceVideoTab` but the tab isn't wired into the actual tab list, OR
2. CI merge against current master dropped the wiring during conflict resolution

**You are the PR author** (`jeanfbrito` per the `feat/telephony-deeplink` branch). Tree-shake likely killed the unused export.
