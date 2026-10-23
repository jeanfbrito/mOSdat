"""``mosdat build`` — clone/update a PR, build the Electron app, deploy + verify on VMs.

Implements roadmap item I3 (see docs/IMPROVEMENTS.md).

Design notes:
- Uses the ``gh`` CLI for PR metadata and cloning. We rely on ``gh`` being
  authenticated (the rest of mosdat already does).
- The build is long (5+ minutes for yarn release). We log to a per-PR tempfile
  under ``/tmp/mosdat-build-<pr>.log`` and print only the tail to avoid flooding
  the agent context.
- ``--target deb`` is implemented end-to-end on Linux. ``--target exe`` is
  implemented end-to-end on Windows. When every ``--deploy`` VM is Windows,
  clone+yarn+electron-builder run natively on the VM over SSH (host Wine is
  not required / not viable on macOS Catalina+). Mixed Linux+Windows deploy
  lists still build on the host. Install is driven by PowerShell +
  electron-builder NSIS ``/S`` silent flag. rpm/AppImage paths remain
  stubbed with TODO comments so the dispatcher table stays cheap to extend.
- ``--artifact-first`` (default ON): fetch the prebuilt .deb from the S3 URL
  posted by the github-actions bot in PR comments before falling back to local
  yarn build. Requires ``gh`` and ``aria2c`` (or ``curl``) on PATH.

Runtime tool requirements:
- ``gh`` — GitHub CLI, authenticated (used for PR metadata, cloning).
- ``aria2c`` — fast multi-connection downloader (preferred). Falls back to
  ``curl -L --fail`` if aria2c is not present.

Exit codes:
    0  — all OK
    1  — verify-symbol missing on at least one VM
    2  — build phase failed
    3  — deploy phase failed (SCP / install)
    4  — clone / fetch / checkout phase failed
    5  — invalid args / preconditions
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Target plug-in registry.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BuildTarget:
    name: str
    dist_glob: str  # glob (relative to <clone>/dist) matching the built artifact
    yarn_release_args: list[str]
    install_cmd_template: str  # {artifact} placeholder; runs on the VM


TARGETS: dict[str, BuildTarget] = {
    "deb": BuildTarget(
        name="deb",
        dist_glob="*.deb",
        yarn_release_args=["--linux", "deb"],
        # `dpkg -i` followed by `apt-get install -y -f` to pull deferred deps.
        install_cmd_template=(
            "sudo dpkg -i {artifact} || sudo apt-get install -y -f"
        ),
    ),
    "rpm": BuildTarget(
        name="rpm",
        dist_glob="*.rpm",
        yarn_release_args=["--linux", "rpm"],
        install_cmd_template=(
            "if command -v dnf >/dev/null 2>&1; then "
            "sudo dnf install -y {artifact} || true; "
            "sudo rpm -Uvh --force {artifact}; "
            "elif command -v zypper >/dev/null 2>&1; then "
            "sudo zypper install -y --allow-unsigned-rpm {artifact} || true; "
            "sudo rpm -Uvh --force {artifact}; "
            "else sudo rpm -Uvh --force {artifact}; fi"
        ),
    ),
    "appimage": BuildTarget(
        name="appimage",
        dist_glob="*.AppImage",
        yarn_release_args=["--linux", "AppImage"],
        install_cmd_template=(
            "sudo mkdir -p /opt/Rocket.Chat && "
            "sudo install -m 0755 {artifact} /opt/Rocket.Chat/rocketchat-desktop && "
            "printf '%s\n' '[Desktop Entry]' 'Type=Application' "
            "'Name=Rocket.Chat' 'Exec=/opt/Rocket.Chat/rocketchat-desktop %U' "
            "'Terminal=false' 'Categories=Network;InstantMessaging;' "
            "'MimeType=x-scheme-handler/tel;x-scheme-handler/callto;' "
            "| sudo tee /usr/share/applications/rocketchat-desktop.desktop >/dev/null && "
            "(sudo update-desktop-database /usr/share/applications >/dev/null 2>&1 || true)"
        ),
    ),
    "exe": BuildTarget(
        name="exe",
        # electron-builder NSIS output, e.g. ``rocketchat-4.14.1-win-x64.exe``.
        dist_glob="*.exe",
        yarn_release_args=["--win"],
        # NOTE: install_cmd_template is NOT used by the Windows deploy path —
        # deploy_to_windows_vm() drives the installer directly via PowerShell
        # (kill running processes → silent install with /S → verify). We keep
        # the field set so dispatch / dry-run logs stay consistent.
        install_cmd_template=(
            "Start-Process -FilePath {artifact} -ArgumentList '/S' -Wait"
        ),
    ),
}


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(msg: str) -> None:
    print(f"[mosdat build {_ts()}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Step result accounting
# ---------------------------------------------------------------------------

@dataclass
class DeployResult:
    vm: str
    scp_ok: bool = False
    install_ok: bool = False
    installed_version: str = ""
    missing_symbols: list[str] = field(default_factory=list)
    error: str = ""
    # Path of the installer that was (or would be) installed: the local file
    # for an SCP deploy, the VM-side path for a native Windows build/install.
    artifact: str = ""

    @property
    def ok(self) -> bool:
        return (
            self.scp_ok
            and self.install_ok
            and not self.missing_symbols
            and not self.error
        )


# ---------------------------------------------------------------------------
# Subprocess wrappers
# ---------------------------------------------------------------------------

def _run(
    cmd: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int = 60,
    log_file: Optional[Path] = None,
    env: Optional[dict] = None,
) -> int:
    """Run ``cmd``. If ``log_file`` is set, stream stdout+stderr to it; otherwise
    let it bleed to the user's console. Returns the exit code.

    NOTE: never returns captured output here — callers either tail the log file
    or read from a separate ``_capture`` helper.
    """
    _log(f"$ {' '.join(shlex.quote(c) for c in cmd)}" + (f"  (cwd={cwd})" if cwd else ""))
    if log_file is not None:
        with open(log_file, "ab") as f:
            f.write(f"\n==== {_ts()} :: {' '.join(shlex.quote(c) for c in cmd)} ====\n".encode())
            f.flush()
            try:
                proc = subprocess.run(
                    cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT,
                    timeout=timeout, env=env,
                )
                return proc.returncode
            except subprocess.TimeoutExpired:
                f.write(f"\n!!! TIMEOUT after {timeout}s !!!\n".encode())
                return 124
    try:
        proc = subprocess.run(cmd, cwd=cwd, timeout=timeout, env=env)
        return proc.returncode
    except subprocess.TimeoutExpired:
        _log(f"TIMEOUT after {timeout}s: {cmd[0]}")
        return 124


def _capture(cmd: list[str], *, cwd: Optional[Path] = None, timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def _tail_log(path: Path, lines: int = 20) -> None:
    if not path.exists():
        _log(f"(no log file at {path})")
        return
    try:
        with open(path, "r", errors="replace") as f:
            data = f.readlines()
        tail = data[-lines:]
        _log(f"--- last {len(tail)} lines of {path} ---")
        for line in tail:
            sys.stdout.write(line if line.endswith("\n") else line + "\n")
        _log(f"--- end {path} ---")
    except Exception as e:  # pragma: no cover - defensive
        _log(f"failed to tail log {path}: {e}")


# ---------------------------------------------------------------------------
# Helpers exposed for unit tests
# ---------------------------------------------------------------------------

def derive_clone_dir(pr: int, repo: str, override: Optional[str]) -> Path:
    """Pick a clone dir. Override always wins. Else use the repo name."""
    if override:
        return Path(override).expanduser()
    # repo like "RocketChat/Rocket.Chat.Electron" → "rocket.chat.electron"
    repo_slug = repo.split("/")[-1].lower()
    return Path.home() / "projects" / f"{repo_slug}-pr{pr}"


def resolve_target(name: str) -> BuildTarget:
    if name not in TARGETS:
        raise ValueError(
            f"unsupported --target {name!r}; supported targets: {sorted(TARGETS)}"
        )
    return TARGETS[name]


def match_artifact(dist_dir: Path, glob: str) -> Optional[Path]:
    """Return the most recently modified file in ``dist_dir`` matching glob."""
    if not dist_dir.is_dir():
        return None
    matches = sorted(dist_dir.glob(glob), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


# Every real Rocket.Chat package is tens of MiB. Anything below this floor is a
# truncated download or an empty packaging result (a 96-byte .deb was seen).
_ARTIFACT_MIN_BYTES = 1024 * 1024

# Leading bytes per target. deb = ar archive, rpm = lead magic, AppImage = ELF,
# exe = DOS/PE "MZ" header.
_ARTIFACT_MAGIC: dict[str, bytes] = {
    "deb": b"!<arch>\n",
    "rpm": b"\xed\xab\xee\xdb",
    "appimage": b"\x7fELF",
    "exe": b"MZ",
}

_EXT_TO_TARGET: dict[str, str] = {
    ".deb": "deb",
    ".rpm": "rpm",
    ".AppImage": "appimage",
    ".exe": "exe",
}


def validate_artifact(path: Path, target_name: str) -> str:
    """Return ``""`` when ``path`` looks like a real ``target_name`` package,
    else a one-line reason.

    Checks: the file exists, is at least 1 MiB, and starts with the magic
    bytes of the target format. An unknown ``target_name`` gets only the
    existence and size checks.
    """
    path = Path(path)
    if not path.is_file():
        return f"artifact not found: {path}"
    size = path.stat().st_size
    if size < _ARTIFACT_MIN_BYTES:
        return (
            f"artifact {path.name} is {size} bytes (< {_ARTIFACT_MIN_BYTES} bytes) — "
            "empty or truncated package"
        )
    magic = _ARTIFACT_MAGIC.get(target_name)
    if magic:
        with open(path, "rb") as f:
            head = f.read(len(magic))
        if head != magic:
            return (
                f"artifact {path.name} is not a {target_name} package "
                f"(leading bytes {head!r}, expected {magic!r})"
            )
    return ""


def parse_verify_symbols(values: Iterable[str]) -> list[str]:
    """Accept a list of --verify-symbol values that may themselves be
    comma-separated (matches the example in IMPROVEMENTS.md).
    """
    out: list[str] = []
    for v in values:
        for s in v.split(","):
            s = s.strip()
            if s and s not in out:
                out.append(s)
    return out


# ---------------------------------------------------------------------------
# gh CLI helpers
# ---------------------------------------------------------------------------

def gh_pr_head_ref(pr: int, repo: str) -> str:
    rc, out, err = _capture(
        ["gh", "pr", "view", str(pr), "--repo", repo, "--json", "headRefName,headRefOid"],
        timeout=30,
    )
    if rc != 0:
        raise RuntimeError(f"`gh pr view {pr}` failed: {err.strip()}")
    data = json.loads(out)
    return data["headRefName"]


def _gh_pr_view_labels(pr: int, repo: str) -> dict:
    """Call ``gh pr view --json labels`` and return the parsed labels dict.

    Returns a dict with a "labels" key containing a list of {"name": "..."}
    objects.
    """
    rc, out, err = _capture(
        ["gh", "pr", "view", str(pr), "--repo", repo, "--json", "labels"],
        timeout=30,
    )
    if rc != 0:
        raise RuntimeError(f"`gh pr view {pr}` (labels) failed: {err.strip()}")
    return json.loads(out)


def _gh_run_list_builds(repo: str, branch: str) -> list:
    """Call ``gh run list`` for builds on a given branch.

    Returns a list of run objects:
    [{"name": "...", "conclusion": "...", "headSha": "...", "createdAt": "..."}, ...]
    """
    rc, out, err = _capture(
        [
            "gh", "run", "list",
            "--repo", repo,
            "--branch", branch,
            "--json", "conclusion,headSha,name,createdAt",
            "--limit", "20",
        ],
        timeout=30,
    )
    if rc != 0:
        raise RuntimeError(f"`gh run list` failed: {err.strip()}")
    return json.loads(out)


# ---------------------------------------------------------------------------
# Artifact-first helpers  (--artifact-first / --no-artifact-first)
# ---------------------------------------------------------------------------

# S3 URL pattern used by the github-actions CI bot.
# Example: https://s3.us-east-1.wasabisys.com/builds.cloud.rocket.chat/pr-3325/ubuntu-latest/rocketchat-4.14.1-linux-amd64.deb
_S3_DEB_RE = re.compile(r"https://[^\s)\"']+\.deb")
_S3_RPM_RE = re.compile(r"https://[^\s)\"']+\.rpm")
_S3_APPIMAGE_RE = re.compile(r"https://[^\s)\"']+\.AppImage")
# Windows installer S3 URL pattern (electron-builder NSIS .exe).
# Example: .../pr-3325/windows-latest/rocketchat-4.14.1-win-x64.exe
_S3_EXE_RE = re.compile(r"https://[^\s)\"']+\.exe")

# Map artifact extension → URL regex. New formats only need to register here.
_ARTIFACT_PATTERNS: dict[str, re.Pattern] = {
    ".deb": _S3_DEB_RE,
    ".rpm": _S3_RPM_RE,
    ".AppImage": _S3_APPIMAGE_RE,
    ".exe": _S3_EXE_RE,
}

# Bot identity used by the GitHub Actions app.
_BOT_AUTHOR_LOGIN = "github-actions"


def fetch_pr_metadata(pr: int, repo: str) -> dict:
    """Call ``gh pr view`` and return the parsed JSON.

    Returns a dict with at least::

        {
            "comments": [...],
            "commits":  [...],   # list, last item is head
            "updatedAt": "...",
            "headRefOid": "...",
        }
    """
    rc, out, err = _capture(
        [
            "gh", "pr", "view", str(pr),
            "--repo", repo,
            "--json", "comments,commits,updatedAt,headRefOid",
        ],
        timeout=30,
    )
    if rc != 0:
        raise RuntimeError(f"`gh pr view {pr}` failed: {err.strip()}")
    return json.loads(out)


def _pick_by_arch(matches: list[str], vm_arch: str = "x64") -> str:
    """Return the URL in ``matches`` that best matches ``vm_arch``.

    Scoring:
    - 100 if the URL contains ``-{vm_arch}.`` or ``_{vm_arch}.`` (exact arch token)
    - 0 if the URL contains the *other* arch (cross-arch mismatch)
    - 50 otherwise (neutral / arch not encoded in filename)

    Avoids ``sorted()[0]`` falling back to alphabetical order, where
    ``...-arm64.exe`` would beat ``...-x64.exe`` on Windows-x64 VMs.
    """
    if not matches:
        return ""

    other_arch = "arm64" if vm_arch == "x64" else "x64"

    def score(url: str) -> int:
        if f"-{vm_arch}." in url or f"_{vm_arch}." in url:
            return 100
        if other_arch in url:
            return 0
        return 50

    # Stable sort: preserves input order among equal scores so the first
    # match in the comment body wins on ties.
    return sorted(matches, key=score, reverse=True)[0]


def pick_artifact_url(
    pr_data: dict,
    extension: str = ".deb",
    vm_arch: str = "x64",
) -> Optional[str]:
    """Scan PR comments and return the URL from the most recent github-actions
    comment that contains an S3 URL ending in ``extension``.

    Returns ``None`` when no matching comment is found.

    Supports ``extension`` values registered in ``_ARTIFACT_PATTERNS``
    (currently ``.deb`` and ``.exe``).  Unknown extensions return ``None``.

    When a single comment contains multiple matching URLs (e.g. both
    ``-x64.exe`` and ``-arm64.exe``), ``vm_arch`` selects the right one.
    Defaults to ``"x64"`` — alphabetical ``matches[0]`` would otherwise
    return arm64 on Windows-x64 deploys.
    """
    pattern = _ARTIFACT_PATTERNS.get(extension)
    if pattern is None:
        return None

    best_url: Optional[str] = None
    best_dt: Optional[datetime] = None

    for comment in pr_data.get("comments", []):
        # gh CLI returns author as {"login": "..."} for regular users and
        # {"login": "github-actions[bot]"} or {"login": "github-actions"}
        # for the Actions bot.  Accept either form.
        author = comment.get("author", {})
        login: str = author.get("login", "")
        if _BOT_AUTHOR_LOGIN not in login:
            continue

        body: str = comment.get("body", "")
        matches = pattern.findall(body)
        if not matches:
            continue

        created_at: str = comment.get("createdAt", "")
        if not created_at:
            continue

        try:
            dt = datetime.fromisoformat(
                created_at.rstrip("Z").replace("Z", "+00:00")
            )
        except ValueError:
            continue

        if best_dt is None or dt > best_dt:
            best_url = _pick_by_arch(matches, vm_arch)
            best_dt = dt

    return best_url


def _artifact_cache_dir(pr: int) -> Path:
    return Path.home() / ".cache" / "mosdat" / f"pr{pr}"


def _sha_sidecar(artifact_path: Path) -> Path:
    return artifact_path.with_suffix(artifact_path.suffix + ".sha")


def _gate_check_build_artifacts_label(pr: int, repo: str) -> bool:
    """Gate 1: Check if the PR has the 'build-artifacts' label.

    Returns True if the label is present, False otherwise (logs and falls back).
    """
    try:
        labels_data = _gh_pr_view_labels(pr, repo)
        labels = labels_data.get("labels", [])
        for label in labels:
            if label.get("name") == "build-artifacts":
                return True
        # Label not found.
        _log(f"[artifact-first] PR #{pr} missing 'build-artifacts' label — falling back to local clone+build. Add the label to skip yarn build on future runs.")
        return False
    except Exception as e:
        _log(f"[artifact-first] could not check labels ({e}); falling back")
        return False


def _gate_check_ci_build_success(pr: int, repo: str, head_sha: str, head_ref: str) -> bool:
    """Gate 2: Check if the latest CI build run succeeded for the current HEAD.

    Returns True if the most recent build run's headSha matches PR head SHA and
    conclusion is "success". Logs and returns False otherwise.
    """
    try:
        runs = _gh_run_list_builds(repo, head_ref)

        # Filter to runs whose name contains "build" (case-insensitive).
        build_runs = [r for r in runs if "build" in r.get("name", "").lower()]
        if not build_runs:
            _log(f"[artifact-first] no CI build runs found for branch {head_ref} — falling back")
            return False

        # Take the most recent one (first in the list, as gh returns newest first).
        latest_run = build_runs[0]
        run_sha = latest_run.get("headSha", "")
        conclusion = latest_run.get("conclusion", "")

        if run_sha != head_sha:
            _log(
                f"[artifact-first] CI build hasn't run for current HEAD {head_sha[:7]} yet "
                f"(last run was {run_sha[:7]}) — falling back."
            )
            return False

        if conclusion != "success":
            _log(f"[artifact-first] latest CI build failed (conclusion={conclusion}) — falling back.")
            return False

        return True
    except Exception as e:
        _log(f"[artifact-first] could not check CI runs ({e}); falling back")
        return False


def resolve_artifact(
    pr: int,
    repo: str,
    target_ext: str,
    *,
    dry_run: bool = False,
) -> Optional[Path]:
    """Try to fetch a prebuilt artifact from the PR's S3 comment URLs.

    Returns the local ``Path`` to the downloaded file, or ``None`` on any
    failure (no comment, stale, download failure, verify failure).  Callers
    must fall back to clone+build when ``None`` is returned.
    """
    # Gate 1: Check 'build-artifacts' label.
    if not _gate_check_build_artifacts_label(pr, repo):
        return None

    # 1. Fetch PR metadata.
    try:
        pr_data = fetch_pr_metadata(pr, repo)
    except Exception as e:
        _log(f"[artifact-first] gh pr view failed ({e}); falling back to local build")
        return None

    # 2. Resolve PR head SHA + committed date.
    commits = pr_data.get("commits", [])
    if not commits:
        _log("[artifact-first] no commits in PR metadata; falling back")
        return None
    head_commit = commits[-1]
    head_sha: str = head_commit.get("oid", "")
    head_date: str = head_commit.get("committedDate", "")
    if not head_sha or not head_date:
        _log("[artifact-first] missing oid/committedDate in commits; falling back")
        return None

    # Gate 2: Check if latest CI build succeeded for current HEAD.
    # We need the head ref name, so fetch it.
    try:
        head_ref = gh_pr_head_ref(pr, repo)
    except Exception as e:
        _log(f"[artifact-first] could not get PR head ref ({e}); falling back")
        return None

    if not _gate_check_ci_build_success(pr, repo, head_sha, head_ref):
        return None

    # Gates 1+2 passed — log success before proceeding.
    _log(f"[artifact-first] Gates 1+2 passed (label present, CI success for HEAD {head_sha[:7]}) — using artifact URL")

    # 3. Check cache — skip download if same SHA already present.
    cache_dir = _artifact_cache_dir(pr)
    # Look for any .deb in the cache dir with a matching .sha sidecar.
    if cache_dir.is_dir():
        for existing in cache_dir.glob(f"*{target_ext}"):
            sidecar = _sha_sidecar(existing)
            if sidecar.exists() and sidecar.read_text().strip() == head_sha:
                reason = validate_artifact(existing, _EXT_TO_TARGET.get(target_ext, ""))
                if reason:
                    _log(
                        f"[artifact-first] cached file is unusable ({reason}); "
                        "deleting it and downloading again"
                    )
                    existing.unlink(missing_ok=True)
                    sidecar.unlink(missing_ok=True)
                    continue
                _log(f"[artifact-first] cache hit — {existing} (SHA {head_sha[:12]})")
                return existing

    # 4. Scan comments for the S3 URL.
    url = pick_artifact_url(pr_data, target_ext)
    if url is None:
        _log("[artifact-first] no matching S3 URL found in PR comments; falling back")
        return None

    # 5. Dry-run — just report what would happen.
    filename = url.split("/")[-1]
    dest = cache_dir / filename
    if dry_run:
        _log(f"[dry-run] would fetch artifact: {url}")
        _log(f"[dry-run] would save to: {dest}")
        return dest  # Return a path so deploy dry-run can proceed.

    # 6. Download.
    cache_dir.mkdir(parents=True, exist_ok=True)
    _log(f"[artifact-first] downloading {url}")
    if shutil.which("aria2c"):
        dl_cmd = ["aria2c", "-x4", "-s4", "-d", str(cache_dir), "-o", filename, url]
    else:
        dl_cmd = ["curl", "-L", "--fail", "-o", str(dest), url]
    rc = _run(dl_cmd, timeout=300)
    if rc != 0 or not dest.exists() or dest.stat().st_size == 0:
        _log(f"[artifact-first] download failed (rc={rc}); falling back")
        return None

    # 7. Verify download integrity.
    if target_ext == ".deb":
        if shutil.which("dpkg-deb"):
            vrc, _, verr = _capture(["dpkg-deb", "-I", str(dest)], timeout=30)
            if vrc != 0:
                _log(f"[artifact-first] dpkg-deb -I failed: {verr.strip()}; falling back")
                dest.unlink(missing_ok=True)
                return None
        else:
            vrc, vout, _ = _capture(["file", str(dest)], timeout=15)
            if vrc != 0 or "Debian binary package" not in vout:
                _log(
                    f"[artifact-first] file check failed (expected 'Debian binary package'): "
                    f"{vout.strip()}; falling back"
                )
                dest.unlink(missing_ok=True)
                return None
    elif target_ext == ".exe":
        # NSIS installers are PE32 binaries. ``file`` is enough — we have no
        # cross-platform PE introspector and don't need one to detect a truncated
        # download. A minimum size threshold catches partial transfers that still
        # parse as PE; electron-builder NSIS output is >40 MiB.
        vrc, vout, _ = _capture(["file", str(dest)], timeout=15)
        if vrc != 0 or ("PE32" not in vout and "MS-DOS" not in vout):
            _log(
                f"[artifact-first] file check failed (expected PE32 binary): "
                f"{vout.strip()}; falling back"
            )
            dest.unlink(missing_ok=True)
            return None
        if dest.stat().st_size < 1_000_000:
            _log(
                f"[artifact-first] .exe suspiciously small ({dest.stat().st_size} bytes); "
                f"falling back"
            )
            dest.unlink(missing_ok=True)
            return None

    # 7b. Same size + magic-byte gate the deploy path uses, so a truncated or
    # HTML-error download is never cached.
    _target_by_ext = {".deb": "deb", ".rpm": "rpm", ".AppImage": "appimage", ".exe": "exe"}
    bad = validate_artifact(dest, _target_by_ext.get(target_ext, ""))
    if bad:
        _log(f"[artifact-first] {bad}; falling back")
        dest.unlink(missing_ok=True)
        return None

    # 8. Write SHA sidecar so next run hits the cache.
    _sha_sidecar(dest).write_text(head_sha)
    _log(f"[artifact-first] ready — {dest} ({dest.stat().st_size // 1024} KiB)")
    return dest


# ---------------------------------------------------------------------------
# Phase 1: clone / update the PR head
# ---------------------------------------------------------------------------

def clone_or_update(pr: int, repo: str, clone_dir: Path, *, dry_run: bool) -> int:
    """Bring ``clone_dir`` to the latest PR head. Returns 0 on success."""
    if clone_dir.exists():
        _log(f"clone exists: {clone_dir} — fetching PR head")
        if dry_run:
            return 0
        rc = _run(
            ["git", "-C", str(clone_dir), "fetch", "origin",
             f"pull/{pr}/head", "--force"],
            timeout=180,
        )
        if rc != 0:
            return rc
        rc = _run(
            ["git", "-C", str(clone_dir), "checkout", "-B",
             f"pr-{pr}-latest", "FETCH_HEAD"],
            timeout=30,
        )
        if rc != 0:
            return rc
    else:
        _log(f"cloning {repo} → {clone_dir}")
        if dry_run:
            return 0
        head_ref = gh_pr_head_ref(pr, repo)
        _log(f"PR #{pr} head branch: {head_ref}")
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        rc = _run(
            ["gh", "repo", "clone", repo, str(clone_dir), "--", "--branch", head_ref],
            timeout=300,
        )
        if rc != 0:
            return rc

    rc, out, _ = _capture(
        ["git", "-C", str(clone_dir), "log", "-1", "--format=%h %s"],
        timeout=10,
    )
    if rc == 0:
        _log(f"HEAD: {out.strip()}")
    return 0


# ---------------------------------------------------------------------------
# Phase 2: build
# ---------------------------------------------------------------------------

def build(clone_dir: Path, target: BuildTarget, pr: int, *, dry_run: bool) -> tuple[int, Path]:
    """Run install, app build, then release packaging. Returns (rc, log_path)."""
    log_path = Path(f"/tmp/mosdat-build-{pr}.log")
    if not dry_run:
        log_path.write_bytes(b"")  # truncate

    pkg_json = clone_dir / "package.json"
    has_yarn = (clone_dir / "yarn.lock").exists() or (
        pkg_json.exists() and "yarn" in pkg_json.read_text()
    )
    install_cmd = (
        ["yarn", "install", "--frozen-lockfile"] if has_yarn
        else ["npm", "ci"]
    )
    build_cmd = (
        ["yarn", "build"] if has_yarn
        else ["npm", "run", "build"]
    )
    # Package locally only. The project `release` script publishes on tag/draft
    # and requires GH_TOKEN; mOSdat build just needs a local installer artifact.
    release_cmd = (
        ["yarn", "electron-builder", "--publish", "never", "--x64", *target.yarn_release_args]
        if has_yarn
        else ["npx", "electron-builder", "--publish", "never", "--x64", *target.yarn_release_args]
    )

    _log(f"build log: {log_path}")
    if dry_run:
        _log(f"[dry-run] would run: {' '.join(install_cmd)}")
        _log(f"[dry-run] would run: {' '.join(build_cmd)}")
        _log(f"[dry-run] would run: {' '.join(release_cmd)}")
        return 0, log_path

    rc = _run(install_cmd, cwd=clone_dir, timeout=1800, log_file=log_path)
    if rc != 0:
        _log(f"install failed (rc={rc})")
        _tail_log(log_path)
        return rc, log_path

    # Rocket.Chat.Electron's own CI (build-release.yml, validate-pr.yml,
    # pull-request-build.yml) always runs this build step with
    # NODE_ENV=production — without it rollup inlines NODE_ENV=development,
    # producing a dev-mode artifact (wrong userData dir, dev-only code paths,
    # missing devDependencies like chokidar referenced by dev-reload watchers).
    build_env = {**os.environ, "NODE_ENV": "production"}
    rc = _run(build_cmd, cwd=clone_dir, timeout=1800, log_file=log_path, env=build_env)
    if rc != 0:
        _log(f"app build failed (rc={rc})")
        _tail_log(log_path)
        return rc, log_path

    rc = _run(release_cmd, cwd=clone_dir, timeout=1800, log_file=log_path)
    _tail_log(log_path)
    return rc, log_path


# ---------------------------------------------------------------------------
# Phase 3: deploy + verify per VM
# ---------------------------------------------------------------------------

def _find_installed_asar(ssh) -> Optional[str]:
    """Locate the deployed app.asar on a VM.

    Native packages place it under ``/opt/<appname>/resources``. AppImage
    installs keep one executable at ``/opt/Rocket.Chat/rocketchat-desktop``;
    extract just ``resources/app.asar`` into a stable temp dir for symbol
    verification.
    """
    res = ssh.run(
        "ls /opt/*/resources/app.asar 2>/dev/null | head -1",
        timeout=15,
    )
    if res.success and res.stdout.strip():
        return res.stdout.strip()
    res = ssh.run(
        "test -x /opt/Rocket.Chat/rocketchat-desktop && "
        "rm -rf /tmp/mosdat-appimage-asar && "
        "mkdir -p /tmp/mosdat-appimage-asar && "
        "cd /tmp/mosdat-appimage-asar && "
        "/opt/Rocket.Chat/rocketchat-desktop "
        "--appimage-extract resources/app.asar >/dev/null 2>&1 && "
        "test -f squashfs-root/resources/app.asar && "
        "printf '%s\n' /tmp/mosdat-appimage-asar/squashfs-root/resources/app.asar",
        timeout=120,
    )
    if res.success and res.stdout.strip():
        return res.stdout.strip()
    return None


def _verify_symbols_on_vm(ssh, asar: str, symbols: list[str]) -> list[str]:
    """Return the list of symbols MISSING from the installed asar (count == 0)."""
    missing: list[str] = []
    for sym in symbols:
        # grep -a works on app.asar without requiring binutils/strings on every VM.
        cmd = f"grep -a -c {shlex.quote(sym)} {shlex.quote(asar)} 2>/dev/null || true"
        res = ssh.run(cmd, timeout=120)
        count = 0
        try:
            count = int((res.stdout or "0").strip().splitlines()[-1])
        except (ValueError, IndexError):
            count = 0
        if count <= 0:
            missing.append(sym)
        else:
            _log(f"  verify-symbol {sym}: {count} occurrence(s)")
    return missing


def _check_installed_package_version(ssh, target_name: str, remote_pkg: str) -> tuple[str, str]:
    """Compare the package db on the VM with the metadata of ``remote_pkg``.

    Returns ``(error, installed_version)``. ``error`` is empty when the
    installed version equals the artifact version. Targets without a package
    db (AppImage) return ``("", "")``.
    """
    q_pkg = shlex.quote(remote_pkg)
    if target_name == "deb":
        meta = ssh.run(f"dpkg-deb -f {q_pkg} Package Version", timeout=60)
        fields: dict[str, str] = {}
        for line in (meta.stdout or "").splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fields[key.strip()] = value.strip()
        name, want = fields.get("Package", ""), fields.get("Version", "")
        if not meta.success or not name or not want:
            return (
                f"could not read Package/Version from {remote_pkg} with dpkg-deb "
                f"(rc={meta.returncode}): {(meta.stderr or meta.stdout).strip()[:200]}"
            ), ""
        db = ssh.run(
            f"dpkg-query -W -f='${{Status}}|${{Version}}' {shlex.quote(name)}",
            timeout=30,
        )
        status, _, have = (db.stdout or "").strip().partition("|") if db.success else ("", "", "")
        if status != "install ok installed":
            have_shown = f"{have or '(none)'} [dpkg status: {status or 'not installed'}]"
        else:
            have_shown = have
        ok = status == "install ok installed" and have == want
    elif target_name == "rpm":
        meta = ssh.run(
            f"rpm -qp --qf '%{{NAME}} %{{VERSION}}-%{{RELEASE}}' {q_pkg}",
            timeout=60,
        )
        parts = (meta.stdout or "").split()
        if not meta.success or len(parts) != 2:
            return (
                f"could not read NAME/VERSION from {remote_pkg} with rpm -qp "
                f"(rc={meta.returncode}): {(meta.stderr or meta.stdout).strip()[:200]}"
            ), ""
        name, want = parts
        db = ssh.run(
            f"rpm -q --qf '%{{VERSION}}-%{{RELEASE}}\\n' {shlex.quote(name)}",
            timeout=30,
        )
        installed = [ln.strip() for ln in (db.stdout or "").splitlines() if ln.strip()] if db.success else []
        have_shown = ", ".join(installed) or "(none)"
        have = have_shown
        ok = want in installed
    else:
        return "", ""

    if not ok:
        return (
            f"installed {name} version {have_shown} != artifact version {want} — "
            "install did not take effect"
        ), have
    _log(f"  package db: {name} {have} (matches artifact)")
    return "", have


# Candidate install paths for the deployed Rocket.Chat Windows app, in priority
# order. electron-builder NSIS defaults to perUser install under
# %LOCALAPPDATA%\Programs, but some images install perMachine into Program Files.
_WINDOWS_RC_CANDIDATES = (
    r"$env:LOCALAPPDATA\Programs\rocketchat-desktop\Rocket.Chat.exe",
    r"$env:LOCALAPPDATA\Programs\Rocket.Chat\Rocket.Chat.exe",
    r"$env:ProgramFiles\Rocket.Chat\Rocket.Chat.exe",
    r"${env:ProgramFiles(x86)}\Rocket.Chat\Rocket.Chat.exe",
)


def _ps_quote(s: str) -> str:
    """Single-quote a string for embedding into a PowerShell -Command argument.
    PowerShell single-quoted literals only need single-quote doubling.
    """
    return "'" + s.replace("'", "''") + "'"


def _ps_b64(script: str) -> str:
    """Base64-encode a PowerShell script for ``powershell -EncodedCommand``.

    PowerShell expects UTF-16LE bytes.  Encoding the whole script avoids the
    quoting nightmare that comes with passing nested PowerShell through SSH +
    sh -c.
    """
    import base64
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _ps_run(ssh, script: str, *, timeout: int):
    """Run ``script`` on ``ssh`` via ``powershell -EncodedCommand``."""
    return ssh.run(
        f"powershell -NoProfile -EncodedCommand {_ps_b64(script)}",
        timeout=timeout,
    )


def _ps_require_last_exit(label: str) -> str:
    """Fail the PowerShell script when the last native command's exit code is non-zero.

    ``$ErrorActionPreference = 'Stop'`` does NOT stop on a non-PowerShell
    process's non-zero exit; always check ``$LASTEXITCODE`` after yarn/git/node.
    """
    return (
        "if ($LASTEXITCODE -ne 0) { "
        f"Write-Error \"{label} failed: $LASTEXITCODE\"; "
        "exit $LASTEXITCODE "
        "}"
    )


def _ps_tag(stdout: str, name: str) -> str:
    """Extract the value of a ``NAME=value`` line from PowerShell stdout."""
    prefix = name + "="
    for line in (stdout or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.split("=", 1)[1].strip()
    return ""


_WINDOWS_BUILDS_ROOT = r"C:\mosdat-builds"


def _windows_build_dir(pr: int) -> str:
    return rf"{_WINDOWS_BUILDS_ROOT}\pr{pr}"


def _windows_local_build_dir(source_dir: Path) -> str:
    """Workspace for a ``--source`` build: ``C:\\mosdat-builds\\local-<name>``."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(source_dir).resolve().name).strip("-.")
    return rf"{_WINDOWS_BUILDS_ROOT}\local-{(name or 'src').lower()}"


