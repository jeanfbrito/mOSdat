# Test Strategy — Scoped-Tests-First Workflow

## Inner loop (pre-flight, <5 s)

Before doing anything else, run:

```bash
tools/run-scoped-tests.sh HEAD
```

This maps changed `automation/**/*.py` files to their likely test counterparts and runs only those. Must be green before proceeding with further changes.

If the scoper emits `__FULL__`, that means `automation/config.py` changed and the full suite IS the scoped phase — run it, don't skip it.

## Final gate (mandatory)

Before reporting "done", run the full suite and report counts vs baseline:

```bash
python -m pytest tests/ -q
```

No exception even for one-line changes. This repo has historical cross-file fixture coupling (PIL re-import bug commit d05b9c1, live_dashboard port binding) — the full suite is the only reliable safety net.

## Parallel execution (opt-in only)

Full-suite runs may add `-n auto` if you are confident the change is parallel-safe:

```bash
python -m pytest tests/ -q -n auto
```

Default: OFF. Do NOT add `-n auto` to `addopts` in `pyproject.toml`. Enable per-invocation only after verifying no shared global state (ports, PIL module-level caches, file fixtures) is affected.

## Trivial-change exception

If a change is **comments-only or docs-only** (no executable lines touched), you may state that explicitly and skip the full suite. Default: do not skip. When in doubt, run the full suite.

## Summary

| Phase | Command | When |
|---|---|---|
| Inner loop | `tools/run-scoped-tests.sh HEAD` | Before every change |
| `__FULL__` sentinel | `python -m pytest tests/ -q` | When scoper says so |
| Final gate | `python -m pytest tests/ -q` | Before reporting done |
| Parallel (opt-in) | `python -m pytest tests/ -q -n auto` | When parallel-safe confirmed |
