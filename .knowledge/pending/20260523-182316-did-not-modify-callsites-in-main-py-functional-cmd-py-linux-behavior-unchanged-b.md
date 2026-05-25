---
date: "2026-05-23"
project: mosdat
topic: Did **not** modify callsites in `main.py`/`functional_cmd.py` — Linux behavior unchanged because `platform` defaults
kind: decision
scope: project-shared
confidence: low
---

**Key decisions**:
- Used a single `windows/` bucket (covers windows10 + windows11) keyed off `VMConfig.is_windows` semantics (`os_type == "windows"`).
- Resolver kept lru_cache (now keys on `(slug, platform)`).
- Did **not** modify callsites in `main.py`/`functional_cmd.py` — Linux behavior unchanged because `platform` defaults to `None`. Wiring the VM platform through belongs to the runner-side ticket per brief framing.
- Existing Linux routines at `shared/routines/*.yaml` untouched per constraint.

**Tests**: 12 new in `test_routine_resolution.py` (all named in findings). Regression: `1118 passed, 5 skipped, 3 xfailed, 0 failed` (above ≥1077/5/3/0 floor).

**Deferred**: Wiring `vm.os_type` into `load_test_yaml` call sites — separate runner ticket.</result>