def _windows_locate_script(workspace: str) -> str:
    """PowerShell that prints ``ARTIFACT=<newest *-win-x64.exe in <workspace>\\dist>``."""
    return (
        f"$dist = Join-Path {_ps_quote(workspace)} 'dist';"
        "$hit = Get-ChildItem -Path $dist -Filter '*-win-x64.exe' "
        "-ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | "
        "Select-Object -First 1;"
        "if (-not $hit) { Write-Error 'no *-win-x64.exe in dist'; exit 1 };"
        "Write-Host \"ARTIFACT=$($hit.FullName)\""
    )


# Free space on C: below which old build workspaces are removed, and below
# which (after the cleanup) a native build is refused. A full C: broke the
# electron-builder packaging step on windows10.
_GIB = 1024 ** 3
_WINDOWS_DISK_CLEANUP_BELOW = 15 * _GIB
_WINDOWS_DISK_FAIL_BELOW = 8 * _GIB
_WINDOWS_BUILDS_KEEP = 2


def _windows_free_bytes(ssh) -> Optional[int]:
    """Free bytes on C: of a Windows VM, or ``None`` if the probe failed."""
    probe = _ps_run(
        ssh, "$drive = Get-PSDrive C; Write-Host \"FREE_BYTES=$($drive.Free)\"",
        timeout=60,
    )
    try:
        return int(_ps_tag(probe.stdout, "FREE_BYTES")) if probe.success else None
    except ValueError:
        return None


