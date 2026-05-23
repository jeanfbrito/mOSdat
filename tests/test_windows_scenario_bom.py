"""Regression: Windows scenarios + routines must not use BOM-emitting PowerShell writers.

Windows PowerShell 5.1 (Windows 10 default) writes a UTF-8 BOM (EF BB BF) whenever
``Set-Content -Encoding UTF8`` or ``Out-File -Encoding UTF8`` is used. Rocket.Chat's
``electron-store`` parses ``config.json`` with raw ``JSON.parse`` (no BOM strip), so a
BOM crashes the main process ~700ms after launch with::

    SyntaxError: Unexpected token '﻿'... at mergePersistableValues (app.asar/app/main.js)

The safe pattern is ``[System.IO.File]::WriteAllText($path, $content,
[System.Text.UTF8Encoding]::new($false))`` which works on PS 5.1 + 7.x.

This test guards both directories so a future scenario edit cannot regress the fix.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WIN_SCENARIOS = REPO_ROOT / "shared" / "scenarios" / "functional" / "windows10"
WIN_ROUTINES = REPO_ROOT / "shared" / "routines" / "windows"

# Tokens that, in PowerShell 5.1, always emit a UTF-8 BOM.
BOM_WRITER_PATTERNS = (
    re.compile(r"Set-Content\b[^\n]*-Encoding\s+UTF8\b", re.IGNORECASE),
    re.compile(r"Out-File\b[^\n]*-Encoding\s+UTF8\b", re.IGNORECASE),
    # utf8NoBOM is PS 7+ only and won't work on the win10 VM; still disallow it
    # to keep the canonical writer single.
    re.compile(r"Set-Content\b[^\n]*-Encoding\s+utf8NoBOM\b", re.IGNORECASE),
)


def _yaml_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.yaml") if p.is_file())


def test_no_bom_emitting_writers_in_windows_scenarios() -> None:
    offenders: list[str] = []
    for f in _yaml_files(WIN_SCENARIOS):
        text = f.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pat in BOM_WRITER_PATTERNS:
                if pat.search(line):
                    offenders.append(f"{f.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Found PowerShell writers that emit UTF-8 BOM (crashes RC electron-store). "
        "Use [System.IO.File]::WriteAllText($path, $json, "
        "[System.Text.UTF8Encoding]::new($false)) instead:\n  "
        + "\n  ".join(offenders)
    )


def test_no_bom_emitting_writers_in_windows_routines() -> None:
    offenders: list[str] = []
    for f in _yaml_files(WIN_ROUTINES):
        text = f.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pat in BOM_WRITER_PATTERNS:
                if pat.search(line):
                    offenders.append(f"{f.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Found PowerShell writers that emit UTF-8 BOM in routines:\n  "
        + "\n  ".join(offenders)
    )
