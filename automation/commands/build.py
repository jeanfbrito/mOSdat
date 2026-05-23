"""``mosdat build`` — clone/update a PR, build the Electron app, deploy + verify on VMs.

Implements roadmap item I3 (see docs/IMPROVEMENTS.md).

Design notes:
- Uses the ``gh`` CLI for PR metadata and cloning. We rely on ``gh`` being
  authenticated (the rest of mosdat already does).
- The build is long (5+ minutes for yarn release). We log to a per-PR tempfile
  under ``/tmp/mosdat-build-<pr>.log`` and print only the tail to avoid flooding
  the agent context.
- ``--target deb`` is implemented end-to-end on Linux. ``--target exe`` is
  implemented end-to-end on Windows (Windows VMs reached via OpenSSH; install
  driven by PowerShell + electron-builder NSIS ``/S`` silent flag).
  rpm/AppImage paths remain stubbed with TODO comments so the dispatcher
  table stays cheap to extend.
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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Target plug-in registry — v1 only ships "deb".
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
    # TODO(I3): rpm — yarn release --linux rpm; install via `sudo rpm -i --force`
    # TODO(I3): AppImage — yarn release --linux AppImage; install = chmod +x + place under /opt
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
# Windows installer S3 URL pattern (electron-builder NSIS .exe).
# Example: .../pr-3325/windows-latest/rocketchat-4.14.1-win-x64.exe
_S3_EXE_RE = re.compile(r"https://[^\s)\"']+\.exe")

# Map artifact extension → URL regex. New formats only need to register here.
_ARTIFACT_PATTERNS: dict[str, re.Pattern] = {
    ".deb": _S3_DEB_RE,
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
             f"pull/{pr}/head:pr-{pr}-latest", "--force"],
            timeout=180,
        )
        if rc != 0:
            return rc
        rc = _run(
            ["git", "-C", str(clone_dir), "checkout", f"pr-{pr}-latest"],
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
    """Run ``yarn install && yarn release ...``. Returns (rc, log_path)."""
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
    # `yarn release` already runs `yarn electron-builder --publish onTagOrDraft --x64`.
    # Append the target flags. Same shape works with npm via `npm run release --`.
    release_cmd = (
        ["yarn", "release", *target.yarn_release_args] if has_yarn
        else ["npm", "run", "release", "--", *target.yarn_release_args]
    )

    _log(f"build log: {log_path}")
    if dry_run:
        _log(f"[dry-run] would run: {' '.join(install_cmd)}")
        _log(f"[dry-run] would run: {' '.join(release_cmd)}")
        return 0, log_path

    rc = _run(install_cmd, cwd=clone_dir, timeout=1800, log_file=log_path)
    if rc != 0:
        _log(f"install failed (rc={rc})")
        _tail_log(log_path)
        return rc, log_path

    rc = _run(release_cmd, cwd=clone_dir, timeout=1800, log_file=log_path)
    _tail_log(log_path)
    return rc, log_path


# ---------------------------------------------------------------------------
# Phase 3: deploy + verify per VM
# ---------------------------------------------------------------------------

def _find_installed_asar(ssh) -> Optional[str]:
    """Locate the deployed app.asar on a VM. Pattern: /opt/<appname>/resources/app.asar."""
    res = ssh.run(
        "ls /opt/*/resources/app.asar 2>/dev/null | head -1",
        timeout=15,
    )
    if res.success and res.stdout.strip():
        return res.stdout.strip()
    return None


def _verify_symbols_on_vm(ssh, asar: str, symbols: list[str]) -> list[str]:
    """Return the list of symbols MISSING from the installed asar (count == 0)."""
    missing: list[str] = []
    for sym in symbols:
        # strings is fast; grep -c returns count (0 means missing)
        cmd = f"strings {shlex.quote(asar)} | grep -c {shlex.quote(sym)}"
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


# Candidate install paths for the deployed Rocket.Chat Windows app, in priority
# order. electron-builder NSIS defaults to perUser install under
# %LOCALAPPDATA%\Programs, but some images install perMachine into Program Files.
_WINDOWS_RC_CANDIDATES = (
    r"$env:LOCALAPPDATA\Programs\rocketchat-desktop\Rocket.Chat.exe",
    r"$env:LOCALAPPDATA\Programs\Rocket.Chat\Rocket.Chat.exe",
    r"$env:ProgramFiles\Rocket.Chat\Rocket.Chat.exe",
    r"$env:ProgramFiles(x86)\Rocket.Chat\Rocket.Chat.exe",
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
    res = DeployResult(vm=vm_name)
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
    install_script = (
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
        # Silent install. /S is the electron-builder NSIS silent flag.
        f"$proc = Start-Process -FilePath {_ps_quote(remote_path)} "
        "-ArgumentList '/S' -Wait -PassThru;"
        "if ($proc.ExitCode -ne 0) { Write-Error \"installer exit code $($proc.ExitCode)\"; exit 1 };"
        "Write-Host 'INSTALL_OK'"
    )
    install_cmd = f"powershell -NoProfile -EncodedCommand {_ps_b64(install_script)}"
    _log(f"{vm_name}: install (silent /S, kill+uninstall first)")
    inst = ssh.run(install_cmd, timeout=600)
    res.install_ok = inst.success
    if not res.install_ok:
        res.error = f"install failed (rc={inst.returncode}): {(inst.stderr or inst.stdout).strip()[:300]}"
        return res

    # Verify install — probe candidate paths and read FileVersion.
    candidates_ps = ",".join(_ps_quote(c) for c in _WINDOWS_RC_CANDIDATES)
    verify_script = (
        f"$cands = @({candidates_ps});"
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
    verify_cmd = f"powershell -NoProfile -EncodedCommand {_ps_b64(verify_script)}"
    ver_res = ssh.run(verify_cmd, timeout=60)
    if not ver_res.success:
        res.error = "could not locate installed Rocket.Chat.exe at any known path"
        return res

    installed_at = ""
    for line in (ver_res.stdout or "").splitlines():
        if line.startswith("INSTALLED_AT="):
            installed_at = line.split("=", 1)[1].strip()
        elif line.startswith("VERSION="):
            res.installed_version = line.split("=", 1)[1].strip() or "(unknown)"
    if installed_at:
        _log(f"{vm_name}: installed at = {installed_at}")

    # Verify symbols by grepping the extracted app.asar.  Windows ships ``tar.exe``
    # since 1803; we use it to pull app.asar out of the installed app directory
    # rather than introducing a Node dependency on the VM.  If the asar happens
    # to live next to Rocket.Chat.exe, ``Select-String`` is plenty.
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
            sym_cmd = f"powershell -NoProfile -EncodedCommand {_ps_b64(sym_script)}"
            sres = ssh.run(sym_cmd, timeout=120)
            count = 0
            for line in (sres.stdout or "").splitlines():
                if line.startswith("COUNT="):
                    try:
                        count = int(line.split("=", 1)[1].strip())
                    except ValueError:
                        count = 0
            if count <= 0:
                res.missing_symbols.append(sym)
            else:
                _log(f"  verify-symbol {sym}: {count} occurrence(s)")

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
    res = DeployResult(vm=vm_name)
    remote_path = f"/tmp/{artifact.name}"

    if dry_run:
        _log(f"[dry-run] {vm_name}: scp {artifact} → {vm_ip}:{remote_path}")
        _log(f"[dry-run] {vm_name}: install → {target.install_cmd_template.format(artifact=remote_path)}")
        _log(f"[dry-run] {vm_name}: verify symbols → {verify_symbols}")
        res.scp_ok = True
        res.install_ok = True
        res.installed_version = "(dry-run)"
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

    asar = _find_installed_asar(ssh)
    if not asar:
        res.error = "could not locate installed app.asar under /opt/*/resources/"
        return res
    _log(f"{vm_name}: installed asar = {asar}")

    if verify_symbols:
        res.missing_symbols = _verify_symbols_on_vm(ssh, asar, verify_symbols)

    ver_res = ssh.run(
        f"strings {shlex.quote(asar)} | grep -m1 -oE '\"version\":\"[^\"]+\"' | head -1",
        timeout=60,
    )
    if ver_res.success:
        res.installed_version = ver_res.stdout.strip() or "(unknown)"

    return res


# ---------------------------------------------------------------------------
# Top-level run_build entry point
# ---------------------------------------------------------------------------

def run_build(args: argparse.Namespace) -> int:
    pr = int(args.pr)
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

    _log(f"PR #{pr} from {repo}")
    _log(f"clone-dir: {clone_dir}")
    _log(f"target: {target.name} (dist glob: {target.dist_glob})")
    _log(f"deploy VMs: {deploy_vms or '(none)'}")
    _log(f"verify symbols: {verify_symbols or '(none)'}")
    _log(f"artifact-first: {'ON' if artifact_first else 'OFF'}")
    if dry_run:
        _log("DRY-RUN — no commands will actually mutate state.")

    artifact: Optional[Path] = None

    # Phase 1a: try prebuilt artifact from S3 (if --artifact-first)
    target_ext_map = {"deb": ".deb", "exe": ".exe"}
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

    # Phase 4: load VM IPs from config if provided; else assume <name>=<name>
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
    p.add_argument("--pr", required=True, help="Pull request number")
    p.add_argument(
        "--repo", default="RocketChat/Rocket.Chat.Electron",
        help="<owner>/<repo> (default: RocketChat/Rocket.Chat.Electron)",
    )
    p.add_argument(
        "--target", default="deb", choices=sorted(TARGETS.keys()),
        help="Build target (deb=Linux .deb; exe=Windows NSIS .exe; rpm/AppImage planned)",
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