def _windows_disk_guard(ssh, vm_name: str, keep_workspace: str) -> str:
    """Make room on C: before a native build. Returns an error, or ``""``.

    Below 15 GB free: delete every ``C:\\mosdat-builds\\*`` folder except the
    2 newest and ``keep_workspace``. Below 8 GB after that: refuse the build.
    An unreadable free-space probe is logged and does not block the build.
    """
    free = _windows_free_bytes(ssh)
    if free is None:
        _log(f"{vm_name}: WARN: could not read free space on C: — skipping disk check")
        return ""
    _log(f"{vm_name}: C: free = {free / _GIB:.1f} GB")
    if free >= _WINDOWS_DISK_CLEANUP_BELOW:
        return ""

    _log(
        f"{vm_name}: C: below {_WINDOWS_DISK_CLEANUP_BELOW // _GIB} GB — removing old "
        f"{_WINDOWS_BUILDS_ROOT}\\* folders (keep newest {_WINDOWS_BUILDS_KEEP} + {keep_workspace})"
    )
    cleanup_script = (
        f"$root = {_ps_quote(_WINDOWS_BUILDS_ROOT)};"
        f"$keep = {_ps_quote(keep_workspace)};"
        "if (Test-Path -LiteralPath $root) {"
        " $dirs = @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue "
        "| Where-Object { $_.FullName -ne $keep } | Sort-Object LastWriteTime -Descending);"
        f" foreach ($d in ($dirs | Select-Object -Skip {_WINDOWS_BUILDS_KEEP})) {{"
        # rmdir /s /q copes with deep node_modules paths better than Remove-Item.
        "  cmd.exe /c rmdir /s /q \"$($d.FullName)\";"
        "  if (Test-Path -LiteralPath $d.FullName) { Write-Host \"KEPT=$($d.FullName)\" }"
        "  else { Write-Host \"REMOVED=$($d.FullName)\" }"
        " }"
        "};"
        "$drive = Get-PSDrive C; Write-Host \"FREE_BYTES=$($drive.Free)\""
    )
    cleanup = _ps_run(ssh, cleanup_script, timeout=900)
    out = cleanup.stdout or ""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("REMOVED="):
            _log(f"{vm_name}: removed {line.split('=', 1)[1]}")
        elif line.startswith("KEPT="):
            _log(f"{vm_name}: WARN: could not fully remove {line.split('=', 1)[1]}")
    try:
        after = int(_ps_tag(out, "FREE_BYTES"))
    except ValueError:
        after = None
    if after is None:
        after = _windows_free_bytes(ssh)
    if after is None:
        _log(f"{vm_name}: WARN: could not read free space on C: after cleanup")
        return ""
    _log(
        f"{vm_name}: C: free {free / _GIB:.1f} GB → {after / _GIB:.1f} GB "
        f"(freed {max(after - free, 0) / _GIB:.1f} GB)"
    )
    if after < _WINDOWS_DISK_FAIL_BELOW:
        return (
            f"{vm_name}: only {after / _GIB:.1f} GB free on C: after removing old "
            f"{_WINDOWS_BUILDS_ROOT} folders (need >= {_WINDOWS_DISK_FAIL_BELOW // _GIB} GB "
            "for a native build). Free space on the VM C: drive, then retry."
        )
    return ""


