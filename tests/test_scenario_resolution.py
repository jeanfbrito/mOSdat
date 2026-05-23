"""Tests for the per-OS scenario directory resolver.

Covers ``automation.runners.scenario_loader.resolve_test_path`` — the helper
that maps ``--test NAME`` (or ``--test linux/NAME``) to a concrete YAML path,
trying the platform subdir first then falling back to the legacy root.

See also: docs reorg moving most scenarios under ``shared/scenarios/functional/linux/``
to make room for ``windows10/`` and ``windows11/`` variants.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from automation.runners.scenario_loader import (
    ScenarioNotFoundError,
    resolve_test_path,
)


@pytest.fixture
def scenarios_root(tmp_path: Path) -> Path:
    """Build a synthetic functional/ tree:

    functional/
        linux/foo.yaml
        linux/tel-qa-001.yaml
        legacy.yaml             # root, no subdir copy
        windows10/.gitkeep
    """
    root = tmp_path / "functional"
    (root / "linux").mkdir(parents=True)
    (root / "windows10").mkdir()
    (root / "linux" / "foo.yaml").write_text("name: foo\nsteps: []\n")
    (root / "linux" / "tel-qa-001.yaml").write_text("name: tel-qa-001\nsteps: []\n")
    (root / "legacy.yaml").write_text("name: legacy\nsteps: []\n")
    return root


# ---------------------------------------------------------------------------
# 1. Bare name resolves under the platform subdir
# ---------------------------------------------------------------------------


def test_bare_name_resolves_to_linux_subdir(scenarios_root: Path) -> None:
    resolved = resolve_test_path("foo", scenarios_root, subdir="linux")
    assert resolved == scenarios_root / "linux" / "foo.yaml"


def test_tel_qa_resolves_to_linux_subdir(scenarios_root: Path) -> None:
    """The 5 TEL-QA scenarios live under linux/ and must resolve by bare name."""
    resolved = resolve_test_path("tel-qa-001", scenarios_root, subdir="linux")
    assert resolved == scenarios_root / "linux" / "tel-qa-001.yaml"


# ---------------------------------------------------------------------------
# 2. Explicit subdir prefix (linux/foo) also works
# ---------------------------------------------------------------------------


def test_explicit_subdir_path_resolves(scenarios_root: Path) -> None:
    resolved = resolve_test_path("linux/foo", scenarios_root, subdir="linux")
    assert resolved == scenarios_root / "linux" / "foo.yaml"


def test_explicit_subdir_path_ignores_subdir_arg(scenarios_root: Path) -> None:
    """``linux/foo`` is unambiguous; the subdir arg should not double it."""
    resolved = resolve_test_path("linux/foo", scenarios_root, subdir="windows10")
    assert resolved == scenarios_root / "linux" / "foo.yaml"


# ---------------------------------------------------------------------------
# 3. Root fallback for legacy scenarios not yet moved into a subdir
# ---------------------------------------------------------------------------


def test_fallback_to_root_when_subdir_missing(scenarios_root: Path) -> None:
    """If ``linux/legacy.yaml`` does not exist, resolve to ``legacy.yaml`` at root."""
    resolved = resolve_test_path("legacy", scenarios_root, subdir="linux")
    assert resolved == scenarios_root / "legacy.yaml"


def test_no_subdir_arg_falls_through_to_root(scenarios_root: Path) -> None:
    """Caller without a subdir hint should still resolve a root-level scenario."""
    resolved = resolve_test_path("legacy", scenarios_root, subdir=None)
    assert resolved == scenarios_root / "legacy.yaml"


# ---------------------------------------------------------------------------
# 4. Unknown name raises with the candidate-paths diagnostic
# ---------------------------------------------------------------------------


def test_unknown_name_raises_with_candidates(scenarios_root: Path) -> None:
    with pytest.raises(ScenarioNotFoundError) as exc_info:
        resolve_test_path("does-not-exist", scenarios_root, subdir="linux")
    err = exc_info.value
    assert err.test_name == "does-not-exist"
    # Both probed paths should appear in the diagnostic
    candidates_str = "\n".join(str(c) for c in err.candidates)
    assert "linux/does-not-exist.yaml" in candidates_str
    assert "functional/does-not-exist.yaml" in candidates_str


def test_explicit_path_unknown_raises(scenarios_root: Path) -> None:
    """An explicit ``windows10/foo`` that doesn't exist should error, NOT silently fall back."""
    with pytest.raises(ScenarioNotFoundError) as exc_info:
        resolve_test_path("windows10/foo", scenarios_root, subdir="linux")
    err = exc_info.value
    # Only one candidate probed (the explicit one)
    assert len(err.candidates) == 1
    assert "windows10/foo.yaml" in str(err.candidates[0])


# ---------------------------------------------------------------------------
# 5. .yaml extension already present is handled
# ---------------------------------------------------------------------------


def test_name_with_yaml_extension_is_handled(scenarios_root: Path) -> None:
    resolved = resolve_test_path("foo.yaml", scenarios_root, subdir="linux")
    assert resolved == scenarios_root / "linux" / "foo.yaml"


# ---------------------------------------------------------------------------
# 6. Integration: real repo TEL-QA scenarios resolve via the new logic
# ---------------------------------------------------------------------------

_PROJ = Path(__file__).parent.parent
_REAL_ROOT = _PROJ / "shared" / "scenarios" / "functional"


@pytest.mark.skipif(
    not (_REAL_ROOT / "linux").is_dir(),
    reason="repo not in per-OS layout yet",
)
@pytest.mark.parametrize(
    "name",
    [
        "tel-qa-001-settings-discovery",
        "tel-qa-002-enable-disable-gating",
        "tel-qa-005-single-workspace-links",
        "tel-qa-006-multi-workspace-picker",
        "tel-qa-008-global-shortcut",
    ],
)
def test_real_tel_qa_scenarios_resolve(name: str) -> None:
    resolved = resolve_test_path(name, _REAL_ROOT, subdir="linux")
    assert resolved.exists()
    assert resolved.parent.name == "linux"


# ---------------------------------------------------------------------------
# 7. VMConfig.scenario_subdir property — single source of truth for subdir name
# ---------------------------------------------------------------------------


def test_vmconfig_linux_scenario_subdir() -> None:
    from automation.config import VMConfig

    vm = VMConfig(
        name="ubuntu2204",
        vmid=100,
        ip="10.0.0.1",
        packages=[],
        desktop="GNOME",
        os_type="linux",
    )
    assert vm.scenario_subdir == "linux"


def test_vmconfig_windows10_scenario_subdir() -> None:
    from automation.config import VMConfig

    vm = VMConfig(
        name="windows10",
        vmid=104,
        ip="10.0.0.4",
        packages=[],
        desktop="Windows 10",
        os_type="windows",
    )
    assert vm.scenario_subdir == "windows10"


def test_vmconfig_windows11_scenario_subdir() -> None:
    from automation.config import VMConfig

    vm = VMConfig(
        name="windows11",
        vmid=105,
        ip="10.0.0.5",
        packages=[],
        desktop="Windows 11",
        os_type="windows",
    )
    assert vm.scenario_subdir == "windows11"
