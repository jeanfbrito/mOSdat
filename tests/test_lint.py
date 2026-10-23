"""Unit tests for automation.commands.lint (F1a).

12 tests covering each WARN rule + a clean-pass case.
No live VM or VNC connection required.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub automation.transport.vnc so lint imports without Crypto/websockets.
# We do NOT clear automation.commands.lint from sys.modules — instead we patch
# _KEYSYMS / _MODIFIERS directly in the module globals after import.
# ---------------------------------------------------------------------------
for _mod in list(sys.modules):
    if _mod.startswith("automation.transport.vnc"):
        sys.modules.pop(_mod, None)

_vnc_stub = types.ModuleType("automation.transport.vnc")
_vnc_stub._KEYSYMS = {
    "enter": 0xff0d, "return": 0xff0d, "tab": 0xff09,
    "escape": 0xff1b, "esc": 0xff1b, "backspace": 0xff08,
    "delete": 0xffff, "home": 0xff50, "end": 0xff57,
    "pgup": 0xff55, "pgdn": 0xff56,
    "up": 0xff52, "down": 0xff54, "left": 0xff51, "right": 0xff53,
    "insert": 0xff63, "space": 0x0020,
    "f1": 0xffbe, "f2": 0xffbf, "f3": 0xffc0, "f4": 0xffc1,
    "f5": 0xffc2, "f6": 0xffc3, "f7": 0xffc4, "f8": 0xffc5,
    "f9": 0xffc6, "f10": 0xffc7, "f11": 0xffc8, "f12": 0xffc9,
    "comma": 0x002c,
}
_vnc_stub._MODIFIERS = {
    "shift": 0xffe1,
    "ctrl": 0xffe3, "control": 0xffe3,
    "alt": 0xffe9,
    "meta": 0xffe7,
    "super": 0xffeb, "win": 0xffeb,
}
sys.modules["automation.transport.vnc"] = _vnc_stub

import automation.commands.lint as lint_mod  # noqa: E402

# Force (re-)populate the module globals with our test keysyms.
# This is necessary because lint_mod may have already been imported by
# another test file before our stub was installed, leaving stale values.
lint_mod._KEYSYMS = dict(_vnc_stub._KEYSYMS)
lint_mod._MODIFIERS = dict(_vnc_stub._MODIFIERS)

from automation.commands.lint import (  # noqa: E402
    LintDiag,
    check_config_json_migrations,
    check_heredoc_in_yaml,
    check_key_combos,
    check_schema_validation,
    check_settings_toggle_pattern,
    check_step_type_discriminator,
    check_tel_dispatch_xauthority,
    check_transient_localize,
    check_xdotool_coords,
    run_lint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lines(text: str) -> list[str]:
    return text.strip().splitlines()


def _warns(diags: list[LintDiag]) -> list[LintDiag]:
    return [d for d in diags if d.severity == "WARN"]


# ---------------------------------------------------------------------------
# Check 1 — key combo validation
# ---------------------------------------------------------------------------

class TestKeyComboCheck:
    def test_valid_combo_ctrl_comma(self):
        lines = _lines("    then_key: ctrl+comma")
        assert check_key_combos(lines, "f.yaml") == []

    def test_valid_combo_ctrl_f(self):
        lines = _lines("    key: ctrl+f")
        assert check_key_combos(lines, "f.yaml") == []

    def test_valid_combo_alt_f10(self):
        lines = _lines("    then_key: F10")
        assert check_key_combos(lines, "f.yaml") == []

    def test_invalid_combo_ctrl_plus(self):
        """ctrl+plus is not a valid keysym name."""
        lines = _lines("    then_key: ctrl+plus")
        diags = _warns(check_key_combos(lines, "f.yaml"))
        assert len(diags) == 1
        assert "key-combo" in diags[0].rule

    def test_invalid_combo_alt_plus(self):
        lines = _lines("    key: alt+plus")
        diags = _warns(check_key_combos(lines, "f.yaml"))
        assert len(diags) == 1

    def test_unknown_modifier_blah(self):
        lines = _lines("    key: blah+w")
        diags = _warns(check_key_combos(lines, "f.yaml"))
        assert len(diags) == 1

    def test_then_key_pre_valid(self):
        lines = _lines("    then_key_pre: shift")
        assert check_key_combos(lines, "f.yaml") == []


# ---------------------------------------------------------------------------
# Check 2 — hardcoded xdotool coords
# ---------------------------------------------------------------------------

class TestXdotoolCoordsCheck:
    def test_warns_on_mousemove(self):
        lines = _lines("xdotool mousemove 640 400 click 1")
        diags = _warns(check_xdotool_coords(lines, "f.yaml"))
        assert len(diags) == 1
        assert "coord-drift" in diags[0].rule

    def test_clean_xdotool_key(self):
        lines = _lines("xdotool key ctrl+f")
        assert check_xdotool_coords(lines, "f.yaml") == []


# ---------------------------------------------------------------------------
# Check 3 — transient localize targets
# ---------------------------------------------------------------------------

class TestTransientLocalizeCheck:
    def test_warns_on_kebab(self):
        lines = _lines('  localize: "three-dot kebab button"')
        diags = _warns(check_transient_localize(lines, "f.yaml"))
        assert len(diags) == 1
        assert "transient-localize" in diags[0].rule

    def test_warns_on_dropdown(self):
        lines = _lines("  localize: Open dropdown menu")
        diags = _warns(check_transient_localize(lines, "f.yaml"))
        assert len(diags) == 1

    def test_clean_stable_target(self):
        lines = _lines("  localize: General tab in Settings")
        assert check_transient_localize(lines, "f.yaml") == []


# ---------------------------------------------------------------------------
# Check 4 — Settings toggle without verify
# ---------------------------------------------------------------------------

class TestSettingsToggleCheck:
    def test_warns_when_no_verify_before_kill(self):
        text = """\
  localize: Settings panel
  click: true
  localize: General tab
  click: true
  localize: Telephony toggle
  click: true
  shell: |
    pkill -KILL -f /opt/Rocket.Chat