# Top-level folders of the source tree that are build output, never input.
_SOURCE_TAR_EXCLUDE_TOP = frozenset({"node_modules", "dist", "app"})


def make_source_tarball(source_dir: Path, dest: Path) -> tuple[int, str]:
    """Write a tar of the files git sees in ``source_dir`` to ``dest``.

    The file list is ``git ls-files -co --exclude-standard`` (tracked plus
    untracked, minus ignored), so uncommitted work is included. Paths under
    ``node_modules`` and the top-level ``dist`` / ``app`` folders are left out.
    Returns ``(file_count, error)``; ``error`` is empty on success.
    """
    source_dir = Path(source_dir)
    rc, out, err = _capture(
        ["git", "-C", str(source_dir), "ls-files", "-co", "--exclude-standard", "-z"],
        timeout=60,
    )
    if rc != 0:
        return 0, f"git ls-files failed in {source_dir}: {(err or out).strip()[:200]}"
    count = 0
    with tarfile.open(dest, "w") as tf:
        for rel in out.split("\0"):
            if not rel:
                continue
            parts = Path(rel).parts
            if parts[0] in _SOURCE_TAR_EXCLUDE_TOP or "node_modules" in parts:
                continue
            path = source_dir / rel
            # Tracked-but-deleted files and submodule folders are listed too.
            if not path.is_file() or path.is_symlink():
                continue
            tf.add(str(path), arcname=rel, recursive=False)
            count += 1
    if count == 0:
        return 0, f"no source files found in {source_dir}"
    return count, ""


