"""Snapshot tests: detect drift in mosdat CLI help output.

Each test captures `python -m automation.main <sub> --help` via subprocess
and diffs against the snapshot stored in tests/expected_help/<sub>.txt.
A mismatch means the CLI interface changed without updating the snapshot —
fix the snapshot or revert the CLI change.

To regenerate snapshots after an intentional change:
    python -m automation.main --help > tests/expected_help/mosdat.txt 2>&1
    for sub in run test functional validate list-vms report dashboard author visual; do
        python -m automation.main $sub --help > tests/expected_help/${sub}.txt 2>&1
    done
"""

import subprocess
import sys
from pathlib import Path

import pytest

SNAPSHOT_DIR = Path(__file__).parent / "expected_help"

# (label, extra_args) — label matches snapshot filename (without .txt)
CASES = [
    ("mosdat",     []),
    ("run",        ["run"]),
    ("test",       ["test"]),
    ("functional", ["functional"]),
    ("validate",   ["validate"]),
    ("list-vms",   ["list-vms"]),
    ("report",     ["report"]),
    ("dashboard",  ["dashboard"]),
    ("author",     ["author"]),
    ("visual",     ["visual"]),
]


@pytest.mark.parametrize("label,extra", CASES)
def test_help_no_drift(label: str, extra: list[str]) -> None:
    snapshot_path = SNAPSHOT_DIR / f"{label}.txt"
    assert snapshot_path.exists(), (
        f"Snapshot missing: {snapshot_path}. "
        "Run the regeneration commands at the top of this file."
    )

    argv = [sys.executable, "-m", "automation.main"] + extra + ["--help"]
    result = subprocess.run(argv, capture_output=True, text=True)
    actual = result.stdout

    expected = snapshot_path.read_text()

    assert actual == expected, (
        f"`{' '.join(argv)}` output has drifted from snapshot.\n"
        f"--- expected ({snapshot_path}) ---\n{expected}\n"
        f"--- actual ---\n{actual}\n"
        "Update the snapshot if the change is intentional."
    )
