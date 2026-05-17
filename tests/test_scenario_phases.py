"""Tests for I9 — named phases + --from-phase / --until-phase.

Covers:
  - Schema: valid phases list accepted
  - Schema: missing required fields (id, name, from_step)
  - Schema: duplicate phase id rejected
  - Schema: non-monotonic from_step rejected
  - Schema: from_step < 1 rejected
  - _resolve_phases: no phase flags → passthrough
  - _resolve_phases: --from-phase B resolves to correct step number
  - _resolve_phases: --from-phase X errors when X not declared
  - _resolve_phases: --until-phase resolves to last step of phase
  - _resolve_phases: --from-phase + --until-phase combined
  - _resolve_phases: --from-phase overrides --from-step with warning
  - _resolve_phases: --until-phase overrides --until-step with warning
  - _resolve_phases: no phases block + phase flag → error
  - Logging line includes phase resolution
  - Demo scenario (3325-master-toggle.yaml) validates with phases block
"""

from __future__ import annotations

import importlib.util
import sys
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Module loader helpers
# ---------------------------------------------------------------------------

def _load_mod(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _PROJ / rel_path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = ".".join(name.split(".")[:-1])
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_scenario_mod = _load_mod("automation/scenario.py", "automation.scenario")
ScenarioModel = _scenario_mod.ScenarioModel
PhaseDef = _scenario_mod.PhaseDef

from pydantic import ValidationError  # noqa: E402


# ---------------------------------------------------------------------------
# Load _resolve_phases from main.py without executing the whole module
# ---------------------------------------------------------------------------

def _get_resolve_phases():
    """Extract _resolve_phases from main.py via AST — no import chain triggered.

    Uses ast.parse + ast.unparse to pull just the function body, avoiding any
    sys.modules pollution that would break sibling test modules.
    """
    import ast

    src = (_PROJ / "automation" / "main.py").read_text()
    tree = ast.parse(src)

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_resolve_phases":
            func_src = ast.unparse(node)
            ns: dict = {}
            exec(func_src, ns)  # noqa: S102
            return ns["_resolve_phases"]

    raise RuntimeError("_resolve_phases not found in automation/main.py")


_resolve_phases = _get_resolve_phases()


# ---------------------------------------------------------------------------
# Helper: build a minimal ScenarioModel data dict
# ---------------------------------------------------------------------------

def _minimal(extra: dict | None = None) -> dict:
    base: dict[str, Any] = {"steps": [{"shell": "echo ok"}]}
    if extra:
        base.update(extra)
    return base


def _make_phases(*specs) -> list[dict]:
    """Build a phases list from (id, name, from_step) tuples."""
    return [{"id": s[0], "name": s[1], "from_step": s[2]} for s in specs]


def _make_phase_defs(*specs) -> list:
    return [PhaseDef(id=s[0], name=s[1], from_step=s[2]) for s in specs]


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestPhaseDefSchema:
    def test_valid_single_phase(self):
        m = ScenarioModel.model_validate(_minimal({
            "phases": _make_phases(("A", "baseline", 1)),
        }))
        assert len(m.phases) == 1
        assert m.phases[0].id == "A"
        assert m.phases[0].from_step == 1

    def test_valid_two_phases(self):
        m = ScenarioModel.model_validate(_minimal({
            "phases": _make_phases(("A", "phase a", 1), ("B", "phase b", 2)),
        }))
        assert m.phases[0].id == "A"
        assert m.phases[1].id == "B"

    def test_no_phases_block(self):
        """Scenarios without phases: work unchanged."""
        m = ScenarioModel.model_validate(_minimal())
        assert m.phases is None

    def test_duplicate_phase_id_rejected(self):
        with pytest.raises(ValidationError, match="duplicate id"):
            ScenarioModel.model_validate(_minimal({
                "phases": _make_phases(("A", "one", 1), ("A", "two", 5)),
            }))

    def test_non_monotonic_from_step_rejected(self):
        with pytest.raises(ValidationError, match="strictly increasing"):
            ScenarioModel.model_validate(_minimal({
                "phases": _make_phases(("A", "first", 5), ("B", "second", 3)),
            }))

    def test_equal_from_step_rejected(self):
        with pytest.raises(ValidationError, match="strictly increasing"):
            ScenarioModel.model_validate(_minimal({
                "phases": _make_phases(("A", "first", 1), ("B", "second", 1)),
            }))

    def test_from_step_zero_rejected(self):
        with pytest.raises(ValidationError, match="from_step must be >= 1"):
            ScenarioModel.model_validate(_minimal({
                "phases": _make_phases(("A", "bad", 0)),
            }))

    def test_missing_id_rejected(self):
        with pytest.raises(ValidationError):
            ScenarioModel.model_validate(_minimal({
                "phases": [{"name": "no id", "from_step": 1}],
            }))

    def test_missing_name_rejected(self):
        with pytest.raises(ValidationError):
            ScenarioModel.model_validate(_minimal({
                "phases": [{"id": "A", "from_step": 1}],
            }))

    def test_missing_from_step_rejected(self):
        with pytest.raises(ValidationError):
            ScenarioModel.model_validate(_minimal({
                "phases": [{"id": "A", "name": "no from_step"}],
            }))

    def test_extra_field_in_phase_rejected(self):
        with pytest.raises(ValidationError):
            ScenarioModel.model_validate(_minimal({
                "phases": [{"id": "A", "name": "x", "from_step": 1, "typo": "y"}],
            }))


# ---------------------------------------------------------------------------
# _resolve_phases tests
# ---------------------------------------------------------------------------

class TestResolvePhases:

    def _phases(self, *specs):
        return _make_phase_defs(*specs)

    def test_no_flags_passthrough(self):
        from_step, until_step, log = _resolve_phases(None, 10, None, None, 1, None)
        assert from_step == 1
        assert until_step is None
        assert log is None

    def test_from_phase_resolves_to_correct_step(self):
        phases = self._phases(("A", "baseline", 1), ("B", "modal", 7))
        from_step, until_step, log = _resolve_phases(phases, 14, "B", None, 1, None)
        assert from_step == 7
        assert until_step is None

    def test_until_phase_resolves_to_last_step_of_phase(self):
        phases = self._phases(("A", "baseline", 1), ("B", "modal", 7))
        from_step, until_step, log = _resolve_phases(phases, 14, None, "A", 1, None)
        # Phase A spans steps 1..6 (phase B starts at 7)
        assert until_step == 6

    def test_until_phase_last_phase_resolves_to_total(self):
        phases = self._phases(("A", "baseline", 1), ("B", "modal", 7))
        from_step, until_step, log = _resolve_phases(phases, 14, None, "B", 1, None)
        assert until_step == 14

    def test_from_and_until_phase_combined(self):
        phases = self._phases(("A", "a", 1), ("B", "b", 5), ("C", "c", 10))
        from_step, until_step, log = _resolve_phases(phases, 15, "B", "B", 1, None)
        assert from_step == 5
        assert until_step == 9

    def test_unknown_from_phase_raises_system_exit(self):
        phases = self._phases(("A", "a", 1), ("B", "b", 7))
        with pytest.raises(SystemExit):
            _resolve_phases(phases, 14, "X", None, 1, None)

    def test_unknown_until_phase_raises_system_exit(self):
        phases = self._phases(("A", "a", 1), ("B", "b", 7))
        with pytest.raises(SystemExit):
            _resolve_phases(phases, 14, None, "Z", 1, None)

    def test_no_phases_block_with_flag_raises_system_exit(self):
        with pytest.raises(SystemExit):
            _resolve_phases(None, 14, "B", None, 1, None)

    def test_from_phase_overrides_from_step_warns(self, capsys):
        phases = self._phases(("A", "a", 1), ("B", "b", 7))
        from_step, _, _ = _resolve_phases(phases, 14, "B", None, 3, None)
        assert from_step == 7
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "--from-step" in captured.out

    def test_until_phase_overrides_until_step_warns(self, capsys):
        phases = self._phases(("A", "a", 1), ("B", "b", 7))
        _, until_step, _ = _resolve_phases(phases, 14, None, "A", 1, 10)
        assert until_step == 6
        captured = capsys.readouterr()
        assert "WARNING" in captured.out
        assert "--until-step" in captured.out

    def test_phase_log_line_format(self):
        phases = self._phases(("A", "baseline", 1), ("B", "modal", 7))
        _, _, log = _resolve_phases(phases, 14, "A", "B", 1, None)
        assert log is not None
        assert "[phases] running" in log
        assert "A (baseline)" in log
        assert "B (modal)" in log
        assert "steps 1..6" in log
        assert "steps 7..14" in log

    def test_log_line_only_overlapping_phases(self):
        """When --from-phase B, only phase B should appear in log."""
        phases = self._phases(("A", "baseline", 1), ("B", "modal", 7))
        _, _, log = _resolve_phases(phases, 14, "B", None, 1, None)
        assert "B (modal)" in log
        assert "A (baseline)" not in log


# ---------------------------------------------------------------------------
# Demo scenario validation
# ---------------------------------------------------------------------------

class TestDemoScenario:

    def test_master_toggle_phases_validate(self):
        path = _PROJ / "shared" / "scenarios" / "functional" / "3325-master-toggle.yaml"
        data = yaml.safe_load(path.read_text())
        m = ScenarioModel.model_validate(data)
        assert m.phases is not None
        assert len(m.phases) == 2
        ids = [p.id for p in m.phases]
        assert ids == ["A", "B"]
        # Phase A starts at step 1, Phase B at step 10
        assert m.phases[0].from_step == 1
        assert m.phases[1].from_step == 10
        assert len(m.steps) == 20
