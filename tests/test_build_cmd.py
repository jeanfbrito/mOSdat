"""Unit tests for ``mosdat build`` (I3).

Integration smoke (real clone + yarn build + deploy) is opt-in via the
``MOSDAT_TEST_BUILD=1`` env var. By default we only exercise pure helpers
that don't touch the network, the filesystem outside tmp_path, or a VM.
"""

from __future__ import annotations


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
        or _name.startswith("PIL")
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

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

from automation.commands import build as build_mod
from automation.commands.build import (
    TARGETS,
    derive_clone_dir,
    match_artifact,
    parse_verify_symbols,
    resolve_target,
)
from automation.commands.parser import build_parser


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def _parse(argv: list[str]) -> argparse.Namespace:
    parser = build_parser()
    return parser.parse_args(argv)


def test_build_subcommand_is_registered() -> None:
    args = _parse([
        "build",
        "--pr", "3325",
        "--repo", "RocketChat/Rocket.Chat.Electron",
        "--target", "deb",
        "--clone-dir", "/tmp/x",
        "--deploy", "ubuntu2204,ubuntu2404",
        "--verify-symbol", "isTelephonyEnabled",
        "--verify-symbol", "telephonyGlobalShortcutConfig",
        "--dry-run",
    ])
    assert args.command == "build"
    assert args.pr == "3325"
    assert args.repo == "RocketChat/Rocket.Chat.Electron"
    assert args.target == "deb"
    assert args.clone_dir == "/tmp/x"
    assert args.deploy == "ubuntu2204,ubuntu2404"
    assert args.verify_symbol == [
        "isTelephonyEnabled",
        "telephonyGlobalShortcutConfig",
    ]
    assert args.dry_run is True


def test_build_requires_pr() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["build", "--target", "deb"])


def test_build_rejects_unknown_target() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["build", "--pr", "1", "--target", "snap"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_derive_clone_dir_uses_repo_slug_and_pr() -> None:
    p = derive_clone_dir(3325, "RocketChat/Rocket.Chat.Electron", None)
    # Expand the user, then check the suffix — robust to different $HOME values.
    assert p.name == "rocket.chat.electron-pr3325"
    assert p.parent.name == "projects"


def test_derive_clone_dir_override_wins(tmp_path: Path) -> None:
    override = tmp_path / "custom"
    p = derive_clone_dir(3325, "x/y", str(override))
    assert p == override


def test_resolve_target_known_and_unknown() -> None:
    target = resolve_target("deb")
    assert target.name == "deb"
    assert target.yarn_release_args == ["--linux", "deb"]
    assert "{artifact}" in target.install_cmd_template
    with pytest.raises(ValueError):
        resolve_target("snap")


def test_parse_verify_symbols_dedups_and_splits_commas() -> None:
    out = parse_verify_symbols([
        "isTelephonyEnabled",
        "telephonyGlobalShortcutConfig,isTelephonyEnabled",
        "  spaced  ",
    ])
    assert out == [
        "isTelephonyEnabled",
        "telephonyGlobalShortcutConfig",
        "spaced",
    ]


def test_match_artifact_returns_newest(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    older = dist / "old.deb"
    newer = dist / "new.deb"
    older.write_bytes(b"x")
    newer.write_bytes(b"x")
    # Force a deterministic mtime gap so the test is stable on fast FS.
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    other = dist / "ignored.txt"
    other.write_bytes(b"x")

    hit = match_artifact(dist, "*.deb")
    assert hit == newer

    assert match_artifact(dist, "*.rpm") is None
    assert match_artifact(tmp_path / "missing", "*.deb") is None


def test_targets_table_only_deb_in_v1() -> None:
    # If we add rpm/AppImage/exe later, this test should be updated alongside
    # docs/IMPROVEMENTS.md and the spec example.
    assert set(TARGETS) == {"deb"}


# ---------------------------------------------------------------------------
# Dry-run end-to-end (no network, no subprocess)
# ---------------------------------------------------------------------------

def test_run_build_dry_run_succeeds(monkeypatch, capsys) -> None:
    # Belt-and-braces: monkeypatch _run/_capture so even an accidental
    # non-dry-run code path can't reach git/gh/yarn from inside the test.
    def _refuse_run(*a, **kw):
        raise AssertionError(f"unexpected _run call: {a} {kw}")

    def _refuse_capture(*a, **kw):
        raise AssertionError(f"unexpected _capture call: {a} {kw}")

    monkeypatch.setattr(build_mod, "_run", _refuse_run)
    monkeypatch.setattr(build_mod, "_capture", _refuse_capture)

    args = argparse.Namespace(
        pr="3325",
        repo="RocketChat/Rocket.Chat.Electron",
        target="deb",
        clone_dir="/tmp/mosdat-test-clone",
        deploy="ubuntu2204,ubuntu2404",
        verify_symbol=["isTelephonyEnabled", "telephonyGlobalShortcutConfig"],
        config=None,
        dry_run=True,
    )
    rc = build_mod.run_build(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "ubuntu2204" in out
    assert "ubuntu2404" in out
    assert "verify symbols → ['isTelephonyEnabled'" in out


# ---------------------------------------------------------------------------
# Help works (CLI smoke)
# ---------------------------------------------------------------------------

def test_cli_build_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "automation.main", "build", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--pr" in result.stdout
    assert "--target" in result.stdout
    assert "--verify-symbol" in result.stdout
    assert "--dry-run" in result.stdout


# ---------------------------------------------------------------------------
# Integration smoke — gated by env var because it pulls 800 MiB of yarn deps
# and takes 5+ minutes per target.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("MOSDAT_TEST_BUILD") != "1",
    reason="set MOSDAT_TEST_BUILD=1 to run the real PR build+deploy smoke test",
)
def test_real_build_smoke(tmp_path: Path) -> None:  # pragma: no cover
    args = argparse.Namespace(
        pr=os.environ.get("MOSDAT_TEST_BUILD_PR", "3325"),
        repo=os.environ.get(
            "MOSDAT_TEST_BUILD_REPO", "RocketChat/Rocket.Chat.Electron"
        ),
        target="deb",
        clone_dir=str(tmp_path / "clone"),
        deploy="",  # build only; deploy phase requires live VMs
        verify_symbol=[],
        config=None,
        dry_run=False,
    )
    rc = build_mod.run_build(args)
    assert rc in (0, 1)  # 0 = ok, 1 = missing symbol (none requested, so 0 expected)