def _upload_source_to_windows(ssh, vm_name: str, source_dir: Path, workspace: str) -> str:
    """Tar ``source_dir``, scp it, and extract it into ``workspace`` with tar.exe.

    Everything in ``workspace`` except ``node_modules`` is replaced, so a
    rebuild of the same tree keeps the yarn install cache. Returns an error,
    or ``""``.
    """
    fd, tmp_name = tempfile.mkstemp(prefix="mosdat-src-", suffix=".tar")
    os.close(fd)
    tmp_tar = Path(tmp_name)
    try:
        count, err = make_source_tarball(source_dir, tmp_tar)
        if err:
            return err
        _log(
            f"{vm_name}: source tarball {count} files, "
            f"{tmp_tar.stat().st_size // 1024} KiB from {source_dir}"
        )
        prep = _ps_run(
            ssh,
            f"New-Item -ItemType Directory -Force -Path {_ps_quote(_WINDOWS_BUILDS_ROOT)} "
            "| Out-Null; Write-Host 'PREP_OK'",
            timeout=60,
        )
        if not prep.success:
            return (
                f"could not create {_WINDOWS_BUILDS_ROOT} (rc={prep.returncode}): "
                f"{(prep.stderr or prep.stdout).strip()[:300]}"
            )
        remote_tar = workspace + ".tar"
        _log(f"{vm_name}: scp source tarball → {remote_tar}")
        scp = ssh.scp_to(tmp_tar, remote_tar.replace("\\", "/"))
        if not scp.success:
            return f"scp of source tarball failed: {(scp.stderr or '').strip()[:300]}"
    finally:
        tmp_tar.unlink(missing_ok=True)

    extract_script = (
        "$ErrorActionPreference = 'Stop';"
        f"$dest = {_ps_quote(workspace)};"
        f"$tar = {_ps_quote(remote_tar)};"
        "if (Test-Path -LiteralPath $dest) {"
        " Get-ChildItem -LiteralPath $dest -Force | Where-Object { $_.Name -ne 'node_modules' } "
        "| Remove-Item -Recurse -Force"
        "};"
        "New-Item -ItemType Directory -Path $dest -Force | Out-Null;"
        "tar.exe -xf $tar -C $dest;"
        f"{_ps_require_last_exit('tar.exe -xf')};"
        "Remove-Item -Force -LiteralPath $tar;"
        "Write-Host 'EXTRACT_OK'"
    )
    _log(f"{vm_name}: extract source → {workspace}")
    extract = _ps_run(ssh, extract_script, timeout=600)
    if not extract.success or "EXTRACT_OK" not in (extract.stdout or ""):
        return (
            f"source extract failed (rc={extract.returncode}): "
            f"{(extract.stderr or extract.stdout).strip()[:300]}"
        )
    return ""


def _node_version_tuple(s: str) -> Optional[tuple[int, int, int]]:
    """Parse ``v24.11.1`` / ``24.11`` / ``24`` into a comparable tuple."""
    text = (s or "").strip().lstrip("vV")
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0), int(match.group(3) or 0)


def node_satisfies_engines(installed: str, engines_node: str) -> bool:
    """Return True if ``installed`` satisfies a simple ``>=X.Y.Z`` engines.node range.

    Empty engines or unrecognized range syntax are treated as satisfied so a
    parser gap never blocks a build. A parseable ``>=`` range with an
    unparseable installed version is *not* satisfied (fail closed on the
    thing we actually need to compare).
    """
    engines_node = (engines_node or "").strip()
    if not engines_node:
        return True
    match = re.match(r"^>=\s*(v?\d+(?:\.\d+){0,2})$", engines_node)
    if not match:
        return True
    required = _node_version_tuple(match.group(1))
    have = _node_version_tuple(installed)
    if required is None or have is None:
        return False
    return have >= required


def _windows_silent_install_script(installer_path: str) -> str:
    """Kill Rocket.Chat, best-effort uninstall, then NSIS silent install ``/S``."""
    return (
        "$ErrorActionPreference = 'Stop';"
        # Kill — Stop-Process is best-effort; ignore failures (no process running).
        "Get-Process -Name 'Rocket.Chat','rocketchat-desktop' -ErrorAction SilentlyContinue "
        "| Stop-Process -Force -ErrorAction SilentlyContinue;"
        "Start-Sleep -Milliseconds 500;"
        # Best-effort uninstall via Get-Package (PackageManagement).
        # We don't fail if Get-Package isn't present (older PowerShell) — NSIS
        # will overwrite the install in place.
        "try {"
        " $pkgs = Get-Package -Name 'Rocket.Chat*' -ErrorAction SilentlyContinue;"
        " foreach ($p in $pkgs) { Uninstall-Package -Name $p.Name -Force -ErrorAction SilentlyContinue | Out-Null }"
        "} catch {}"
        # Silent install. /S is the electron-builder NSIS silent flag. /D pins
        # the per-user default folder: without it the installer reuses any
        # InstallLocation an earlier run stored in HKCU (found on windows10:
        # C:\tmp\c87-4183), and scenarios that launch the default path miss it.
        f"$proc = Start-Process -FilePath {_ps_quote(installer_path)} "
        "-ArgumentList \"/S /D=$env:LOCALAPPDATA\\Programs\\Rocket.Chat\" -Wait -PassThru;"
        "if ($proc.ExitCode -ne 0) { Write-Error \"installer exit code $($proc.ExitCode)\"; exit 1 };"
        "Write-Host 'INSTALL_OK'"
    )


def _verify_windows_install(ssh, verify_symbols: list[str]) -> tuple[str, str, list[str]]:
    """Probe well-known install paths, then grep ``resources\\app.asar`` for symbols.

    Returns ``(installed_at, installed_version, missing_symbols)``.
    ``installed_at`` is empty when Rocket.Chat.exe could not be located.
    """
    candidates_ps = ",".join(_ps_quote(c) for c in _WINDOWS_RC_CANDIDATES)
    verify_script = (
        # Where the uninstall registration says the app is comes first: an
        # install can live outside every well-known path (see /D above).
        "$reg = @(Get-ChildItem 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall',"
        "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall' -ErrorAction SilentlyContinue"
        " | ForEach-Object { Get-ItemProperty $_.PSPath }"
        " | Where-Object { $_.DisplayName -like 'Rocket.Chat*' }"
        " | ForEach-Object { if ($_.InstallLocation) { $_.InstallLocation }"
        " elseif ($_.UninstallString -match '\"([^\"]+)\\\\Uninstall') { $Matches[1] } }"
        " | ForEach-Object { Join-Path $_ 'Rocket.Chat.exe' });"
        f"$cands = @($reg) + @({candidates_ps});"
        "foreach ($c in $cands) {"
        " $resolved = $ExecutionContext.InvokeCommand.ExpandString($c);"
        " if (Test-Path $resolved) {"
        "   $v = (Get-Item $resolved).VersionInfo.FileVersion;"
        "   Write-Host \"INSTALLED_AT=$resolved\";"
        "   Write-Host \"VERSION=$v\";"
        "   exit 0"
        " }"
        "}"
        "Write-Error 'NOT_FOUND'; exit 1"
    )
    ver_res = _ps_run(ssh, verify_script, timeout=60)
    if not ver_res.success:
        return "", "", []

    installed_at = ""
    installed_version = ""
    for line in (ver_res.stdout or "").splitlines():
        if line.startswith("INSTALLED_AT="):
            installed_at = line.split("=", 1)[1].strip()
        elif line.startswith("VERSION="):
            installed_version = line.split("=", 1)[1].strip() or "(unknown)"

    missing: list[str] = []
    if verify_symbols and installed_at:
        app_dir_ps = f"Split-Path -Parent {_ps_quote(installed_at)}"
        for sym in verify_symbols:
            sym_script = (
                f"$dir = {app_dir_ps};"
                "$asar = Join-Path $dir 'resources\\app.asar';"
                "if (-not (Test-Path $asar)) { Write-Error 'app.asar missing'; exit 2 };"
                f"$hits = (Select-String -Path $asar -Pattern {_ps_quote(sym)} -SimpleMatch -List "
                "-ErrorAction SilentlyContinue | Measure-Object).Count;"
                "Write-Host \"COUNT=$hits\""
            )
            sres = _ps_run(ssh, sym_script, timeout=120)
            count = 0
            for line in (sres.stdout or "").splitlines():
                if line.startswith("COUNT="):
                    try:
                        count = int(line.split("=", 1)[1].strip())
                    except ValueError:
                        count = 0
            if count <= 0:
                missing.append(sym)
            else:
                _log(f"  verify-symbol {sym}: {count} occurrence(s)")

    return installed_at, installed_version, missing


def _windows_install_and_verify(
    ssh, vm_name: str, installer_path: str, verify_symbols: list[str], res: DeployResult,
) -> DeployResult:
    """Silent-install ``installer_path`` (already on the VM) and verify it into ``res``."""
    _log(f"{vm_name}: install {installer_path} (silent /S, kill+uninstall first)")
    inst = _ps_run(ssh, _windows_silent_install_script(installer_path), timeout=600)
    res.install_ok = inst.success
    if not res.install_ok:
        res.error = (
            f"install failed (rc={inst.returncode}): "
            f"{(inst.stderr or inst.stdout).strip()[:300]}"
        )
        return res

    installed_at, installed_version, missing = _verify_windows_install(ssh, verify_symbols)
    if not installed_at:
        res.error = "could not locate installed Rocket.Chat.exe at any known path"
        return res
    res.installed_version = installed_version
    res.missing_symbols = missing
    _log(f"{vm_name}: installed at = {installed_at}")
    return res


def deploy_to_windows_vm(
    vm_name: str,
    vm_ip: str,
    vm_user: str,
    artifact: Path,
    target: BuildTarget,
    verify_symbols: list[str],
    *,
    dry_run: bool,
) -> DeployResult:
    """SCP + silent install + verify on a Windows VM via OpenSSH + PowerShell.

    Mirrors :func:`deploy_to_vm` but adapted for Windows:
    - artifact is uploaded to ``C:\\tmp\\rc-installer.exe``;
    - any running Rocket.Chat process is killed first (NSIS refuses to write
      over locked files);
    - install runs silently with the electron-builder NSIS ``/S`` flag;
    - install location is detected via ``Test-Path`` across well-known paths;
    - ``verify_symbols`` are looked up by extracting ``resources/app.asar``
      with the bundled ``tar.exe`` and grepping with ``Select-String``. Tar is
      shipped with Windows 10 1803+ and Windows Server 2019+, so no extra
      tooling is required.
    """
    res = DeployResult(vm=vm_name, artifact=str(artifact))
    remote_path = r"C:\tmp\rc-installer.exe"

    if dry_run:
        _log(f"[dry-run] {vm_name}: scp {artifact} → {vm_ip}:C:/tmp/rc-installer.exe")
        _log(f"[dry-run] {vm_name}: kill Rocket.Chat processes + silent install /S")
        _log(f"[dry-run] {vm_name}: verify install at one of {len(_WINDOWS_RC_CANDIDATES)} candidate paths")
        _log(f"[dry-run] {vm_name}: verify symbols → {verify_symbols}")
        res.scp_ok = True
        res.install_ok = True
        res.installed_version = "(dry-run)"
        return res

    reason = validate_artifact(artifact, target.name)
    if reason:
        res.error = f"refusing to deploy: {reason}"
        return res

    from automation.transport.ssh import SSHClient

    ssh = SSHClient(vm_ip, vm_user)

    # SCP — OpenSSH on Windows accepts forward-slash paths just like POSIX.
    _log(f"{vm_name}: scp → {vm_ip}:C:/tmp/rc-installer.exe")
    scp = ssh.scp_to(artifact, "C:/tmp/rc-installer.exe")
    res.scp_ok = scp.success
    if not res.scp_ok:
        res.error = f"scp failed: {scp.stderr.strip()}"
        return res

    # Kill any running Rocket.Chat processes (locked files block NSIS install).
    # Then uninstall existing copies (best-effort), then install.
    _log(f"{vm_name}: install (silent /S, kill+uninstall first)")
    inst = _ps_run(ssh, _windows_silent_install_script(remote_path), timeout=600)
    res.install_ok = inst.success
    if not res.install_ok:
        res.error = f"install failed (rc={inst.returncode}): {(inst.stderr or inst.stdout).strip()[:300]}"
        return res

    installed_at, installed_version, missing = _verify_windows_install(ssh, verify_symbols)
    if not installed_at:
        res.error = "could not locate installed Rocket.Chat.exe at any known path"
        return res
    res.installed_version = installed_version
    res.missing_symbols = missing
    _log(f"{vm_name}: installed at = {installed_at}")
    return res