"""
        diags = _warns(check_settings_toggle_pattern(_lines(text), "f.yaml"))
        assert len(diags) == 1
        assert "settings-toggle-no-verify" in diags[0].rule

    def test_clean_when_verify_present(self):
        text = """\
  localize: Settings panel
  click: true
  localize: General tab
  click: true
  localize: Telephony toggle
  click: true
  verify: Telephony toggle is now enabled
  shell: |
    pkill -KILL -f /opt/Rocket.Chat
"""
        diags = _warns(check_settings_toggle_pattern(_lines(text), "f.yaml"))
        assert diags == []


# ---------------------------------------------------------------------------
# Check 5 — heredoc in YAML
# ---------------------------------------------------------------------------

class TestHeredocCheck:
    def test_warns_on_heredoc(self):
        lines = _lines("    cat > /tmp/config.json <<'EOF'")
        diags = _warns(check_heredoc_in_yaml(lines, "f.yaml"))
        assert len(diags) == 1
        assert "heredoc-in-yaml" in diags[0].rule

    def test_clean_printf_pattern(self):
        lines = _lines("    printf '%s\\n' '{\"key\":\"val\"}' > /tmp/config.json")
        assert check_heredoc_in_yaml(lines, "f.yaml") == []


# ---------------------------------------------------------------------------
# Check 6 — tel: dispatch without XAUTHORITY
# ---------------------------------------------------------------------------

class TestTelDispatchXauthCheck:
    def test_warns_without_xauthority(self):
        """nohup ... "tel:... without XAUTHORITY= nearby should warn."""
        lines = ['nohup /opt/opener "tel:+123"']
        diags = _warns(check_tel_dispatch_xauthority(lines, "f.yaml"))
        assert len(diags) == 1
        assert "tel-dispatch-xauth" in diags[0].rule

    def test_clean_with_xauthority_nearby(self):
        lines = [
            "export XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.abc",
            "export DISPLAY=:0",
            'nohup /opt/opener "tel:+123"',
        ]
        diags = _warns(check_tel_dispatch_xauthority(lines, "f.yaml"))
        assert diags == []


# ---------------------------------------------------------------------------
# Check 7 — config.json write without migrations version
# ---------------------------------------------------------------------------

