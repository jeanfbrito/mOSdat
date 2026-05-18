---
date: "2026-05-18"
project: mosdat
topic: Master-toggle scenario tests must accept routine: form after ff831bc conversion
kind: lesson
scope: project-shared
confidence: high
---

Commit `ff831bc refactor(scenarios): convert 3325-master-toggle to routines` rewrote `shared/scenarios/functional/3325-master-toggle.yaml` from inline steps to `routine:` calls. Five tests asserting on the OLD inline form failed:

- `test_scenario_phases.py::TestDemoScenario::test_master_toggle_phases_validate` — raw `ScenarioModel.model_validate` fails because `routine:` isn't an `AnyStep` variant (routines are expanded by `load_test_yaml` BEFORE schema validation).
- `test_step_imports.py::TestMasterToggleConversion::*` — checked for `import:` / `!import` syntax; scenario now uses `routine:`.
- `test_step_labels.py::TestMasterToggleLabels::test_first_step_has_a1_label` — expected `[import:...]` prefix on expanded labels; routine expansion uses `[routine:...]` prefix.

Fix: tests updated to accept routine-based form. Phase test uses `load_test_yaml` for expansion before assertion. Import-step and label-prefix tests accept both `[import:...]` and `[routine:...]` labels.

Takeaway: scenario format is a moving spec. Tests asserting on raw YAML shape break across refactors. Prefer asserting on the EXPANDED scenario (after `load_test_yaml`) when possible — that's the more stable contract.
