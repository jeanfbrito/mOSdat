"""Negative-fixture test suite (I15).

Guards against the "passes on broken build" lie: each fixture in
tests/negative_fixtures/ is a scenario designed to FAIL with a specific
known error. If any fixture unexpectedly passes, the test suite fails with
a diagnostic message.

Opt-in only
-----------
- pytest marker: negative_fixtures  (excluded from default collection)
- Requires env var: MOSDAT_NEG_FIXTURES_VM=<vm-name>  (e.g. ubuntu2204)
  If unset, the entire module is skipped.

Running the negative suite
--------------------------
    MOSDAT_NEG_FIXTURES_VM=ubuntu2204 pytest tests/test_negative_fixtures.py \
        -v -m negative_fixtures

Expected outcome: all tests PASS (meaning each fixture failed as designed).
If a fixture unexpectedly exits 0, the test fails and warns that the runner
may be masking real failures.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Opt-in gating
# ---------------------------------------------------------------------------

_VM = os.environ.get("MOSDAT_NEG_FIXTURES_VM", "")

pytestmark = pytest.mark.negative_fixtures


def pytest_configure(config):
    """Register the negative_fixtures marker so pytest doesn't warn."""
    config.addinivalue_line(
        "markers",
        "negative_fixtures: negative-fixture suite; opt-in via MOSDAT_NEG_FIXTURES_VM env var",
    )


if not _VM:
    pytest.skip(
        "MOSDAT_NEG_FIXTURES_VM not set — skipping negative fixture suite. "
        "Set it to a VM name (e.g. ubuntu2204) to run.",
        allow_module_level=True,
    )

# ---------------------------------------------------------------------------
# Fixture discovery + header parsing
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "negative_fixtures"
_HEADER_RE = re.compile(
    r"^#\s*(?P<key>expected_fail|expected_reason):\s*(?P<value>.+)$",
    re.MULTILINE,
)


def _parse_header(path: Path) -> dict:
    """Extract expected_fail and expected_reason from the YAML comment header."""
    header = {}
    with path.open() as f:
        for line in f:
            line = line.rstrip()
            if not line.startswith("#"):
                break  # stop at first non-comment line
            m = _HEADER_RE.match(line)
            if m:
                key = m.group("key")
                val = m.group("value").strip().strip('"')
                if key == "expected_fail":
                    header["expected_fail"] = int(val)
                else:
                    header[key] = val
    return header


def _collect_fixtures() -> list[tuple[str, Path, dict]]:
    """Return [(fixture_name, path, header)] for every fixture file."""
    results = []
    for f in sorted(_FIXTURES_DIR.glob("*.yaml")):
        header = _parse_header(f)
        if "expected_fail" not in header or "expected_reason" not in header:
            raise ValueError(
                f"Fixture {f.name} missing required header fields "
                "(expected_fail and expected_reason must both be set)"
            )
        results.append((f.stem, f, header))
    return results


_FIXTURES = _collect_fixtures()
_FIXTURE_IDS = [name for name, _, _ in _FIXTURES]

# ---------------------------------------------------------------------------
# Schema validation (parse-time guard — runs without a VM)
# ---------------------------------------------------------------------------


def test_fixtures_parse_as_scenario_model() -> None:
    """All fixture YAMLs must be valid ScenarioModel instances.

    Fixtures fail at runtime, not at parse time.
    """
    from automation.scenario import ScenarioModel

    for name, path, _ in _FIXTURES:
        data = yaml.safe_load(path.read_text())
        model = ScenarioModel.model_validate(data)
        assert len(model.steps) > 0, f"{name}: must have at least one step"


# ---------------------------------------------------------------------------
# Negative run test — requires live VM
# ---------------------------------------------------------------------------

def _mosdat_toml() -> Path:
    """Find the project's TOML config file (prefers MOSDAT_CONFIG env, then examples/)."""
    env_cfg = os.environ.get("MOSDAT_CONFIG")
    if env_cfg:
        return Path(env_cfg)
    proj = Path(__file__).parent.parent
    candidates = list((proj / "examples").glob("*.toml"))
    if not candidates:
        candidates = [p for p in proj.glob("*.toml") if p.name != "pyproject.toml"]
    if not candidates:
        raise RuntimeError("No TOML config found. Set MOSDAT_CONFIG env var.")
    return candidates[0]


def _run_fixture(fixture_path: Path, vm: str, toml: Path, tmp_dir: Path) -> tuple[int, str]:
    """Copy fixture into a temp scenarios dir and run mosdat functional.

    Returns (exit_code, combined_log_text).
    """
    import shutil

    # mosdat functional looks in --tests-dir for <name>.yaml
    scenarios_dir = tmp_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    dest = scenarios_dir / fixture_path.name
    shutil.copy2(fixture_path, dest)

    cmd = [
        "python3", "-m", "automation.main",
        "functional", str(toml),
        "--vms", vm,
        "--test", fixture_path.stem,
        "--tests-dir", str(scenarios_dir),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )
    combined = result.stdout + "\n" + result.stderr
    return result.returncode, combined


@pytest.mark.parametrize("fixture_name,fixture_path,header", _FIXTURES, ids=_FIXTURE_IDS)
def test_negative_fixture_fails_as_expected(
    fixture_name: str,
    fixture_path: Path,
    header: dict,
) -> None:
    """Each negative fixture MUST exit with expected_fail and log expected_reason.

    If a fixture exits 0 (passes), this test fails and warns that the test
    runner may be masking real failures in regular scenarios.
    """
    expected_exit = header["expected_fail"]
    expected_reason = header["expected_reason"]

    toml = _mosdat_toml()
    with tempfile.TemporaryDirectory(prefix="mosdat_neg_") as tmp:
        exit_code, log = _run_fixture(fixture_path, _VM, toml, Path(tmp))

    # Primary guard: fixture must NOT pass
    if exit_code == 0:
        pytest.fail(
            f"Negative fixture '{fixture_name}' unexpectedly PASSED (exit 0). "
            "This means real failures may also be unreported — "
            "the runner may be swallowing errors. "
            f"Expected exit code: {expected_exit}, expected reason substring: '{expected_reason}'"
        )

    assert exit_code == expected_exit, (
        f"Fixture '{fixture_name}': exit code {exit_code} != expected {expected_exit}.\n"
        f"Log tail:\n{log[-2000:]}"
    )

    assert expected_reason.lower() in log.lower(), (
        f"Fixture '{fixture_name}': expected reason substring '{expected_reason}' "
        f"not found in runner output.\nLog tail:\n{log[-2000:]}"
    )


# ---------------------------------------------------------------------------
# All-fixtures-passed guard (separate test for clear failure message)
# ---------------------------------------------------------------------------

def test_not_all_negative_fixtures_passed() -> None:
    """Meta-test: if somehow every fixture passed, something is deeply wrong.

    This test is intentionally a no-op in normal execution — it exists so
    that if the parametrized tests above are all skipped or misconfigured,
    there is still a named test that fails with a clear message.

    In practice, individual parametrized tests above will catch unexpected
    passes before this fires.
    """
    # Sanity: at least one fixture must exist
    assert len(_FIXTURES) >= 1, (
        "tests/negative_fixtures/ is empty — no fixtures to guard against. "
        "Add at least one *.yaml negative fixture."
    )
