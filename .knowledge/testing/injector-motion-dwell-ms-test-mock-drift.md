---
date: "2026-05-18"
project: mosdat
topic: InputInjector.click/hover signature drift broke test_runner_dispatch + test_if_visible
kind: lesson
scope: project-shared
confidence: high
---

Commit `38440e8 feat(mosdat): M1-M8 human-like cursor motion` added `motion` and `dwell_ms` kwargs to `InputInjector.click()` and `.hover()`. The localize-step dispatcher in `automation/runners/functional_steps.py` also switched from `injector.move(...)` to `injector.hover(...)`. Tests still asserted the old call signatures.

Failures:
- `test_runner_dispatch.TestLocalizeStep::test_localize_issues_click` — `assert_called_once_with(200, 300, button=1)` missed new kwargs.
- `test_runner_dispatch.TestLocalizeStep::test_localize_issues_hover_without_click` — asserted on `injector.move.assert_called_once_with(...)` but code now calls `.hover(...)`.
- `test_if_visible.TestIfVisibleExecute::test_click_when_visible` — same kwarg drift.

Fix: update assertions to include `motion=None, dwell_ms=None` and swap `injector.move` → `injector.hover` where production code did. No production code changed.

Takeaway: when extending a public injector/runner method, grep callers + tests in same PR. Mock-based tests pin the OLD signature; they need updating in lock-step with production.