def _windows_yarn_script(workspace: str, command: str, ok_tag: str, fail_label: str) -> str:
    """Run a yarn/native command in ``workspace``, checking ``$LASTEXITCODE``."""
    return (
        "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force;"
        f"Set-Location {_ps_quote(workspace)};"
        f"{command};"
        f"{_ps_require_last_exit(fail_label)};"
        f"Write-Host {_ps_quote(ok_tag)}"
    )


def build_on_windows_vm(
    vm_name: str,
    vm_ip: str,
    vm_user: str,
    pr: int,
    repo: str,
    target: BuildTarget,
    verify_symbols: list[str],
    *,
    dry_run: bool,
    source_dir: Optional[Path] = None,
) -> DeployResult:
    """Clone, yarn-build, package, and silent-install a PR natively on a Windows VM.

    Used when ``mosdat build --target exe`` deploys only to Windows VMs. Host-side
    ``electron-builder --win`` needs Wine (unavailable on modern macOS); building
    on the VM avoids that. The artifact never leaves the VM, so ``scp_ok`` is
    marked True once packaging succeeds (nothing was SCP'd).

    With ``source_dir``, the local tree (``git ls-files -co --exclude-standard``)
    is tarred, copied, and extracted into ``C:\\mosdat-builds\\local-<name>``
    instead of a git clone of the PR; ``pr`` is then ignored (may be 0).
    """
    res = DeployResult(vm=vm_name)
    if source_dir is not None:
        source_dir = Path(source_dir).expanduser()
        workspace = _windows_local_build_dir(source_dir)
    else:
        workspace = _windows_build_dir(pr)

    if dry_run:
        if source_dir is not None:
            _log(f"[dry-run] {vm_name}: tar git-visible files of {source_dir} → scp → {workspace}")
        else:
            _log(f"[dry-run] {vm_name}: resolve PR #{pr} head ref via gh")
            _log(f"[dry-run] {vm_name}: clone {repo} → {workspace}")
        _log(
            f"[dry-run] {vm_name}: free space check on C: (cleanup of old "
            f"{_WINDOWS_BUILDS_ROOT} folders below {_WINDOWS_DISK_CLEANUP_BELOW // _GIB} GB)"
        )
        _log(
            f"[dry-run] {vm_name}: corepack + yarn install + yarn build + "
            "electron-builder --publish never --win --x64"
        )
        _log(f"[dry-run] {vm_name}: silent install /S + verify symbols → {verify_symbols}")
        res.scp_ok = True
        res.install_ok = True
        res.installed_version = "(dry-run)"
        return res

    head_ref = ""
    if source_dir is None:
        try:
            head_ref = gh_pr_head_ref(pr, repo)
        except Exception as e:
            res.error = f"could not resolve PR #{pr} head ref: {e}"
            return res
        _log(f"{vm_name}: PR #{pr} head branch: {head_ref}")

    from automation.transport.ssh import SSHClient

    ssh = SSHClient(vm_ip, vm_user)
    _log(f"{vm_name}: native {target.name} build on {vm_ip} ({vm_user})")
    clone_url = f"https://github.com/{repo}.git"

    if source_dir is None:
        git_check_script = (
            "if (Get-Command git -ErrorAction SilentlyContinue) "
            "{ Write-Host 'GIT_OK' } else { Write-Host 'GIT_MISSING' }"
        )
        git_check = _ps_run(ssh, git_check_script, timeout=30)
        if "GIT_OK" not in (git_check.stdout or ""):
            res.error = (
                f"{vm_name}: git is not installed on this VM — cannot clone. "
                "Install git first (see docs/KNOWN_ISSUES.md "
                "'Windows VMs: git can be completely absent, not just stale')."
            )
            _log(f"{vm_name}: ERROR: {res.error}")
            return res

    disk_error = _windows_disk_guard(ssh, vm_name, workspace)
    if disk_error:
        res.error = disk_error
        _log(f"{vm_name}: ERROR: {res.error}")
        return res

    if source_dir is not None:
        upload_error = _upload_source_to_windows(ssh, vm_name, source_dir, workspace)
        if upload_error:
            res.error = upload_error
            return res
    else:
        clone_script = (
            "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force;"
            "$ErrorActionPreference = 'Stop';"
            f"$dest = {_ps_quote(workspace)};"
            "if (Test-Path $dest) { Remove-Item -Recurse -Force $dest };"
            "New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null;"
            "git clone --branch "
            f"{_ps_quote(head_ref)} --single-branch --depth 1 "
            f"{_ps_quote(clone_url)} $dest;"
            f"{_ps_require_last_exit('git clone')};"
            "Write-Host 'CLONE_OK'"
        )
        _log(f"{vm_name}: clone {repo}@{head_ref} → {workspace}")
        clone_res = _ps_run(ssh, clone_script, timeout=300)
        if not clone_res.success:
            res.error = (
                f"clone failed (rc={clone_res.returncode}): "
                f"{(clone_res.stderr or clone_res.stdout).strip()[:300]}"
            )
            return res

    inspect_script = (
        "$ErrorActionPreference = 'Stop';"
        f"Set-Location {_ps_quote(workspace)};"
        "$pkg = Get-Content -Raw 'package.json' | ConvertFrom-Json;"
        "$pm = '';"
        "if ($pkg.PSObject.Properties.Name -contains 'packageManager') { "
        "$pm = [string]$pkg.packageManager };"
        "$eng = '';"
        "if ($pkg.engines -and $pkg.engines.node) { $eng = [string]$pkg.engines.node };"
        "$node = (node --version);"
        "Write-Host \"PACKAGE_MANAGER=$pm\";"
        "Write-Host \"ENGINES_NODE=$eng\";"
        "Write-Host \"NODE_VERSION=$node\""
    )
    inspect = _ps_run(ssh, inspect_script, timeout=60)
    if not inspect.success:
        res.error = (
            f"could not read package.json / node --version "
            f"(rc={inspect.returncode}): {(inspect.stderr or inspect.stdout).strip()[:300]}"
        )
        return res

    package_manager = _ps_tag(inspect.stdout, "PACKAGE_MANAGER")
    engines_node = _ps_tag(inspect.stdout, "ENGINES_NODE")
    node_ver = _ps_tag(inspect.stdout, "NODE_VERSION")
    _log(f"{vm_name}: node {node_ver or '(unknown)'} engines.node={engines_node or '(none)'}")

    if engines_node and not node_satisfies_engines(node_ver, engines_node):
        shown = node_ver or "(unknown)"
        res.error = (
            f"{vm_name}: Node {shown} is older than the required {engines_node} — "
            "upgrade Node on this VM first (see docs/KNOWN_ISSUES.md "
            "'Windows VMs: stale Node.js breaks Electron builds run natively on the VM')."
        )
        _log(f"{vm_name}: ERROR: {res.error}")
        return res

    if package_manager.startswith("yarn@"):
        yarn_pin = package_manager  # e.g. yarn@4.6.0
        corepack_script = (
            "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force;"
            "corepack enable;"
            f"{_ps_require_last_exit('corepack enable')};"
            f"corepack prepare {_ps_quote(yarn_pin)} --activate;"
            f"{_ps_require_last_exit('corepack prepare')};"
            "Write-Host 'COREPACK_OK'"
        )
        _log(f"{vm_name}: corepack prepare {yarn_pin} --activate")
        corepack = _ps_run(ssh, corepack_script, timeout=120)
        if not corepack.success:
            res.error = (
                f"corepack prepare failed (rc={corepack.returncode}): "
                f"{(corepack.stderr or corepack.stdout).strip()[:300]}"
            )
            return res

    yarn_steps = (
        ("yarn install --frozen-lockfile", "YARN_INSTALL_OK", "yarn install", 1800),
        ("$env:NODE_ENV='production'; yarn build", "YARN_BUILD_OK", "yarn build", 1800),
        (
            "yarn electron-builder --publish never --win --x64",
            "ELECTRON_BUILDER_OK",
            "electron-builder",
            1800,
        ),
    )
    for command, ok_tag, fail_label, timeout in yarn_steps:
        _log(f"{vm_name}: {command}")
        step = _ps_run(
            ssh,
            _windows_yarn_script(workspace, command, ok_tag, fail_label),
            timeout=timeout,
        )
        if not step.success:
            res.error = (
                f"{fail_label} failed (rc={step.returncode}): "
                f"{(step.stderr or step.stdout).strip()[:300]}"
            )
            return res

    locate = _ps_run(ssh, _windows_locate_script(workspace), timeout=60)
    artifact_path = _ps_tag(locate.stdout, "ARTIFACT") if locate.success else ""
    if not locate.success or not artifact_path:
        res.error = (
            f"no *-win-x64.exe in {workspace}\\dist "
            f"(rc={locate.returncode}): {(locate.stderr or locate.stdout).strip()[:300]}"
        )
        return res
    _log(f"{vm_name}: artifact {artifact_path}")
    res.artifact = artifact_path
    res.scp_ok = True  # artifact is already on the VM; nothing to SCP

    _log(f"{vm_name}: install (silent /S, kill+uninstall first)")
    inst = _ps_run(ssh, _windows_silent_install_script(artifact_path), timeout=600)
    res.install_ok = inst.success
    if not res.install_ok:
        res.error = (
            f"install failed (rc={inst.returncode}): "
            f"{(inst.stderr or inst.stdout).strip()[:300]}"
        )
        return res

    installed_at, installed_version, missing = _verify_windows_install(ssh, verify_symbols)
    if not installed_at:
        res.error = "could not locate installed Rocket.Chat.exe at any known path"
        return res
    res.installed_version = installed_version
    res.missing_symbols = missing
    _log(f"{vm_name}: installed at = {installed_at}")
    return res


