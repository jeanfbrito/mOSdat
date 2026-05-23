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
        # NOTE: do NOT pop "PIL" or "PIL.Image" — multiple pop+reimport
        # cycles produce distinct PIL.Image MODULE INSTANCES (each with its
        # own Image class), and downstream isinstance() checks compare
        # across the copies and fail. Real PIL is installed in the venv and
        # never needs to be a stub; just leave its sys.modules entries alone.
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
import json
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
    pick_artifact_url,
    resolve_artifact,
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


def test_targets_table_contains_deb_and_exe() -> None:
    # deb = Linux .deb deploy (Phase 1). exe = Windows NSIS deploy (this task).
    # rpm / AppImage remain TODO; expand this set when they land.
    assert set(TARGETS) == {"deb", "exe"}


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
        artifact_first=False,  # skip artifact lookup so _capture is not called
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
    assert "--artifact-first" in result.stdout
    assert "--no-artifact-first" in result.stdout


# ---------------------------------------------------------------------------
# Artifact-first helpers
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> dict:
    fixture_path = Path(__file__).parent / "fixtures" / name
    with open(fixture_path) as f:
        return json.load(f)


def test_artifact_first_picks_latest_matching_comment() -> None:
    """pick_artifact_url returns the URL from the MOST RECENT github-actions
    comment that contains a .deb link, ignoring older bot comments and
    non-bot comments."""
    pr_data = _load_fixture("pr3325_comments.json")
    url = pick_artifact_url(pr_data, ".deb")

    # The fixture has two github-actions[bot] comments with .deb links.
    # The later one (2026-05-10T10:30:00Z) has rocketchat-4.14.1-linux-amd64.deb.
    assert url == (
        "https://s3.us-east-1.wasabisys.com/builds.cloud.rocket.chat"
        "/pr-3325/ubuntu-latest/rocketchat-4.14.1-linux-amd64.deb"
    )


def test_artifact_first_falls_back_when_no_comment() -> None:
    """pick_artifact_url returns None when no bot comment contains an S3 .deb URL."""
    pr_data = {
        "comments": [
            {
                "author": {"login": "jeanfbrito"},
                "body": "No S3 links here.",
                "createdAt": "2026-05-10T10:00:00Z",
            },
            {
                "author": {"login": "github-actions[bot]"},
                "body": "Build succeeded! Download from our internal mirror.",
                "createdAt": "2026-05-10T10:30:00Z",
            },
        ],
        "commits": [{"oid": "abc", "committedDate": "2026-05-10T10:00:00Z"}],
    }
    url = pick_artifact_url(pr_data, ".deb")
    assert url is None


def test_artifact_first_caches_by_sha(tmp_path: Path, monkeypatch) -> None:
    """resolve_artifact returns the cached path without calling download when
    a .sha sidecar matching the PR head SHA already exists."""
    import json as _json

    pr = 3325
    head_sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"

    # Pre-populate cache dir.
    cache_dir = tmp_path / ".cache" / "mosdat" / f"pr{pr}"
    cache_dir.mkdir(parents=True)
    cached_deb = cache_dir / "rocketchat-4.14.1-linux-amd64.deb"
    cached_deb.write_bytes(b"\x00fake")
    # Write the SHA sidecar that matches the PR head.
    (cached_deb.with_suffix(".deb.sha")).write_text(head_sha)

    # Redirect Path.home() so the function uses our tmp cache.
    monkeypatch.setattr(build_mod.Path, "home", staticmethod(lambda: tmp_path))

    # Stub Gate 1: label is present.
    def mock_gh_pr_view_labels(_pr, _repo):
        return {"labels": [{"name": "build-artifacts"}]}

    # Stub gh_pr_head_ref.
    def mock_gh_pr_head_ref(_pr, _repo):
        return "feature-branch"

    # Stub Gate 2: CI run succeeded with matching SHA.
    def mock_gh_run_list_builds(_repo, _branch):
        return [
            {
                "name": "build and test",
                "conclusion": "success",
                "headSha": head_sha,
                "createdAt": "2026-05-10T10:30:00Z",
            }
        ]

    # Stub fetch_pr_metadata to return minimal PR data with matching SHA.
    pr_data = {
        "headRefOid": head_sha,
        "commits": [
            {"oid": head_sha, "committedDate": "2026-05-10T10:00:00Z"}
        ],
        "comments": [],
        "updatedAt": "2026-05-10T12:00:00Z",
    }
    monkeypatch.setattr(build_mod, "fetch_pr_metadata", lambda _pr, _repo: pr_data)
    monkeypatch.setattr(build_mod, "_gh_pr_view_labels", mock_gh_pr_view_labels)
    monkeypatch.setattr(build_mod, "gh_pr_head_ref", mock_gh_pr_head_ref)
    monkeypatch.setattr(build_mod, "_gh_run_list_builds", mock_gh_run_list_builds)

    # Stub _run / _capture to fail loudly if called (download must NOT happen).
    def _no_download(*a, **kw):
        raise AssertionError(f"unexpected _run/_capture in cache-hit path: {a}")

    monkeypatch.setattr(build_mod, "_run", _no_download)
    monkeypatch.setattr(build_mod, "_capture", _no_download)

    result = resolve_artifact(pr, "RocketChat/Rocket.Chat.Electron", ".deb")
    assert result == cached_deb