class TestConfigJsonMigrationsCheck:
    def test_warns_without_migrations(self):
        lines = _lines("printf '%s\\n' '{\"currentView\":null}' > ~/.config/Rocket.Chat/config.json")
        diags = _warns(check_config_json_migrations(lines, "f.yaml"))
        assert len(diags) == 1
        assert "config-json-no-migrations" in diags[0].rule

    def test_clean_with_migrations_nearby(self):
        lines = [
            "printf '%s\\n' '{",
            '  "__internal__": {"migrations": {"version": "5.0.0"}},',
            '  "currentView": null',
            "}' > ~/.config/Rocket.Chat/config.json",
        ]
        diags = _warns(check_config_json_migrations(lines, "f.yaml"))
        assert diags == []


# ---------------------------------------------------------------------------
# Clean-pass integration test via run_lint
# ---------------------------------------------------------------------------

class TestRunLintCleanPass:
    def test_clean_scenario_returns_0(self, tmp_path):
        scenario = tmp_path / "clean.yaml"
        scenario.write_text("""\
name: clean-test
steps:
  - shell: |
      echo hello
  - localize: General tab in Settings panel
    click: left
    then_key: ctrl+f
  - verify: search box is visible
""")
        args = MagicMock()
        args.scenario = str(scenario)
        result = run_lint(args)
        assert result == 0

    def test_missing_file_returns_2(self, tmp_path):
        args = MagicMock()
        args.scenario = str(tmp_path / "nonexistent.yaml")
        result = run_lint(args)
        assert result == 2


# ---------------------------------------------------------------------------
# Check 8/9 — schema validation via the real runner's loader path, and the
# no-recognized-step-type shortcut (F1a bug fix: lint must not bless a
# scenario the real functional runner would reject at load time).
# ---------------------------------------------------------------------------

class TestSchemaValidationCheck:
    def test_step_with_no_type_discriminator_fails_lint(self, tmp_path):
        """A step with only label/verify_not/verify_timeout/retries (no
        launch/localize/verify/key/etc.) matches zero AnyStep union members —
        the real runner rejects this at exit code 4. Lint must not pass it."""
        scenario = tmp_path / "broken.yaml"
        scenario.write_text("""\
name: broken-test
steps:
  - label: some step
    verify_not: something bad
    verify_timeout: 15
    retries: 2
""")
        args = MagicMock()
        args.scenario = str(scenario)
        result = run_lint(args)
        assert result == 1

    def test_step_type_discriminator_direct(self):
        raw_data = {
            "steps": [
                {"label": "x", "verify_not": "y", "verify_timeout": 15, "retries": 2},
            ]
        }
        diags = _warns(check_step_type_discriminator(raw_data, "f.yaml"))
        assert len(diags) == 1
        assert "step-no-type-match" in diags[0].rule
        assert "steps[0]" in diags[0].message

    def test_step_type_discriminator_allows_routine(self):
        """Pre-expansion 'routine:' steps are resolved before schema
        validation runs — they must not be flagged as no-type-match."""
        raw_data = {"steps": [{"routine": "cleanup-rocketchat"}]}
        assert check_step_type_discriminator(raw_data, "f.yaml") == []

    @pytest.mark.parametrize("step", [
        {"click_at": {"x": 1, "y": 2, "native": True}},
        {"poll_shell": "true"},
        {"verify_recorded": {"prompt": "p", "between": [1, 2]}},
    ])
    def test_step_type_discriminator_allows_dsl_steps(self, step):
        """click_at / poll_shell / verify_recorded are real step types."""
        assert check_step_type_discriminator({"steps": [step]}, "f.yaml") == []

    def test_valid_scenario_passes_schema_validation(self, tmp_path):
        scenario = tmp_path / "valid.yaml"
        scenario.write_text("""\
name: valid-test
steps:
  - shell: |
      echo hello
  - verify: search box is visible
""")
        diags = check_schema_validation(scenario, str(scenario))
        assert diags == []

    def test_invalid_scenario_fails_schema_validation(self, tmp_path):
        scenario = tmp_path / "invalid.yaml"
        scenario.write_text("""\
name: invalid-test
steps:
  - label: some step
    verify_not: something bad
    verify_timeout: 15
    retries: 2
""")
        diags = _warns(check_schema_validation(scenario, str(scenario)))
        assert len(diags) == 1
        assert "schema-validation" in diags[0].rule
