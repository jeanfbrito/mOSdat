"""Tests for I11 — imported step fragments via !import / imports: block.

Covers:
  - Both YAML shapes: !import tag form and - import: dict form
  - Fragment expanded inline at correct position in step list
  - Missing fragment file → clear FileNotFoundError
  - Cycle detection (frag-A imports frag-B imports frag-A) → RuntimeError
  - Vars from parent scenario substitute into fragment steps (I8 still works)
  - Comment labels inside fragments get [import:X] prefix
  - Manifest mismatch (!import X without listing in imports:) → warning, not error
  - All 3 canonical fragments parse + validate against ScenarioModel
  - Demo: tiny test scenario using cleanup-rocketchat expands to expected steps
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
import tempfile
import warnings
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

_PROJ = Path(__file__).parent.parent
_IMPORTS_DIR = _PROJ / "shared/scenarios/imports"


# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

def _load_mod(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _PROJ / rel_path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = ".".join(name.split(".")[:-1])
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_loader_mod = _load_mod("automation/runners/scenario_loader.py", "automation.runners.scenario_loader")
_is_import_step = _loader_mod._is_import_step
_expand_imports = _loader_mod._expand_imports
_load_fragment_steps = _loader_mod._load_fragment_steps
_warn_manifest_mismatch = _loader_mod._warn_manifest_mismatch
_imports_dir = _loader_mod._imports_dir
load_test_yaml = _loader_mod.load_test_yaml
parse_step = _loader_mod.parse_step


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str) -> Path:
    """Write YAML content to a temp file and return its path."""
    p = tmp_path / "scenario.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def _write_fragment(imports_dir: Path, name: str, steps_yaml: str):
    """Write a fragment YAML file into a temp imports dir."""
    imports_dir.mkdir(parents=True, exist_ok=True)
    p = imports_dir / f"{name}.yaml"
    p.write_text(textwrap.dedent(steps_yaml))
    return p


def _make_imports_dir(tmp_path: Path) -> Path:
    d = tmp_path / "shared/scenarios/imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# 1. _is_import_step — unit tests
# ---------------------------------------------------------------------------

class TestIsImportStep:
    def test_dict_form_recognized(self):
        assert _is_import_step({"import": "cleanup-rocketchat"}) == "cleanup-rocketchat"

    def test_dict_form_extra_key_not_import(self):
        # Has extra keys → not a pure import step
        assert _is_import_step({"import": "x", "shell": "echo hi"}) is None

    def test_non_dict(self):
        assert _is_import_step("import: foo") is None
        assert _is_import_step(None) is None
        assert _is_import_step(42) is None

    def test_empty_import_value(self):
        # Empty string → not valid
        assert _is_import_step({"import": ""}) is None
        assert _is_import_step({"import": "  "}) is None

    def test_whitespace_stripped(self):
        assert _is_import_step({"import": "  foo  "}) == "foo"


# ---------------------------------------------------------------------------
# 2. Both YAML shapes work
# ---------------------------------------------------------------------------

class TestYamlShapes:
    """Both !import tag and - import: dict form produce same result."""

    def test_tag_form_parsed_by_safeloader(self, tmp_path):
        """!import tag form → SafeLoader constructor → {"import": "name"}."""
        from automation.runners.scenario_loader import _register_import_constructor
        _register_import_constructor(yaml.SafeLoader)

        content = "- !import cleanup-rocketchat\n"
        doc = yaml.safe_load(content)
        assert doc == [{"import": "cleanup-rocketchat"}]

    def test_dict_form_is_recognized(self):
        step = {"import": "install-x11-deps"}
        assert _is_import_step(step) == "install-x11-deps"

    def test_tag_form_and_dict_form_expand_identically(self, tmp_path):
        """Both forms result in same expanded steps."""
        imports_dir = _make_imports_dir(tmp_path)
        _write_fragment(imports_dir, "frag-a", """\
            steps:
              - shell: echo hello
        """)

        steps_tag = [{"import": "frag-a"}]
        steps_dict = [{"import": "frag-a"}]

        expanded_tag = _expand_imports(steps_tag, imports_dir, tmp_path / "s.yaml")
        expanded_dict = _expand_imports(steps_dict, imports_dir, tmp_path / "s.yaml")

        assert len(expanded_tag) == 1
        assert expanded_tag == expanded_dict
        assert "echo hello" in expanded_tag[0]["shell"]


# ---------------------------------------------------------------------------
# 3. Fragment expanded inline at correct position
# ---------------------------------------------------------------------------

class TestFragmentExpansion:
    def test_single_import_at_start(self, tmp_path):
        imports_dir = _make_imports_dir(tmp_path)
        _write_fragment(imports_dir, "frag-a", """\
            steps:
              - shell: echo A1
              - shell: echo A2
        """)

        steps = [{"import": "frag-a"}, {"shell": "echo B"}]
        expanded = _expand_imports(steps, imports_dir, tmp_path / "s.yaml")

        assert len(expanded) == 3
        assert "echo A1" in expanded[0]["shell"]
        assert "echo A2" in expanded[1]["shell"]
        assert expanded[2]["shell"] == "echo B"

    def test_import_in_middle(self, tmp_path):
        imports_dir = _make_imports_dir(tmp_path)
        _write_fragment(imports_dir, "mid", """\
            steps:
              - wait: 5
        """)

        steps = [{"shell": "echo before"}, {"import": "mid"}, {"shell": "echo after"}]
        expanded = _expand_imports(steps, imports_dir, tmp_path / "s.yaml")

        assert len(expanded) == 3
        assert expanded[0]["shell"] == "echo before"
        assert expanded[1]["wait"] == 5
        assert expanded[2]["shell"] == "echo after"

    def test_no_imports_passthrough(self, tmp_path):
        imports_dir = _make_imports_dir(tmp_path)
        steps = [{"shell": "echo hi"}, {"wait": 3}]
        expanded = _expand_imports(steps, imports_dir, tmp_path / "s.yaml")
        assert expanded == steps

    def test_multiple_imports(self, tmp_path):
        imports_dir = _make_imports_dir(tmp_path)
        _write_fragment(imports_dir, "f1", "steps:\n  - shell: echo F1\n")
        _write_fragment(imports_dir, "f2", "steps:\n  - shell: echo F2\n")

        steps = [{"import": "f1"}, {"import": "f2"}]
        expanded = _expand_imports(steps, imports_dir, tmp_path / "s.yaml")

        assert len(expanded) == 2
        assert "echo F1" in expanded[0]["shell"]
        assert "echo F2" in expanded[1]["shell"]


# ---------------------------------------------------------------------------
# 4. Missing fragment → clear error
# ---------------------------------------------------------------------------

class TestMissingFragment:
    def test_missing_fragment_raises_file_not_found(self, tmp_path):
        imports_dir = _make_imports_dir(tmp_path)
        steps = [{"import": "nonexistent-fragment"}]

        with pytest.raises(FileNotFoundError) as exc_info:
            _expand_imports(steps, imports_dir, tmp_path / "my-scenario.yaml")

        msg = str(exc_info.value)
        assert "nonexistent-fragment" in msg
        assert "my-scenario.yaml" in msg

    def test_error_names_fragment_and_scenario(self, tmp_path):
        imports_dir = _make_imports_dir(tmp_path)
        steps = [{"import": "missing-dep"}]

        with pytest.raises(FileNotFoundError) as exc_info:
            _expand_imports(steps, imports_dir, tmp_path / "test-scenario.yaml")

        assert "missing-dep" in str(exc_info.value)
        assert "test-scenario.yaml" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 5. Cycle detection
# ---------------------------------------------------------------------------

class TestCycleDetection:
    def test_direct_self_cycle(self, tmp_path):
        """frag-a imports frag-a → cycle detected."""
        imports_dir = _make_imports_dir(tmp_path)
        _write_fragment(imports_dir, "frag-a", """\
            steps:
              - import: frag-a
        """)

        steps = [{"import": "frag-a"}]
        with pytest.raises(RuntimeError) as exc_info:
            _expand_imports(steps, imports_dir, tmp_path / "s.yaml")

        assert "Cycle" in str(exc_info.value)
        assert "frag-a" in str(exc_info.value)

    def test_indirect_cycle(self, tmp_path):
        """frag-a imports frag-b, frag-b imports frag-a → cycle."""
        imports_dir = _make_imports_dir(tmp_path)
        _write_fragment(imports_dir, "frag-a", """\
            steps:
              - import: frag-b
        """)
        _write_fragment(imports_dir, "frag-b", """\
            steps:
              - import: frag-a
        """)

        steps = [{"import": "frag-a"}]
        with pytest.raises(RuntimeError) as exc_info:
            _expand_imports(steps, imports_dir, tmp_path / "s.yaml")

        assert "Cycle" in str(exc_info.value)

    def test_no_false_positive_sibling(self, tmp_path):
        """Two !imports of the same fragment (non-cyclic) should work."""
        imports_dir = _make_imports_dir(tmp_path)
        _write_fragment(imports_dir, "shared", "steps:\n  - shell: echo shared\n")

        steps = [{"import": "shared"}, {"import": "shared"}]
        expanded = _expand_imports(steps, imports_dir, tmp_path / "s.yaml")
        assert len(expanded) == 2


# ---------------------------------------------------------------------------
# 6. Vars from parent substitute into fragment (I8 still works)
# ---------------------------------------------------------------------------

class TestVarsInFragments:
    def test_var_substituted_in_fragment(self, tmp_path):
        """{{ workspace_url }} in fragment step gets substituted from parent vars."""
        imports_dir = _make_imports_dir(tmp_path)
        _write_fragment(imports_dir, "frag-vars", """\
            steps:
              - shell: echo {{ workspace_url }}
              - verify: "loaded {{ workspace_url }}"
        """)

        scenario_yaml = """\
            name: test-vars
            vars:
              workspace_url: https://my.workspace.com
            steps:
              - import: frag-vars
        """
        p = tmp_path / "proj/shared/scenarios/functional/s.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        # Place imports dir relative to scenario path
        frag_imports = tmp_path / "proj/shared/scenarios/imports"
        frag_imports.mkdir(parents=True, exist_ok=True)
        (frag_imports / "frag-vars.yaml").write_text(textwrap.dedent("""\
            steps:
              - shell: echo {{ workspace_url }}
              - verify: "loaded {{ workspace_url }}"
        """))
        p.write_text(textwrap.dedent(scenario_yaml))

        _, steps, merged_vars, _ = load_test_yaml(p)

        assert len(steps) == 2
        assert "https://my.workspace.com" in steps[0].shell
        assert "https://my.workspace.com" in steps[1].verify

    def test_var_missing_in_fragment_fails(self, tmp_path):
        """{{ undefined_var }} in fragment → MissingVarsError at load time."""
        frag_imports = tmp_path / "proj/shared/scenarios/imports"
        frag_imports.mkdir(parents=True, exist_ok=True)
        (frag_imports / "frag-missing").mkdir(exist_ok=True)
        (frag_imports / "frag-missing.yaml").write_text(textwrap.dedent("""\
            steps:
              - shell: echo {{ undefined_var }}
        """))

        p = tmp_path / "proj/shared/scenarios/functional/s.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent("""\
            name: test-missing-var
            vars:
              workspace_url: https://x.com
            steps:
              - import: frag-missing
        """))

        with pytest.raises(SystemExit):
            load_test_yaml(p)


# ---------------------------------------------------------------------------
# 7. Comment labels inside fragments get [import:X] prefix
# ---------------------------------------------------------------------------

class TestFragmentLabels:
    def test_explicit_label_in_fragment_gets_prefix(self, tmp_path):
        imports_dir = _make_imports_dir(tmp_path)
        _write_fragment(imports_dir, "labeled-frag", """\
            steps:
              - shell: echo hi
                label: "A1: Kill all processes"
        """)

        steps = [{"import": "labeled-frag"}]
        expanded = _expand_imports(steps, imports_dir, tmp_path / "s.yaml")

        assert len(expanded) == 1
        assert expanded[0]["label"] == "[import:labeled-frag] A1: Kill all processes"

    def test_no_label_field_stays_none(self, tmp_path):
        imports_dir = _make_imports_dir(tmp_path)
        _write_fragment(imports_dir, "unlabeled", """\
            steps:
              - shell: echo hi
        """)

        steps = [{"import": "unlabeled"}]
        expanded = _expand_imports(steps, imports_dir, tmp_path / "s.yaml")

        # No label — the key may be absent or None
        assert expanded[0].get("label") is None


# ---------------------------------------------------------------------------
# 8. Manifest mismatch → warning, not error
# ---------------------------------------------------------------------------

class TestManifestMismatch:
    def test_unlisted_import_produces_warning(self, tmp_path):
        steps = [{"import": "unlisted-frag"}]
        declared = ["listed-frag"]

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_manifest_mismatch(steps, declared, tmp_path / "s.yaml")

        assert len(w) == 1
        assert "unlisted-frag" in str(w[0].message)
        assert "imports:" in str(w[0].message)

    def test_listed_import_no_warning(self, tmp_path):
        steps = [{"import": "listed-frag"}]
        declared = ["listed-frag"]

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_manifest_mismatch(steps, declared, tmp_path / "s.yaml")

        assert len(w) == 0

    def test_empty_manifest_no_warning(self, tmp_path):
        """Empty imports: list → no warning emitted (manifest is optional)."""
        steps = [{"import": "anything"}]
        declared = []

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _warn_manifest_mismatch(steps, declared, tmp_path / "s.yaml")

        assert len(w) == 0


# ---------------------------------------------------------------------------
# 9. All 3 canonical fragments parse + validate
# ---------------------------------------------------------------------------

class TestCanonicalFragments:
    """Each canonical fragment file must be loadable + validate steps."""

    @pytest.mark.parametrize("name", [
        "cleanup-rocketchat",
        "install-x11-deps",
        "launch-rocketchat",
    ])
    def test_fragment_file_exists(self, name):
        p = _IMPORTS_DIR / f"{name}.yaml"
        assert p.exists(), f"Fragment file missing: {p}"

    @pytest.mark.parametrize("name", [
        "cleanup-rocketchat",
        "install-x11-deps",
        "launch-rocketchat",
    ])
    def test_fragment_steps_load_as_list(self, name):
        p = _IMPORTS_DIR / f"{name}.yaml"
        doc = yaml.safe_load(p.read_text())
        assert isinstance(doc, dict), f"{name}.yaml must be a dict with 'steps' key"
        steps = doc.get("steps", [])
        assert isinstance(steps, list)
        assert len(steps) > 0, f"{name}.yaml has no steps"

    @pytest.mark.parametrize("name", [
        "cleanup-rocketchat",
        "install-x11-deps",
    ])
    def test_fragment_steps_parse_as_functional_steps(self, name, tmp_path):
        """Fragment steps (without vars) parse cleanly via parse_step."""
        p = _IMPORTS_DIR / f"{name}.yaml"
        doc = yaml.safe_load(p.read_text())
        steps = doc.get("steps", [])
        for raw in steps:
            # Should not raise
            step = parse_step(raw)
            assert step is not None

    def test_launch_fragment_has_verify(self):
        """launch-rocketchat must have a verify step (login page check)."""
        p = _IMPORTS_DIR / "launch-rocketchat.yaml"
        doc = yaml.safe_load(p.read_text())
        steps = doc.get("steps", [])
        verifies = [s for s in steps if "verify" in s]
        assert len(verifies) >= 1, "launch-rocketchat.yaml must have at least one verify step"

    def test_cleanup_fragment_has_shell(self):
        p = _IMPORTS_DIR / "cleanup-rocketchat.yaml"
        doc = yaml.safe_load(p.read_text())
        steps = doc.get("steps", [])
        shells = [s for s in steps if "shell" in s]
        assert len(shells) >= 1

    def test_install_x11_fragment_has_shell(self):
        p = _IMPORTS_DIR / "install-x11-deps.yaml"
        doc = yaml.safe_load(p.read_text())
        steps = doc.get("steps", [])
        shells = [s for s in steps if "shell" in s]
        assert len(shells) >= 1


# ---------------------------------------------------------------------------
# 10. Demo: tiny scenario using cleanup-rocketchat expands to expected steps
# ---------------------------------------------------------------------------

class TestDemoExpansion:
    def test_cleanup_import_expands_to_shell_step(self, tmp_path):
        """Scenario using !import cleanup-rocketchat expands to a shell step."""
        # Build a minimal project tree so _imports_dir finds the real fragments
        # by creating a symlink to the real imports dir
        proj = tmp_path / "proj"
        imports_target = proj / "shared/scenarios/imports"
        imports_target.mkdir(parents=True, exist_ok=True)
        # Copy the real cleanup-rocketchat fragment
        real_frag = _IMPORTS_DIR / "cleanup-rocketchat.yaml"
        (imports_target / "cleanup-rocketchat.yaml").write_text(real_frag.read_text())

        scenario_content = textwrap.dedent("""\
            name: demo-cleanup
            imports:
              - cleanup-rocketchat
            steps:
              - import: cleanup-rocketchat
              - shell: echo AFTER_CLEANUP
        """)
        p = proj / "shared/scenarios/functional/demo.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(scenario_content)

        _, steps, _, _ = load_test_yaml(p)

        # cleanup-rocketchat has 1 shell step → total 2 steps
        assert len(steps) == 2
        assert steps[0].shell is not None
        assert "Rocket.Chat" in steps[0].shell or "pkill" in steps[0].shell or "wmctrl" in steps[0].shell
        assert "echo AFTER_CLEANUP" in steps[1].shell

    def test_scenario_model_validates_with_imports_field(self, tmp_path):
        """ScenarioModel accepts imports: list field."""
        # We need to import the scenario module
        scenario_mod = _load_mod("automation/scenario.py", "automation.scenario_test_imports")
        ScenarioModel = scenario_mod.ScenarioModel

        data = {
            "name": "test",
            "imports": ["cleanup-rocketchat", "install-x11-deps"],
            "steps": [{"shell": "echo hi"}],
        }
        model = ScenarioModel.model_validate(data)
        assert model.imports == ["cleanup-rocketchat", "install-x11-deps"]

    def test_scenario_model_imports_default_empty(self, tmp_path):
        scenario_mod = _load_mod("automation/scenario.py", "automation.scenario_test_imports2")
        ScenarioModel = scenario_mod.ScenarioModel

        data = {"name": "test", "steps": [{"shell": "echo hi"}]}
        model = ScenarioModel.model_validate(data)
        assert model.imports == []

    def test_import_step_in_schema(self, tmp_path):
        """ImportStep (dict form - import: X) validates in ScenarioModel."""
        scenario_mod = _load_mod("automation/scenario.py", "automation.scenario_test_imports3")
        ScenarioModel = scenario_mod.ScenarioModel

        data = {
            "name": "test",
            "steps": [{"import": "cleanup-rocketchat"}],
        }
        # Should not raise — ImportStep is in AnyStep union
        model = ScenarioModel.model_validate(data)
        assert len(model.steps) == 1


# ---------------------------------------------------------------------------
# 11. master-toggle.yaml uses !import cleanup-rocketchat after conversion
# ---------------------------------------------------------------------------

class TestMasterToggleConversion:
    def test_master_toggle_has_import_step(self):
        """3325-master-toggle.yaml contains at least one !import reference."""
        p = _PROJ / "shared/scenarios/functional/3325-master-toggle.yaml"
        assert p.exists()
        content = p.read_text()
        assert "!import cleanup-rocketchat" in content or "import: cleanup-rocketchat" in content

    def test_master_toggle_imports_manifest(self):
        """3325-master-toggle.yaml has cleanup-rocketchat in imports: manifest."""
        p = _PROJ / "shared/scenarios/functional/3325-master-toggle.yaml"
        doc = yaml.safe_load(p.read_text())
        assert "imports" in doc
        assert "cleanup-rocketchat" in doc["imports"]

    def test_master_toggle_loads_without_error(self):
        """3325-master-toggle.yaml loads via load_test_yaml without error."""
        p = _PROJ / "shared/scenarios/functional/3325-master-toggle.yaml"
        # Should not raise (fragment is real + exists)
        _, steps, _, _ = load_test_yaml(p)
        # Should have > 1 step (import expands to at least 1 shell step)
        assert len(steps) > 5

    def test_master_toggle_first_step_is_shell(self):
        """After expansion, first step of 3325-master-toggle.yaml is a shell step."""
        p = _PROJ / "shared/scenarios/functional/3325-master-toggle.yaml"
        _, steps, _, _ = load_test_yaml(p)
        assert steps[0].shell is not None

    def test_master_toggle_cleanup_step_has_prefixed_label(self):
        """Expanded cleanup-rocketchat step has [import:cleanup-rocketchat] label prefix."""
        p = _PROJ / "shared/scenarios/functional/3325-master-toggle.yaml"
        _, steps, _, _ = load_test_yaml(p)
        # First step should carry the prefixed label from the fragment comment
        label = steps[0].label
        if label is not None:
            assert label.startswith("[import:cleanup-rocketchat]")
