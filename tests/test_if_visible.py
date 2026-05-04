"""Unit tests for FunctionalRunner if_visible step support.

Run with:  python -m pytest mOSdat/tests/test_if_visible.py -v
or standalone: python mOSdat/tests/test_if_visible.py
"""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub out heavy imports that functional_runner.py imports at module level so
# we can test without the full dependency tree installed.
# ---------------------------------------------------------------------------
for mod in ("PIL", "PIL.Image"):
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)
# PIL.Image needs an Image class attribute (used in type hints)
sys.modules["PIL.Image"].Image = object
sys.modules["PIL"].Image = sys.modules["PIL.Image"]

# Minimal stubs for the three local modules functional_runner imports
for stub in (
    "test_framework.automation.vlm.client",
    "test_framework.automation.vlm.input",
    "test_framework.automation.vlm.screenshot",
):
    if stub not in sys.modules:
        m = types.ModuleType(stub)
        m.VLMClient = object
        m.InputInjector = object
        m.Screenshotter = object
        sys.modules[stub] = m

# Ensure the package root is importable
_pkg_root = Path(__file__).parent.parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

# Import the module under test using its file path directly to avoid package
# resolution issues when running standalone.
import importlib.util as _ilu  # noqa: E402  # Must import after sys.path setup

_spec = _ilu.spec_from_file_location(
    "functional_runner",
    Path(__file__).parent.parent / "automation" / "runners" / "functional.py",
)
_mod = _ilu.module_from_spec(_spec)

# Patch the relative imports that functional_runner uses
_mod.__package__ = "automation.runners"
with patch.dict(
    sys.modules,
    {
        "automation.vlm.client": sys.modules["test_framework.automation.vlm.client"],
        "automation.vlm.input": sys.modules["test_framework.automation.vlm.input"],
        "automation.vlm.screenshot": sys.modules["test_framework.automation.vlm.screenshot"],
    },
):
    try:
        _spec.loader.exec_module(_mod)
    except ImportError:
        # Relative imports may still fail depending on how the file is run;
        # fall back to a direct exec after patching builtins.__import__.
        pass

FunctionalStep = _mod.FunctionalStep
FunctionalRunner = _mod.FunctionalRunner
StepFailed = _mod.StepFailed


def _make_runner(vlm_verify_return=False):
    """Return a FunctionalRunner with all dependencies mocked."""
    fake_image = MagicMock()

    vlm = MagicMock()
    vlm.verify.return_value = vlm_verify_return
    vlm.localize.return_value = (100, 200)

    screenshotter = MagicMock()
    screenshotter.capture.return_value = (fake_image, (1920, 1080))

    injector = MagicMock()

    runner = FunctionalRunner(
        vlm=vlm,
        screenshotter=screenshotter,
        injector=injector,
        screenshot_dir=None,
        log_fn=lambda msg: None,
    )
    return runner, vlm, injector


class TestIfVisibleSkip(unittest.TestCase):
    """if_visible=False → step body (then_steps) never executes."""

    def test_skip_when_not_visible(self):
        runner, vlm, injector = _make_runner(vlm_verify_return=False)

        then_step = FunctionalStep(localize="the Later button")
        step = FunctionalStep(
            if_visible="blue button labeled 'Later' or 'Dismiss'",
            then_steps=[then_step],
        )

        runner.run_step(step, 1)

        # VLM was queried for visibility
        vlm.verify.assert_called_once()
        # No click was issued — then_steps skipped
        injector.click.assert_not_called()

    def test_no_failure_when_not_visible(self):
        """Skipping must not raise StepFailed."""
        runner, _, _ = _make_runner(vlm_verify_return=False)
        step = FunctionalStep(
            if_visible="blue button labeled 'Later'",
            then_steps=[FunctionalStep(localize="the Later button")],
        )
        # Should complete without exception
        runner.run_step(step, 1)


class TestIfVisibleExecute(unittest.TestCase):
    """if_visible=True → then_steps execute (localize+click)."""

    def test_click_when_visible(self):
        runner, vlm, injector = _make_runner(vlm_verify_return=True)

        then_step = FunctionalStep(localize="button labeled 'Later'")
        step = FunctionalStep(
            if_visible="blue button labeled 'Later' or 'Dismiss'",
            then_steps=[then_step],
        )

        runner.run_step(step, 1)

        # The outer visibility check
        vlm.verify.assert_called_once()
        # then_step triggered localize → click
        vlm.localize.assert_called_once()
        injector.click.assert_called_once_with(100, 200, button=1)

    def test_multiple_then_steps_execute_in_order(self):
        runner, vlm, injector = _make_runner(vlm_verify_return=True)

        then_steps = [
            FunctionalStep(localize="the Dismiss button"),
            FunctionalStep(localize="the OK button"),
        ]
        step = FunctionalStep(
            if_visible="update dialog",
            then_steps=then_steps,
        )

        runner.run_step(step, 2)

        self.assertEqual(vlm.localize.call_count, 2)
        self.assertEqual(injector.click.call_count, 2)


class TestIfVisibleVLMError(unittest.TestCase):
    """VLM error during visibility check → treat as not visible, skip silently."""

    def test_vlm_error_treated_as_not_visible(self):
        runner, vlm, injector = _make_runner()
        vlm.verify.side_effect = RuntimeError("VLM timeout")

        step = FunctionalStep(
            if_visible="the Later button",
            then_steps=[FunctionalStep(localize="Later")],
        )

        # Must not raise
        runner.run_step(step, 1)
        injector.click.assert_not_called()


class TestLoadYamlIfVisible(unittest.TestCase):
    """load_test_yaml parses if_visible + then into FunctionalStep correctly."""

    def test_parse_if_visible_step(self):
        import yaml
        import io

        yaml_text = """
name: test
steps:
  - if_visible: "button labeled 'Later'"
    then:
      - localize: "button labeled 'Later'"
"""
        data = yaml.safe_load(io.StringIO(yaml_text))
        raw_step = data["steps"][0]

        step = _mod._parse_step(raw_step)

        self.assertEqual(step.if_visible, "button labeled 'Later'")
        self.assertIsNotNone(step.then_steps)
        self.assertEqual(len(step.then_steps), 1)
        self.assertEqual(step.then_steps[0].localize, "button labeled 'Later'")
        # All other fields default/None
        self.assertIsNone(step.localize)
        self.assertIsNone(step.launch)


if __name__ == "__main__":
    unittest.main()
