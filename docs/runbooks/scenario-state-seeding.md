# Scenario State Seeding

How to pre-stage Rocket.Chat Electron settings before launching for a test.

## The two-file rule

RC's userData dir contains two files relevant to pre-staging:

| File | Who owns it | What goes here |
|------|-------------|----------------|
| `config.json` | RC (read + write) | Servers list, last selected URL, window bounds, migration version. We seed it once; RC overwrites it over its lifetime. |
| `overridden-settings.json` | mosdat (write); RC (read-only) | Any boolean or string we need to **guarantee** — feature flags, UI toggles, update settings. RC merges this on top of Redux state on every startup. |

**Rule: always use `overridden-settings.json` to force boolean settings. `config.json` is for state RC owns and may overwrite.**

## Why not config.json for flags?

RC uses Redux-persist for feature flags (`isTelephonyEnabled`, `isMenuBarEnabled`, etc.). Redux-persist reads from its own namespace (`__internal__`) on startup and rehydrates in-memory state from there — not from the top-level keys in `config.json`. A top-level write to `config.json` is silently ignored.

Additionally, if RC is not fully quit when we write `config.json`, its own state-save tick will overwrite our changes within seconds.

`overridden-settings.json` bypasses both: RC's `data.ts` merges it on every startup after Redux rehydration, so the values win regardless of prior state.

## Implementation

The `launch-rocketchat` routine writes both files. Pass `telephony_enabled: true/false` and other inputs; the routine handles the split automatically. See `shared/routines/launch-rocketchat.yaml` for the canonical implementation and inline comments.

## History

Before 2026-05-18, the routine wrote `isTelephonyEnabled` directly into `config.json`. This was observed to be ineffective (Redux ignored the top-level key). A later incorrect diagnosis blamed a SIGTRAP crash in the PR3325 branch; the actual root cause was the Redux-persist namespace issue described above. The `overridden-settings.json` approach is the canonical mechanism documented in RC's upstream README and `src/app/main/data.ts`.