def deploy_to_vm(
    vm_name: str,
    vm_ip: str,
    vm_user: str,
    artifact: Path,
    target: BuildTarget,
    verify_symbols: list[str],
    *,
    dry_run: bool,
) -> DeployResult:
    """SCP + install + verify on a single VM."""
    res = DeployResult(vm=vm_name, artifact=str(artifact))
    remote_path = f"/tmp/{artifact.name}"

    if dry_run:
        _log(f"[dry-run] {vm_name}: scp {artifact} → {vm_ip}:{remote_path}")
        _log(f"[dry-run] {vm_name}: install → {target.install_cmd_template.format(artifact=remote_path)}")
        _log(f"[dry-run] {vm_name}: verify symbols → {verify_symbols}")
        res.scp_ok = True
        res.install_ok = True
        res.installed_version = "(dry-run)"
        return res

    reason = validate_artifact(artifact, target.name)
    if reason:
        res.error = f"refusing to deploy: {reason}"
        return res

    # Lazy import — avoids paying ssh.py import cost on --help and on test runs
    # that monkeypatch transport.
    from automation.transport.ssh import SSHClient

    ssh = SSHClient(vm_ip, vm_user)

    _log(f"{vm_name}: scp → {vm_ip}:{remote_path}")
    scp = ssh.scp_to(artifact, remote_path)
    res.scp_ok = scp.success
    if not res.scp_ok:
        res.error = f"scp failed: {scp.stderr.strip()}"
        return res

    install_cmd = target.install_cmd_template.format(artifact=shlex.quote(remote_path))
    _log(f"{vm_name}: install → {install_cmd}")
    inst = ssh.run(install_cmd, timeout=300)
    res.install_ok = inst.success
    if not res.install_ok:
        res.error = f"install failed (rc={inst.returncode}): {(inst.stderr or inst.stdout).strip()[:200]}"
        return res

    # The deb template ends in `|| apt-get install -y -f`, which exits 0 even
    # when dpkg rejected the file — so compare the package db with the artifact.
    version_error, db_version = _check_installed_package_version(
        ssh, target.name, remote_path,
    )
    if version_error:
        res.install_ok = False
        res.error = version_error
        res.installed_version = db_version
        return res

    asar = _find_installed_asar(ssh)
    if not asar:
        res.error = "could not locate installed app.asar under /opt/*/resources/"
        return res
    _log(f"{vm_name}: installed asar = {asar}")

    if verify_symbols:
        res.missing_symbols = _verify_symbols_on_vm(ssh, asar, verify_symbols)

    # Read the root package.json from the asar header; a grep for the first
    # "version" hits a bundled dependency instead (23.7.20 vs 4.17.0-alpha.1).
    from automation.commands.vm_lifecycle import _asar_package_version

    res.installed_version = _asar_package_version(ssh, asar) or "(unknown)"

    return res


# ---------------------------------------------------------------------------
# Reinstall without a rebuild: an installer already on the VM, or a release
# ---------------------------------------------------------------------------

def find_windows_build_artifact(ssh, pr: int) -> str:
    """Return the VM path of the newest ``*-win-x64.exe`` in
    ``C:\\mosdat-builds\\pr<pr>\\dist``, or ``""`` if there is none.

    ``ssh`` is an ``SSHClient`` (or anything with the same ``run``) for a
    Windows VM.
    """
    locate = _ps_run(ssh, _windows_locate_script(_windows_build_dir(pr)), timeout=60)
    return _ps_tag(locate.stdout, "ARTIFACT") if locate.success else ""


def install_existing_on_windows_vm(
    vm_name: str,
    vm_ip: str,
    vm_user: str,
    artifact_on_vm: str,
    verify_symbols: list[str],
    *,
    dry_run: bool,
) -> DeployResult:
    """Silent-install an installer that is already on a Windows VM, then verify.

    Use it to reinstall a native build output (see
    :func:`find_windows_build_artifact`) or a downloaded release without a
    rebuild. The file must exist on the VM, be at least 1 MiB, and start
    with ``MZ``.
    """
    res = DeployResult(vm=vm_name, artifact=artifact_on_vm)
    if dry_run:
        _log(f"[dry-run] {vm_name}: check {artifact_on_vm} exists on {vm_ip}")
        _log(f"[dry-run] {vm_name}: kill Rocket.Chat processes + silent install /S")
        _log(f"[dry-run] {vm_name}: verify symbols → {verify_symbols}")
        res.scp_ok = True
        res.install_ok = True
        res.installed_version = "(dry-run)"
        return res

    from automation.transport.ssh import SSHClient

    ssh = SSHClient(vm_ip, vm_user)
    check_script = (
        f"$p = {_ps_quote(artifact_on_vm)};"
        "if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { Write-Host 'MISSING'; exit 1 };"
        "$f = Get-Item -LiteralPath $p;"
        "$fs = [System.IO.File]::OpenRead($f.FullName);"
        "try { $b0 = $fs.ReadByte(); $b1 = $fs.ReadByte() } finally { $fs.Close() };"
        "Write-Host \"SIZE=$($f.Length)\";"
        "Write-Host ('MAGIC={0:X2}{1:X2}' -f $b0, $b1)"
    )
    check = _ps_run(ssh, check_script, timeout=60)
    if not check.success or "MISSING" in (check.stdout or ""):
        res.error = (
            f"installer not found on {vm_name}: {artifact_on_vm} "
            f"(rc={check.returncode}) {(check.stderr or '').strip()[:200]}"
        ).rstrip()
        return res
    try:
        size = int(_ps_tag(check.stdout, "SIZE"))
    except ValueError:
        size = -1
    magic = _ps_tag(check.stdout, "MAGIC").upper()
    if size < _ARTIFACT_MIN_BYTES:
        res.error = (
            f"installer {artifact_on_vm} on {vm_name} is {size} bytes "
            f"(< {_ARTIFACT_MIN_BYTES} bytes) — empty or truncated"
        )
        return res
    if magic != _ARTIFACT_MAGIC["exe"].hex().upper():
        res.error = (
            f"installer {artifact_on_vm} on {vm_name} is not a Windows executable "
            f"(leading bytes {magic or '?'}, expected 4D5A 'MZ')"
        )
        return res
    _log(f"{vm_name}: found {artifact_on_vm} ({size // 1024} KiB)")
    res.scp_ok = True  # already on the VM; nothing to SCP
    return _windows_install_and_verify(ssh, vm_name, artifact_on_vm, verify_symbols, res)


_RELEASE_REPO = "RocketChat/Rocket.Chat.Electron"
# Asset names per target, checked with
# `gh release view 4.18.4 -R RocketChat/Rocket.Chat.Electron --json assets`
# (same pattern on the 4.19.0-alpha.2 prerelease). Tags have no "v" prefix.
_RELEASE_ASSETS: dict[str, str] = {
    "exe": "rocketchat-{version}-win-x64.exe",
    "deb": "rocketchat-{version}-linux-amd64.deb",
    "rpm": "rocketchat-{version}-linux-x86_64.rpm",
    "appimage": "rocketchat-{version}-linux-x86_64.AppImage",
}
_WINDOWS_RELEASE_DIR = r"C:\mosdat\tmp"


def release_asset(tag: str, target_name: str) -> tuple[str, str, str]:
    """Return ``(tag, asset_name, download_url)`` for a Rocket.Chat Desktop release.

    A leading ``v`` on ``tag`` is dropped (release tags are bare versions).
    """
    tag = tag.strip()
    if tag[:1] in ("v", "V"):
        tag = tag[1:]
    name = _RELEASE_ASSETS[target_name].format(version=tag)
    url = f"https://github.com/{_RELEASE_REPO}/releases/download/{tag}/{name}"
    return tag, name, url


