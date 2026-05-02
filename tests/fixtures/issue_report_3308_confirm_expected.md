# Issue 3308 — Screen share picker opens on every app launch

**Mode**: confirm
**Verdict**: ✅ **CONFIRMED**
**Run ID**: 2026-05-02_191002
**Run start**: 2026-05-02T19:10:02Z (`3` iterations on `fedora42`)
**Issue link**: https://github.com/RocketChat/Rocket.Chat.Electron/issues/3308
**Suspected PR**: [#3266](https://github.com/RocketChat/Rocket.Chat.Electron/pull/3266)

_Bug confirmed._

## Reproduction steps (from scenario)

1. Clean RC state (wipe config)
2. Launch RC via GNOME Activities
3. Verify RC window opens
4. Check if screen-share picker appears unprompted

## Verdict detail

| Iter | Precondition met | Bug visible | Outcome |
|------|------------------|-------------|---------|
| 1    | ✓                | ✓           | CONFIRMED |
| 2    | ✓                | ✓           | CONFIRMED |
| 3    | ✓                | ✓           | CONFIRMED |

## Environment

| Field          | Reporter (issue body) | This run     | Match  |
|----------------|-----------------------|--------------|--------|
| App version    | 4.14.0                | 4.14.0       | ✓     |
| Install        | flatpak               | flatpak      | ✓     |
| OS             | Fedora 43             | Fedora 42    | ≈     |
| Display server | wayland               | wayland      | ✓     |
| Ozone backend  | wayland               | x11          | ✗     |

## Smoking-gun evidence

![Bug visible](iter-1/bug-signal.png)

VLM verdict on this frame:
> A screen-share picker dialog with text mentioning 'share your screen' and Cancel/Share buttons is visible → **yes**

## Reproducibility

| Metric | Value |
|--------|-------|
| Total iterations | 3 |
| Confirmed (bug visible) | 3 |
| Not visible | 0 |
| Inconclusive (precondition failed) | 0 |
| Conclusive | 3/3 |
| Confidence | low |

## Per-iteration artifacts

- `iter-1/events.jsonl`, `iter-1/screenshots/`, `iter-1/vm-state.json`
- `iter-2/events.jsonl`, `iter-2/screenshots/`, `iter-2/vm-state.json`
- `iter-3/events.jsonl`, `iter-3/screenshots/`, `iter-3/vm-state.json`

## Reproducer command

```bash
mosdat confirm 3308 --vm fedora42 --iterations 3 --mode confirm
```

mosdat git revision: `1597085`

_If a fix lands, re-run with `--mode=verify-fix` to validate._
