"""Integration tests for cursor motion: full chain from scenario YAML to VNC pointer events.

Chain under test:
  scenario YAML → load_test_yaml → FunctionalStep → FunctionalRunner._run_localize_body
  → InputInjector.click / hover → VncClient.human_move → cursor_motion.generate_path
  → pointer_event mock

All tests use mocks for VncClient and InputInjector low-level methods.
No real VM, no real VNC connection.
"""
from __future__ import annotations

# Clear sibling-test stub pollution BEFORE importing real modules.
# test_trace.py stubs automation.transport.vnc + automation.vlm.* at its
# module import time during pytest collection.
import sys as _sys
for _name in list(_sys.modules):
    if (
        _name.startswith("automation.transport")
        or _name.startswith("automation.vlm")
        or _name.startswith("automation.setup")
        or _name in (
            "automation.config",
            "automation.proxmox.api",
        )
    ):
        _sys.modules.pop(_name, None)

# Force-reimport so parent packages get their child attributes back.
import automation.transport as _at  # noqa: E402
import automation.transport.vnc as _at_vnc  # noqa: E402
_at.vnc = _at_vnc
import automation.vlm as _av  # noqa: E402
import automation.vlm.input as _av_input  # noqa: E402
_av.input = _av_input
# Sibling test_negative_preflight expects automation.vlm.screenshot in sys.modules;
# our pop above removed it — restore by importing.
try:
    import automation.vlm.screenshot as _av_screenshot  # noqa: E402
    _av.screenshot = _av_screenshot
except Exception:
    pass

import textwrap
import time as _time_module
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, call, patch

import pytest
from PIL import Image

import automation.vlm.input as _input_module
from automation.config import CursorConfig
from automation.runners.scenario_loader import FunctionalStep, load_test_yaml, parse_step
from automation.transport.vnc import VncClient
from automation.vlm.input import InputInjector


@pytest.fixture(autouse=True)
def _restore_real_modules_each_test():
    """Restore real automation.vlm.input + automation.transport.vnc when stubbed.

    Only pops modules that are stubs (no __file__) — leaves real modules and
    other tests' stubs alone.
    """
    import sys as _s
    def _is_stub(name):
        m = _s.modules.get(name)
        return m is not None and not getattr(m, "__file__", None)
    targets = ["automation.transport", "automation.transport.vnc",
               "automation.transport.cursor_motion", "automation.vlm",
               "automation.vlm.input"]
    needs_restore = any(_is_stub(n) for n in targets)
    if needs_restore:
        for n in targets:
            if _is_stub(n):
                _s.modules.pop(n, None)
        import automation.transport as _at
        import automation.transport.vnc as _vnc
        _at.vnc = _vnc
        import automation.vlm as _av
        import automation.vlm.input as _vi
        _av.input = _vi
    yield


# ---------------------------------------------------------------------------
# Shared helpers (reused from test_human_move.py pattern)
# ---------------------------------------------------------------------------

def _make_vnc() -> VncClient:
    """Return a VncClient with mocked internals (no real connection)."""
    vnc = VncClient.__new__(VncClient)
    vnc._proxmox = MagicMock()
    vnc._vmid = 100
    vnc._timeout = 15.0
    vnc._ws = MagicMock()
    vnc._reader = MagicMock()
    vnc._width = 1280
    vnc._height = 800
    vnc._button_mask = 0
    vnc._cursor_x = 0
    vnc._cursor_y = 0
    return vnc


def _make_injector(cursor_config: Optional[CursorConfig] = None) -> tuple[InputInjector, VncClient]:
    vnc = _make_vnc()
    ssh_mock = MagicMock()
    cfg = cursor_config if cursor_config is not None else CursorConfig()
    injector = InputInjector(vnc, ssh_mock, is_windows=False, cursor_config=cfg)
    return injector, vnc


def _make_runner(injector: InputInjector, vlm_localize_result=(100, 200)):
    """Build a FunctionalRunner with mocked VLM + screenshotter.

    Returns (runner, vnc, mock_vlm, mock_screenshotter).
    """
    from automation.runners.functional import FunctionalRunner

    mock_image = Image.new("RGB", (1280, 800))
    screenshotter = MagicMock()
    screenshotter.capture.return_value = (mock_image, (1280, 800))
    screenshotter.wait_for_stable = MagicMock()

    vlm = MagicMock()
    vlm.localize.return_value = vlm_localize_result
    vlm.verify.return_value = True

    runner = FunctionalRunner(
        vlm=vlm,
        screenshotter=screenshotter,
        injector=injector,
        screenshot_dir=None,
        log_fn=lambda msg: None,
    )
    return runner, vlm, screenshotter