def install_release_on_vm(
    vm_name: str,
    vm_ip: str,
    vm_user: str,
    os_type: str,
    tag: str,
    verify_symbols: list[str],
    *,
    dry_run: bool,
    linux_target: str = "deb",
) -> DeployResult:
    """Install a published Rocket.Chat Desktop release ``tag`` on a VM.

    Windows: the VM downloads ``rocketchat-<tag>-win-x64.exe`` into
    ``C:\\mosdat\\tmp`` with ``curl.exe`` (in the foreground — a background job
    dies with the SSH session), then :func:`install_existing_on_windows_vm`.
    Linux: the host downloads the ``linux_target`` asset (default ``deb``) into
    ``~/.cache/mosdat/release-<tag>/`` and calls :func:`deploy_to_vm`.
    """
    if os_type == "windows":
        tag, name, url = release_asset(tag, "exe")
        vm_path = rf"{_WINDOWS_RELEASE_DIR}\{name}"
        if not tag:
            return DeployResult(vm=vm_name, error="empty release tag")
        if dry_run:
            _log(f"[dry-run] {vm_name}: curl.exe {url} → {vm_path}")
            return install_existing_on_windows_vm(
                vm_name, vm_ip, vm_user, vm_path, verify_symbols, dry_run=True,
            )

        from automation.transport.ssh import SSHClient

        ssh = SSHClient(vm_ip, vm_user)
        download_script = (
            f"New-Item -ItemType Directory -Force -Path {_ps_quote(_WINDOWS_RELEASE_DIR)} "
            "-ErrorAction Stop | Out-Null;"
            f"curl.exe -fsSL -o {_ps_quote(vm_path)} {_ps_quote(url)};"
            # Do not leave a partial installer behind for a later reinstall.
            "if ($LASTEXITCODE -ne 0) { $rc = $LASTEXITCODE;"
            f" Remove-Item -Force -LiteralPath {_ps_quote(vm_path)} -ErrorAction SilentlyContinue;"
            " Write-Error \"curl.exe download failed: $rc\"; exit $rc };"
            "Write-Host 'DOWNLOAD_OK'"
        )
        _log(f"{vm_name}: download {url} → {vm_path}")
        dl = _ps_run(ssh, download_script, timeout=900)
        if not dl.success or "DOWNLOAD_OK" not in (dl.stdout or ""):
            return DeployResult(
                vm=vm_name,
                artifact=vm_path,
                error=(
                    f"download of release {tag} failed on {vm_name} "
                    f"(rc={dl.returncode}): {(dl.stderr or dl.stdout).strip()[:300]}"
                ),
            )
        return install_existing_on_windows_vm(
            vm_name, vm_ip, vm_user, vm_path, verify_symbols, dry_run=False,
        )

    if linux_target not in _RELEASE_ASSETS or linux_target == "exe":
        return DeployResult(
            vm=vm_name, error=f"unsupported linux_target {linux_target!r} for a Linux VM",
        )
    target = resolve_target(linux_target)
    tag, name, url = release_asset(tag, linux_target)
    if not tag:
        return DeployResult(vm=vm_name, error="empty release tag")
    cache_dir = Path.home() / ".cache" / "mosdat" / f"release-{tag}"
    dest = cache_dir / name
    if dry_run:
        _log(f"[dry-run] {vm_name}: download {url} → {dest}")
    elif not validate_artifact(dest, target.name):
        _log(f"{vm_name}: release {tag} cached at {dest}")
    else:
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest.unlink(missing_ok=True)
        _log(f"{vm_name}: download {url} → {dest}")
        if shutil.which("aria2c"):
            dl_cmd = ["aria2c", "-x4", "-s4", "-d", str(cache_dir), "-o", name, url]
        else:
            dl_cmd = ["curl", "-L", "--fail", "-o", str(dest), url]
        rc = _run(dl_cmd, timeout=900)
        reason = validate_artifact(dest, target.name)
        if rc != 0 or reason:
            dest.unlink(missing_ok=True)
            return DeployResult(
                vm=vm_name,
                artifact=str(dest),
                error=f"download of release {tag} failed (rc={rc}): {reason or url}",
            )
    return deploy_to_vm(
        vm_name, vm_ip, vm_user, dest, target, verify_symbols, dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Top-level run_build entry point
# ---------------------------------------------------------------------------

def run_build(args: argparse.Namespace) -> int:
    raw_pr = getattr(args, "pr", None)
    try:
        pr = int(raw_pr) if raw_pr not in (None, "") else 0
    except (TypeError, ValueError):
        print(f"[mosdat build] ERROR: --pr must be a number, got {raw_pr!r}")
        return 5
    source = getattr(args, "source", None)
    source_dir: Optional[Path] = Path(source).expanduser().resolve() if source else None
    repo = args.repo
    try:
        target = resolve_target(args.target)
    except ValueError as e:
        print(f"[mosdat build] ERROR: {e}")
        return 5

    verify_symbols = parse_verify_symbols(args.verify_symbol or [])
    clone_dir = derive_clone_dir(pr, repo, args.clone_dir)
    deploy_vms = [v.strip() for v in (args.deploy or "").split(",") if v.strip()]
    dry_run = bool(getattr(args, "dry_run", False))

    artifact_first = bool(getattr(args, "artifact_first", True))

    if source_dir is not None:
        _log(f"source: {source_dir} (local tree, no PR clone)")
    else:
        _log(f"PR #{pr} from {repo}")
    _log(f"clone-dir: {clone_dir}")
    _log(f"target: {target.name} (dist glob: {target.dist_glob})")
    _log(f"deploy VMs: {deploy_vms or '(none)'}")
    _log(f"verify symbols: {verify_symbols or '(none)'}")
    _log(f"artifact-first: {'ON' if artifact_first else 'OFF'}")
    if dry_run:
        _log("DRY-RUN — no commands will actually mutate state.")

    # Load VM configs before deciding whether a local build is needed.
    # Tuple is (ip, user, os_type). os_type defaults to "linux".
    vm_lookup: dict[str, tuple[str, str, str]] = {}
    if getattr(args, "config", None):
        try:
            from automation.config import load_config
            cfg = load_config(args.config)
            for vm in cfg.vms:
                vm_lookup[vm.name] = (vm.ip, vm.user, vm.os_type)
        except Exception as e:
            _log(f"WARN: could not load config {args.config}: {e}")

    windows_deploy: list[str] = []
    other_deploy: list[str] = []
    for vm_name in deploy_vms:
        _ip, _user, os_type = vm_lookup.get(vm_name, (vm_name, "root", "linux"))
        if os_type == "windows":
            windows_deploy.append(vm_name)
        else:
            other_deploy.append(vm_name)

    # Pure Windows --target exe: build on the VM (host Wine cannot package
    # NSIS on macOS Catalina+). Mixed Windows+Linux --deploy lists keep the
    # host-side build+SCP path (out of scope).
    build_on_windows = (
        target.name == "exe" and bool(windows_deploy) and not other_deploy
    )

    if source_dir is not None:
        if not build_on_windows:
            print(
                "[mosdat build] ERROR: --source works only with --target exe and a "
                "--deploy list of Windows VMs (the local tree is built on the VM)"
            )
            return 5
        if not source_dir.is_dir():
            print(f"[mosdat build] ERROR: --source {source_dir} is not a directory")
            return 5
    elif pr <= 0:
        print(
            "[mosdat build] ERROR: --pr is required (it may be omitted only with "
            "--source, --target exe, and Windows-only --deploy)"
        )
        return 5

    artifact: Optional[Path] = None

    if build_on_windows:
        _log(
            "Windows-only --target exe: clone+build will run on the deploy VM "
            "(skipping local artifact-first / clone+build)"
        )
    else:
        # Phase 1a: try prebuilt artifact from S3 (if --artifact-first)
        target_ext_map = {
            "deb": ".deb",
            "rpm": ".rpm",
            "appimage": ".AppImage",
            "exe": ".exe",
        }
        target_ext = target_ext_map.get(target.name)
        if artifact_first and target_ext is not None:
            artifact = resolve_artifact(pr, repo, target_ext, dry_run=dry_run)
            if artifact is not None:
                _log(f"[artifact-first] using prebuilt artifact — skipping clone+build")

        # Phase 1b: fall back to clone+build
        if artifact is None:
            if artifact_first and target_ext is not None:
                _log("[artifact-first] falling back to local clone+build")
            rc = clone_or_update(pr, repo, clone_dir, dry_run=dry_run)
            if rc != 0:
                _log(f"clone/fetch failed (rc={rc})")
                return 4

            # Phase 2: build
            rc, log_path = build(clone_dir, target, pr, dry_run=dry_run)
            if rc != 0:
                _log(f"build failed (rc={rc}); see {log_path}")
                return 2

            # Phase 3: locate artifact
            dist_dir = clone_dir / "dist"
            if dry_run:
                artifact = dist_dir / f"placeholder{target.dist_glob.lstrip('*')}"
                _log(f"[dry-run] would locate artifact matching {dist_dir}/{target.dist_glob}")
            else:
                artifact = match_artifact(dist_dir, target.dist_glob)
                if not artifact:
                    _log(f"no artifact matching {dist_dir}/{target.dist_glob}")
                    return 2
                _log(f"artifact: {artifact} ({artifact.stat().st_size // 1024} KiB)")

    # Phase 5: deploy + verify
    results: list[DeployResult] = []
    for vm_name in deploy_vms:
        ip, user, os_type = vm_lookup.get(vm_name, (vm_name, "root", "linux"))
        if vm_name not in vm_lookup and not dry_run:
            _log(f"WARN: VM {vm_name} not in --config; defaulting to host={vm_name} user=root os=linux")
        # Hard guard: Windows VMs must be deployed with --target exe (and vice
        # versa).  Mixing produces install-side failures that are hard to
        # diagnose; fail fast with a clear message instead.
        if os_type == "windows" and target.name != "exe":
            _log(f"{vm_name}: ERROR: Windows VM requires --target exe (got {target.name!r})")
            results.append(DeployResult(
                vm=vm_name,
                error=f"Windows VM requires --target exe, got {target.name!r}",
            ))
            continue
        if os_type != "windows" and target.name == "exe":
            _log(f"{vm_name}: ERROR: --target exe requires a Windows VM (os_type={os_type!r})")
            results.append(DeployResult(
                vm=vm_name,
                error=f"--target exe requires a Windows VM, got os_type={os_type!r}",
            ))
            continue

        _log(f"--- deploy → {vm_name} ({ip}, os={os_type}) ---")
        if os_type == "windows":
            if build_on_windows:
                res = build_on_windows_vm(
                    vm_name, ip, user, pr, repo, target, verify_symbols, dry_run=dry_run,
                    source_dir=source_dir,
                )
            else:
                res = deploy_to_windows_vm(
                    vm_name, ip, user, artifact, target, verify_symbols, dry_run=dry_run,
                )
        else:
            res = deploy_to_vm(
                vm_name, ip, user, artifact, target, verify_symbols, dry_run=dry_run,
            )
        results.append(res)
        if res.installed_version:
            _log(f"{vm_name}: installed version = {res.installed_version}")
        if res.missing_symbols:
            _log(f"{vm_name}: MISSING SYMBOLS: {res.missing_symbols}")
        if res.error:
            _log(f"{vm_name}: ERROR: {res.error}")
        _log(f"{vm_name}: {'OK' if res.ok else 'FAIL'}")

    # Exit-code logic per spec
    if any(r.missing_symbols for r in results):
        return 1
    if any(r.error or not r.scp_ok or not r.install_ok for r in results):
        return 3
    return 0


# ---------------------------------------------------------------------------
# Subparser wiring
# ---------------------------------------------------------------------------

def add_build_subparser(sub) -> None:
    """Wire ``mosdat build`` onto an existing subparsers action."""
    p = sub.add_parser(
        "build",
        help="I3: clone a PR, build the Electron app, deploy + verify on VMs",
        description=(
            "Reproducible PR build+deploy. Clones the PR head (via gh), runs "
            "yarn install + yarn release for the chosen target, then SCPs the "
            "artifact to each --deploy VM, installs it, and greps the deployed "
            "app.asar for --verify-symbol entries. Eliminates the stale-clone "
            "false-negative class of bug."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  mosdat build --pr 3325 --repo RocketChat/Rocket.Chat.Electron \\\n"
            "      --target deb --deploy ubuntu2204,ubuntu2404 \\\n"
            "      --verify-symbol isTelephonyEnabled \\\n"
            "      --verify-symbol telephonyGlobalShortcutConfig\n"
        ),
    )
    p.add_argument(
        "--pr", default=None,
        help="Pull request number (required unless --source is used)",
    )
    p.add_argument(
        "--source", default=None, metavar="DIR",
        help=(
            "Build this local tree instead of a PR clone (uncommitted files "
            "included; git ls-files -co --exclude-standard). Only with --target "
            "exe and Windows-only --deploy: the tree is copied to the VM and "
            r"built in C:\mosdat-builds\local-<dir name>."
        ),
    )
    p.add_argument(
        "--repo", default="RocketChat/Rocket.Chat.Electron",
        help="<owner>/<repo> (default: RocketChat/Rocket.Chat.Electron)",
    )
    p.add_argument(
        "--target", default="deb", choices=sorted(TARGETS.keys()),
        help="Build target (deb/rpm/appimage for Linux; exe for Windows NSIS)",
    )
    p.add_argument(
        "--clone-dir", default=None, dest="clone_dir",
        help="Override the clone path (default: ~/projects/<repo-name>-pr<N>)",
    )
    p.add_argument(
        "--deploy", default="",
        help="Comma-separated VM names to deploy to (resolved via --config)",
    )
    p.add_argument(
        "--verify-symbol", action="append", default=[], dest="verify_symbol",
        metavar="SYMBOL",
        help=(
            "Symbol name expected in the deployed app.asar (may be repeated; "
            "comma-separated values also accepted). Missing symbol → exit 1."
        ),
    )
    p.add_argument(
        "--config", default=None,
        help="mosdat TOML config (used to resolve VM IPs from --deploy names)",
    )
    p.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Print the planned steps without cloning, building, or deploying",
    )
    p.add_argument(
        "--artifact-first", action="store_true", default=True,
        dest="artifact_first",
        help=(
            "Fetch the prebuilt artifact from the PR's S3 comment before falling "
            "back to local clone+build (default: ON). Requires gh and aria2c (or "
            "curl) on PATH."
        ),
    )
    p.add_argument(
        "--no-artifact-first", action="store_false", dest="artifact_first",
        help="Skip the S3 artifact lookup and always clone+build locally.",
    )
