"""Tests for I4: implicit X11 preamble injection (automation/transport/x11_preamble.py).

Coverage:
- should_inject: true for GUI-tool bodies (xdotool, wmctrl, xclip, nohup-desktop)
- should_inject: false when DISPLAY+XAUTHORITY already exported
- should_inject: false for non-GUI bodies (rm, pkill, cat)
- inject: idempotent
- End-to-end with mock shell: body modified when x11=auto, untouched when x11=off
"""


# Clear sibling-test stub pollution BEFORE importing automation.*.
# Sibling tests (test_negative, test_concurrent_safety, test_proxmox_vm) stub
# heavy modules in sys.modules at their module import time, which runs during
# pytest collection — before our imports. Pop those stubs so we get the real
# modules below.
import sys as _sys
for _name in list(_sys.modules):
    if (
        _name.startswith("automation.transport")
        or _name.startswith("automation.vlm")
        or _name in (
            "openai",
            "httpx",
            "automation.config",
            "automation.proxmox.api",
            "automation.proxmox.vm",
            "automation.reporting.report",
        )
    ):
        _sys.modules.pop(_name, None)

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Direct import of the module under test (no __init__ chain needed)
# ---------------------------------------------------------------------------
_PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJ))

from automation.transport.x11_preamble import inject, should_inject, _PREAMBLE


# ---------------------------------------------------------------------------
# should_inject — positive cases (GUI tokens present, no prior export)
# ---------------------------------------------------------------------------

class TestShouldInjectPositive:
    def test_xdotool_click(self):
        body = "xdotool mousemove 100 200 click 1"
        assert should_inject(body) is True

    def test_wmctrl_list(self):
        body = "wmctrl -l | grep Rocket"
        assert should_inject(body) is True

    def test_xclip(self):
        body = "echo '+1234567890' | xclip -selection clipboard"
        assert should_inject(body) is True

    def test_xdg_mime(self):
        body = "xdg-mime query default x-scheme-handler/tel"
        assert should_inject(body) is True

    def test_xdg_open(self):
        body = "xdg-open tel:+491234567890"
        assert should_inject(body) is True

    def test_nohup_desktop(self):
        body = "nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox &"
        assert should_inject(body) is True

    def test_xset(self):
        body = "xset dpms force on"
        assert should_inject(body) is True

    def test_multiline_body_with_xdotool(self):
        body = "pkill -f Rocket || true\nxdotool key ctrl+a\n"
        assert should_inject(body) is True


# ---------------------------------------------------------------------------
# should_inject — negative: DISPLAY+XAUTHORITY already exported
# ---------------------------------------------------------------------------

class TestShouldInjectAlreadyExported:
    def test_both_exported_inline(self):
        body = (
            "export DISPLAY=:0 XAUTHORITY=/run/user/1000/gdm/Xauthority\n"
            "xdotool mousemove 100 200 click 1\n"
        )
        assert should_inject(body) is False

    def test_both_exported_separately(self):
        body = (
            "export DISPLAY=:0\n"
            "export XAUTHORITY=/tmp/xauth\n"
            "wmctrl -l\n"
        )
        assert should_inject(body) is False

    def test_only_display_exported_still_injects(self):
        """Missing XAUTHORITY → should still inject."""
        body = "export DISPLAY=:0\nxdotool key ctrl+a\n"
        assert should_inject(body) is True

    def test_only_xauthority_exported_still_injects(self):
        """Missing DISPLAY → should still inject."""
        body = 'export XAUTHORITY="/run/user/1000/.mutter-Xwaylandauth.abc"\nwmctrl -l\n'
        assert should_inject(body) is True

    def test_sentinel_blocks_reinject(self):
        """Preamble already present (sentinel) → no injection."""
        body = inject("xdotool click 1")
        assert should_inject(body) is False


# ---------------------------------------------------------------------------
# should_inject — negative: no GUI tokens
# ---------------------------------------------------------------------------

class TestShouldInjectNegative:
    def test_plain_rm(self):
        assert should_inject("rm -rf ~/.config/Rocket.Chat") is False

    def test_pkill_only(self):
        assert should_inject("pkill -f Rocket || true") is False

    def test_cat_file(self):
        assert should_inject("cat /etc/os-release") is False

    def test_empty_body(self):
        assert should_inject("") is False

    def test_nohup_without_desktop(self):
        # nohup present but path doesn't end in -desktop
        assert should_inject("nohup /usr/bin/some-other-app &") is False


# ---------------------------------------------------------------------------
# inject — idempotent
# ---------------------------------------------------------------------------

class TestInject:
    def test_prepends_preamble(self):
        body = "xdotool click 1"
        result = inject(body)
        assert result.startswith(_PREAMBLE)
        assert result.endswith(body)

    def test_idempotent(self):
        body = "xdotool click 1"
        once = inject(body)
        twice = inject(once)
        assert once == twice

    def test_non_gui_body_unchanged(self):
        body = "pkill -f Rocket || true"
        assert inject(body) == body

    def test_already_exported_unchanged(self):
        body = "export DISPLAY=:0 XAUTHORITY=/run/user/1000/gdm/Xauthority\nxdotool click 1\n"
        assert inject(body) == body


# ---------------------------------------------------------------------------
# End-to-end: FunctionalRunner shell dispatch with mock injector
# ---------------------------------------------------------------------------

class TestEndToEnd:
    """Verify that FunctionalRunner modifies shell body iff x11_mode == "auto"."""

    def _make_runner(self, x11_mode: str):
        """Build a minimal FunctionalRunner with mock dependencies."""
        from automation.runners.functional import FunctionalRunner
        from automation.runners.scenario_loader import FunctionalStep

        vlm = MagicMock()
        screenshotter = MagicMock()
        injector = MagicMock()
        screenshotter.capture.return_value = (MagicMock(), (1280, 800))

        runner = FunctionalRunner(
            vlm=vlm,
            screenshotter=screenshotter,
            injector=injector,
            screenshot_dir=None,
            log_fn=lambda _: None,
            x11_mode=x11_mode,
        )
        return runner, injector, FunctionalStep

    def test_shell_body_injected_when_auto(self):
        runner, injector, FunctionalStep = self._make_runner("auto")
        step = FunctionalStep(shell="xdotool click 1")
        runner.run_step(step, 1)
        called_body = injector.shell_result.call_args[0][0]
        assert called_body.startswith(_PREAMBLE)
        assert "xdotool click 1" in called_body

    def test_shell_body_untouched_when_off(self):
        runner, injector, FunctionalStep = self._make_runner("off")
        original = "xdotool click 1"
        step = FunctionalStep(shell=original)
        runner.run_step(step, 1)
        called_body = injector.shell_result.call_args[0][0]
        assert called_body == original

    def test_non_gui_body_untouched_even_when_auto(self):
        runner, injector, FunctionalStep = self._make_runner("auto")
        original = "pkill -f Rocket.Chat || true"
        step = FunctionalStep(shell=original)
        runner.run_step(step, 1)
        called_body = injector.shell_result.call_args[0][0]
        assert called_body == original

    def test_already_exported_body_untouched_when_auto(self):
        runner, injector, FunctionalStep = self._make_runner("auto")
        original = (
            "export DISPLAY=:0 XAUTHORITY=/run/user/1000/gdm/Xauthority\n"
            "xdotool click 1\n"
        )
        step = FunctionalStep(shell=original)
        runner.run_step(step, 1)
        called_body = injector.shell_result.call_args[0][0]
        assert called_body == original