def test_artifact_first_falls_back_when_label_missing(monkeypatch, capsys) -> None:
    """resolve_artifact returns None and logs when PR lacks 'build-artifacts' label."""
    pr = 3325
    repo = "RocketChat/Rocket.Chat.Electron"

    # Stub _gh_pr_view_labels to return empty labels (no 'build-artifacts').
    def mock_gh_pr_view_labels(_pr, _repo):
        return {"labels": [{"name": "bug"}, {"name": "enhancement"}]}

    monkeypatch.setattr(build_mod, "_gh_pr_view_labels", mock_gh_pr_view_labels)

    result = resolve_artifact(pr, repo, ".deb")
    assert result is None

    out = capsys.readouterr().out
    assert "[artifact-first] PR #3325 missing 'build-artifacts' label" in out
    assert "Add the label to skip yarn build on future runs" in out


def test_artifact_first_falls_back_when_ci_run_for_older_sha(monkeypatch, capsys) -> None:
    """resolve_artifact returns None when latest CI build is for an older HEAD SHA."""
    pr = 3325
    repo = "RocketChat/Rocket.Chat.Electron"
    old_sha = "0000000000000000000000000000000000000000"
    new_sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"

    # Mock Gate 1: label is present.
    def mock_gh_pr_view_labels(_pr, _repo):
        return {"labels": [{"name": "build-artifacts"}]}

    # Mock gh_pr_head_ref.
    def mock_gh_pr_head_ref(_pr, _repo):
        return "feature-branch"

    # Mock Gate 2: CI run list returns a run for an older SHA.
    def mock_gh_run_list_builds(_repo, _branch):
        return [
            {
                "name": "build and test",
                "conclusion": "success",
                "headSha": old_sha,
                "createdAt": "2026-05-10T10:00:00Z",
            }
        ]

    # Mock fetch_pr_metadata to return current (newer) HEAD SHA.
    def mock_fetch_pr_metadata(_pr, _repo):
        return {
            "commits": [{"oid": new_sha, "committedDate": "2026-05-10T10:30:00Z"}],
            "comments": [],
            "updatedAt": "2026-05-10T12:00:00Z",
        }

    monkeypatch.setattr(build_mod, "_gh_pr_view_labels", mock_gh_pr_view_labels)
    monkeypatch.setattr(build_mod, "gh_pr_head_ref", mock_gh_pr_head_ref)
    monkeypatch.setattr(build_mod, "_gh_run_list_builds", mock_gh_run_list_builds)
    monkeypatch.setattr(build_mod, "fetch_pr_metadata", mock_fetch_pr_metadata)

    result = resolve_artifact(pr, repo, ".deb")
    assert result is None

    out = capsys.readouterr().out
    assert "[artifact-first] CI build hasn't run for current HEAD" in out
    assert "falling back" in out


def test_artifact_first_falls_back_when_ci_failed(monkeypatch, capsys) -> None:
    """resolve_artifact returns None when latest CI build failed."""
    pr = 3325
    repo = "RocketChat/Rocket.Chat.Electron"
    head_sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"

    # Mock Gate 1: label is present.
    def mock_gh_pr_view_labels(_pr, _repo):
        return {"labels": [{"name": "build-artifacts"}]}

    # Mock gh_pr_head_ref.
    def mock_gh_pr_head_ref(_pr, _repo):
        return "feature-branch"

    # Mock Gate 2: CI run list returns a run with same HEAD SHA but failed conclusion.
    def mock_gh_run_list_builds(_repo, _branch):
        return [
            {
                "name": "build and test",
                "conclusion": "failure",
                "headSha": head_sha,
                "createdAt": "2026-05-10T10:30:00Z",
            }
        ]

    # Mock fetch_pr_metadata.
    def mock_fetch_pr_metadata(_pr, _repo):
        return {
            "commits": [{"oid": head_sha, "committedDate": "2026-05-10T10:00:00Z"}],
            "comments": [],
            "updatedAt": "2026-05-10T12:00:00Z",
        }

    monkeypatch.setattr(build_mod, "_gh_pr_view_labels", mock_gh_pr_view_labels)
    monkeypatch.setattr(build_mod, "gh_pr_head_ref", mock_gh_pr_head_ref)
    monkeypatch.setattr(build_mod, "_gh_run_list_builds", mock_gh_run_list_builds)
    monkeypatch.setattr(build_mod, "fetch_pr_metadata", mock_fetch_pr_metadata)

    result = resolve_artifact(pr, repo, ".deb")
    assert result is None

    out = capsys.readouterr().out
    assert "[artifact-first] latest CI build failed (conclusion=failure)" in out
    assert "falling back" in out


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