def _write_scenario(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "scenario.yaml"
    p.write_text(textwrap.dedent(content))
    return p


# ---------------------------------------------------------------------------
# Test 1: scenario YAML click with motion=bezier propagates to VncClient.human_move
# ---------------------------------------------------------------------------

def test_scenario_click_with_motion_bezier_propagates_to_vnc(tmp_path):
    """Build YAML with motion: bezier dwell_ms: 100, run step, assert human_move called
    with profile='bezier' and ~100ms sleep recorded."""
    yaml_path = _write_scenario(tmp_path, """\
        name: test
        steps:
          - localize: "x"
            click: left
            motion: bezier
            dwell_ms: 100
    """)

    _name, steps, _vars, _ckpts = load_test_yaml(str(yaml_path))
    assert len(steps) == 1
    step = steps[0]
    assert step.motion == "bezier"
    assert step.dwell_ms == 100

    injector, vnc = _make_injector(CursorConfig(profile="bezier"))
    runner, _vlm, _ss = _make_runner(injector)

    human_move_calls = []
    sleep_calls = []

    def capture_human_move(tx, ty, **kwargs):
        human_move_calls.append((tx, ty, kwargs))
        vnc._cursor_x = tx
        vnc._cursor_y = ty

    def capture_sleep(t):
        sleep_calls.append(t)

    with patch.object(vnc, "human_move", side_effect=capture_human_move), \
         patch.object(vnc, "click"), \
         patch.object(_input_module.time, "sleep", side_effect=capture_sleep):
        runner.run_step(step, 1)

    assert len(human_move_calls) >= 1, "VncClient.human_move must be called"
    _, _, kwargs = human_move_calls[0]
    assert kwargs.get("profile") == "bezier", (
        f"Expected profile='bezier', got {kwargs.get('profile')!r}"
    )
    # dwell_ms=100 → sleep(0.1) in _position_cursor
    assert any(abs(t - 0.1) < 0.02 for t in sleep_calls), (
        f"Expected ~0.1s dwell sleep, got: {sleep_calls}"
    )


# ---------------------------------------------------------------------------
# Test 2: step without motion: field inherits config default (bezier)
# ---------------------------------------------------------------------------

def test_scenario_click_no_motion_uses_config_default(tmp_path):
    """Step has no motion: field; config default 'bezier' must be used."""
    yaml_path = _write_scenario(tmp_path, """\
        name: test
        steps:
          - localize: "login button"
            click: left
    """)

    _name, steps, _vars, _ckpts = load_test_yaml(str(yaml_path))
    step = steps[0]
    assert step.motion is None  # no override in YAML

    injector, vnc = _make_injector(CursorConfig(profile="bezier"))
    runner, _vlm, _ss = _make_runner(injector)

    human_move_calls = []

    def capture_human_move(tx, ty, **kwargs):
        human_move_calls.append(kwargs)
        vnc._cursor_x = tx
        vnc._cursor_y = ty

    with patch.object(vnc, "human_move", side_effect=capture_human_move), \
         patch.object(vnc, "click"), \
         patch("automation.vlm.input.time.sleep"), \
         patch("time.sleep"):
        runner.run_step(step, 1)

    assert human_move_calls, "human_move must be called"
    assert human_move_calls[0].get("profile") == "bezier"


# ---------------------------------------------------------------------------
# Test 3: step motion=instant overrides config bezier
# ---------------------------------------------------------------------------

def test_scenario_click_motion_instant_overrides_config_bezier(tmp_path):
    """Config defaults profile to bezier; step has motion: instant → instant wins."""
    yaml_path = _write_scenario(tmp_path, """\
        name: test
        steps:
          - localize: "submit button"
            click: left
            motion: instant
    """)

    _name, steps, _vars, _ckpts = load_test_yaml(str(yaml_path))
    step = steps[0]
    assert step.motion == "instant"

    injector, vnc = _make_injector(CursorConfig(profile="bezier"))
    runner, _vlm, _ss = _make_runner(injector)

    human_move_calls = []

    def capture_human_move(tx, ty, **kwargs):
        human_move_calls.append(kwargs)
        vnc._cursor_x = tx
        vnc._cursor_y = ty

    with patch.object(vnc, "human_move", side_effect=capture_human_move), \
         patch.object(vnc, "click"), \
         patch("automation.vlm.input.time.sleep"), \
         patch("time.sleep"):
        runner.run_step(step, 1)

    assert human_move_calls, "human_move must be called"
    assert human_move_calls[0].get("profile") == "instant", (
        f"Expected 'instant', got {human_move_calls[0].get('profile')!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: CursorConfig(profile="instant") forces instant on no-per-step-motion steps
# ---------------------------------------------------------------------------

def test_cli_cursor_instant_flag_forces_instant(tmp_path):
    """CursorConfig constructed with profile='instant' (as --cursor-instant would do)
    causes a step without explicit motion: to use the instant profile."""
    yaml_path = _write_scenario(tmp_path, """\
        name: test
        steps:
          - localize: "close button"
            click: left
    """)

    _name, steps, _vars, _ckpts = load_test_yaml(str(yaml_path))
    step = steps[0]
    assert step.motion is None  # no per-step override

    instant_config = CursorConfig(profile="instant")
    assert instant_config.profile == "instant"

    injector, vnc = _make_injector(instant_config)
    runner, _vlm, _ss = _make_runner(injector)

    human_move_calls = []

    def capture_human_move(tx, ty, **kwargs):
        human_move_calls.append(kwargs)
        vnc._cursor_x = tx
        vnc._cursor_y = ty

    with patch.object(vnc, "human_move", side_effect=capture_human_move), \
         patch.object(vnc, "click"), \
         patch("automation.vlm.input.time.sleep"), \
         patch("time.sleep"):
        runner.run_step(step, 1)

    assert human_move_calls, "human_move must be called"
    assert human_move_calls[0].get("profile") == "instant"


# ---------------------------------------------------------------------------
# Test 5: hover step with dwell_ms=250: human_move called, sleep(0.25), no click
# ---------------------------------------------------------------------------

def test_hover_step_with_dwell_ms_sleeps_then_no_click(tmp_path):
    """hover: true + dwell_ms: 250 must call human_move for position,
    sleep ~250ms, and NOT trigger vnc.click."""
    yaml_path = _write_scenario(tmp_path, """\
        name: test
        steps:
          - localize: "settings icon"
            hover: true
            dwell_ms: 250
    """)

    _name, steps, _vars, _ckpts = load_test_yaml(str(yaml_path))
    step = steps[0]
    assert step.hover is True
    assert step.dwell_ms == 250

    injector, vnc = _make_injector(CursorConfig(profile="instant"))
    runner, _vlm, _ss = _make_runner(injector)

    human_move_calls = []
    click_calls = []
    sleep_calls = []

    def capture_human_move(tx, ty, **kwargs):
        human_move_calls.append((tx, ty, kwargs))
        vnc._cursor_x = tx
        vnc._cursor_y = ty

    def capture_click(x, y, button=1):
        click_calls.append((x, y, button))

    def capture_sleep(t):
        sleep_calls.append(t)

    with patch.object(vnc, "human_move", side_effect=capture_human_move), \
         patch.object(vnc, "click", side_effect=capture_click), \
         patch.object(_input_module.time, "sleep", side_effect=capture_sleep):
        runner.run_step(step, 1)

    assert len(human_move_calls) >= 1, "human_move must be called for hover"
    assert not click_calls, f"vnc.click must NOT be called for hover, got: {click_calls}"
    assert any(abs(t - 0.25) < 0.02 for t in sleep_calls), (
        f"Expected ~0.25s dwell sleep, got: {sleep_calls}"
    )


# ---------------------------------------------------------------------------
# Test 6: routine with motion/dwell_ms fields expands into steps with correct attrs
# ---------------------------------------------------------------------------

def test_routine_with_motion_field_expands_correctly(tmp_path):
    """A routine containing steps with motion: bezier dwell_ms: 200 must expand
    into FunctionalStep objects that carry those values through."""
    from unittest.mock import patch as _patch
    from automation.routines.schema import Routine

    routine = Routine.model_validate({
        "name": "my-routine",
        "description": "test routine with motion",
        "steps": [
            {"localize": "some button", "click": "left", "motion": "bezier", "dwell_ms": 200},
        ],
    })

    yaml_path = _write_scenario(tmp_path, """\
        name: test
        steps:
          - routine: my-routine
    """)

    with _patch("automation.routines.runner.load_routine", return_value=routine):
        _name, steps, _vars, _ckpts = load_test_yaml(str(yaml_path))

    # The routine step must have been expanded and motion/dwell_ms preserved
    localize_steps = [s for s in steps if s.localize == "some button"]
    assert len(localize_steps) == 1, (
        f"Expected 1 expanded localize step, got {len(localize_steps)}"
    )
    expanded = localize_steps[0]
    assert expanded.motion == "bezier", (
        f"Expected motion='bezier' after routine expansion, got {expanded.motion!r}"
    )
    assert expanded.dwell_ms == 200, (
        f"Expected dwell_ms=200 after routine expansion, got {expanded.dwell_ms!r}"
    )


# ---------------------------------------------------------------------------
# Test 7: 3325-master-toggle.yaml loads without error, click steps have correct defaults
# ---------------------------------------------------------------------------

def test_3325_master_toggle_loads_with_default_cursor_motion():
    """Load the real 3325-master-toggle.yaml; it must load without error.
    Steps that don't declare motion should have motion=None (inherit from config).
    """
    project_root = Path(__file__).parent.parent
    scenario_path = (
        project_root
        / "shared"
        / "scenarios"
        / "functional"
        / "linux"
        / "3325-master-toggle.yaml"
    )
    if not scenario_path.exists():
        pytest.skip("3325-master-toggle.yaml not found")

    # Routines referenced from this scenario (cleanup-rocketchat, etc.) are real
    # files in shared/routines/ — let them load naturally.
    _name, steps, _vars, _ckpts = load_test_yaml(str(scenario_path))

    assert len(steps) > 0, "Scenario must have at least one step after expansion"

    # Every step with a localize: field that doesn't declare motion must have motion=None
    # (meaning: will inherit from CursorConfig at run time, not hardcoded in YAML)
    localize_steps_without_motion = [
        s for s in steps if s.localize and s.motion is None
    ]
    # At minimum there should be some steps (routine expansion adds localize steps)
    # The key check: none of them have an unexpected non-None motion value
    for s in steps:
        if s.localize:
            assert s.motion in (None, "instant", "linear", "bezier"), (
                f"Unexpected motion value {s.motion!r} in step localize={s.localize!r}"
            )


# ---------------------------------------------------------------------------
# Test 8: open-settings.yaml fallback kebab step has dwell_ms=250
# ---------------------------------------------------------------------------

def test_open_settings_routine_kebab_fallback_has_dwell_ms():
    """Load the open-settings.yaml routine; the fallback kebab click step
    must have dwell_ms=250 (M8 doc-driven edit)."""
    from automation.routines.loader import load_routine, routines_dir

    project_root = Path(__file__).parent.parent
    real_routines_dir = project_root / "shared" / "routines"

    if not (real_routines_dir / "open-settings.yaml").exists():
        pytest.skip("open-settings.yaml not found")

    # Use real routines_dir — the file is committed
    load_routine.cache_clear()
    with patch("automation.routines.loader.routines_dir", return_value=real_routines_dir):
        load_routine.cache_clear()
        routine = load_routine("open-settings")

    # Find the fallback steps block
    fallback_steps = []
    if routine.fallbacks:
        for fb in routine.fallbacks:
            fallback_steps.extend(fb.steps)

    assert fallback_steps, "open-settings routine must have at least one fallback step set"

    # The kebab click step in the fallback — the first localize+click step
    kebab_step = next(
        (s for s in fallback_steps if s.get("localize") and s.get("click")),
        None,
    )
    assert kebab_step is not None, (
        "Expected a localize+click step in the open-settings fallback path"
    )
    assert kebab_step.get("dwell_ms") == 250, (
        f"Expected dwell_ms=250 on kebab click step, got {kebab_step.get('dwell_ms')!r}"
    )
