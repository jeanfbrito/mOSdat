---
date: "2026-05-18"
project: mosdat
topic: Two pytest_collection_modifyitems in conftest.py — last def silently overrides first
kind: war-story
scope: project-shared
confidence: high
---

Added a `pytest_collection_modifyitems` hook to `tests/conftest.py` to reorder stub-polluting test files to the end of the suite. The hook never fired — added debug print, no output; added file-write, no file created.

Root cause: `tests/conftest.py` already had a `pytest_collection_modifyitems(config, items)` 200 lines later for the `--live` marker skipping pass. Python module-level: a second `def` with the same name overrides the first. pytest registers only the latter.

Fix: merge logic into a single hook. Factor each concern into a helper (`_reorder_polluting_last(items)`), call them sequentially from the merged dispatcher.

Takeaway: one named function per pytest hook per conftest.py. When you need multiple concerns in one hook, factor each into a helper and call them from a single named dispatcher. This is a Python module-level fact, not pytest-specific.
