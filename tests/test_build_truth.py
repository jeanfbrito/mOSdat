"""Unit tests for build/deploy truth (spec 003, workstream C).

C1 validate_artifact, C2 package-db version check, C3 DeployResult.artifact,
C4 reinstall without rebuild, C5 --source local-tree Windows build,
C6 C: free-space guard. No live VM, no network: SSHClient is replaced with
a scripted fake that records every run/scp call.
"""

from __future__ import annotations


# Mirror test_build_exe.py: clear sibling-test stub pollution before importing
# automation.* so we get the real modules.
import sys as _sys
for _name in list(_sys.modules):
    if (
        _name.startswith("automation.transport")
        or _name.startswith("automation.vlm")
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

import base64
import subprocess
import tarfile
from pathlib import Path

import pytest

from automation.commands import build as build_mod
from automation.commands.build import (
    DeployResult,
    build_on_windows_vm,
    deploy_to_vm,
    deploy_to_windows_vm,
    find_windows_build_artifact,
    install_existing_on_windows_vm,
    install_release_on_vm,
    make_source_tarball,
    release_asset,
    resolve_artifact,
    resolve_target,
    validate_artifact,
)

MIB = 1024 * 1024
GIB = 1024 ** 3


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    from automation.transport.ssh import SSHResult
    return SSHResult(returncode=returncode, stdout=stdout, stderr=stderr)


def _decode(command: str) -> str:
    """Return the PowerShell script for an -EncodedCommand call, else the command."""
    prefix = "powershell -NoProfile -EncodedCommand "
    if command.startswith(prefix):
        return base64.b64decode(command[len(prefix):]).decode("utf-16-le")
    return command


class ScriptedSSH:
    """SSHClient stand-in. ``rules`` is a list of ``(needle, result)``: the
    first rule whose needle is in the (decoded) command answers it. A rule
    whose result is a list pops one item per call. No match → rc 0, no output.
    """

    def __init__(self, rules=None) -> None:
        self.rules = list(rules or [])
        self.commands: list[str] = []  # decoded
        self.scp_calls: list[tuple[Path, str]] = []
        self.scp_result = _result(0)

    def run(self, command: str, timeout=None):
        text = _decode(command)
        self.commands.append(text)
        for needle, res in self.rules:
            if needle in text:
                if isinstance(res, list):
                    return res.pop(0) if len(res) > 1 else res[0]
                return res
        return _result(0)

    def scp_to(self, local_path: Path, remote_path: str):
        self.scp_calls.append((Path(local_path), remote_path))
        return self.scp_result

    def joined(self) -> str:
        return "\n".join(self.commands)


def _patch_ssh(monkeypatch, fake: ScriptedSSH) -> None:
    import automation.transport.ssh as ssh_mod
    monkeypatch.setattr(ssh_mod, "SSHClient", lambda *a, **kw: fake)


def _write(path: Path, head: bytes, size: int = MIB) -> Path:
    path.write_bytes(head + b"\x00" * max(size - len(head), 0))
    return path


_FREE_100 = _result(0, f"FREE_BYTES={100 * GIB}\n")
_VERIFY_PATH_OK = _result(
    0,
    r"INSTALLED_AT=C:\Users\jean\AppData\Local\Programs\rocketchat-desktop\Rocket.Chat.exe"
    "\nVERSION=4.18.4\n",
)


# ---------------------------------------------------------------------------
# C1 — validate_artifact
# ---------------------------------------------------------------------------

def test_validate_artifact_rejects_96_byte_deb(tmp_path) -> None:
    deb = tmp_path / "x.deb"
    deb.write_bytes(b"!<arch>\n" + b"\x00" * 88)
    assert deb.stat().st_size == 96
    reason = validate_artifact(deb, "deb")
    assert "96 bytes" in reason
    assert "empty or truncated" in reason


def test_validate_artifact_missing_file(tmp_path) -> None:
    assert "not found" in validate_artifact(tmp_path / "nope.deb", "deb")


@pytest.mark.parametrize(
    "target,head",
    [
        ("deb", b"!<arch>\n"),
        ("rpm", b"\xed\xab\xee\xdb"),
        ("appimage", b"\x7fELF"),
        ("exe", b"MZ"),
    ],
)
def test_validate_artifact_accepts_real_magic(tmp_path, target, head) -> None:
    assert validate_artifact(_write(tmp_path / f"a.{target}", head), target) == ""


def test_validate_artifact_rejects_wrong_magic(tmp_path) -> None:
    fake = _write(tmp_path / "x.exe", b"<html>")
    reason = validate_artifact(fake, "exe")
    assert "not a exe package" in reason
    assert "b'MZ'" in reason


def test_deploy_to_vm_rejects_96_byte_deb_before_scp(monkeypatch, tmp_path) -> None:
    deb = tmp_path / "x.deb"
    deb.write_bytes(b"\x00" * 96)
    fake = ScriptedSSH()
    _patch_ssh(monkeypatch, fake)

    res = deploy_to_vm(
        "ubuntu2204", "192.168.13.81", "jean", deb, resolve_target("deb"), [],
        dry_run=False,
    )

    assert fake.scp_calls == []
    assert fake.commands == []
    assert res.ok is False
    assert res.scp_ok is False
    assert res.error.startswith("refusing to deploy:")
    assert "96 bytes" in res.error
    assert res.artifact == str(deb)


def test_deploy_to_windows_vm_rejects_non_pe_before_scp(monkeypatch, tmp_path) -> None:
    exe = _write(tmp_path / "x.exe", b"PK\x03\x04")
    fake = ScriptedSSH()
    _patch_ssh(monkeypatch, fake)

    res = deploy_to_windows_vm(
        "windows10", "192.168.13.87", "jean", exe, resolve_target("exe"), [],
        dry_run=False,
    )

    assert fake.scp_calls == []
    assert fake.commands == []
    assert "refusing to deploy" in res.error
    assert res.ok is False


def test_resolve_artifact_drops_bad_cached_file(monkeypatch, tmp_path) -> None:
    pr, head_sha = 3325, "a" * 40
    cache_dir = tmp_path / ".cache" / "mosdat" / f"pr{pr}"
    cache_dir.mkdir(parents=True)
    bad = cache_dir / "rocketchat-4.14.1-linux-amd64.deb"
    bad.write_bytes(b"\x00" * 96)
    sidecar = cache_dir / "rocketchat-4.14.1-linux-amd64.deb.sha"
    sidecar.write_text(head_sha)

    monkeypatch.setattr(build_mod.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(build_mod, "_gh_pr_view_labels",
                        lambda *a: {"labels": [{"name": "build-artifacts"}]})
    monkeypatch.setattr(build_mod, "gh_pr_head_ref", lambda *a: "feature")
    monkeypatch.setattr(build_mod, "_gh_run_list_builds", lambda *a: [
        {"name": "build", "conclusion": "success", "headSha": head_sha},
    ])
    monkeypatch.setattr(build_mod, "fetch_pr_metadata", lambda *a: {
        "commits": [{"oid": head_sha, "committedDate": "2026-10-01T00:00:00Z"}],
        "comments": [],
    })

    # No S3 URL in comments → falls through to "no URL" → None (not the bad file).
    assert resolve_artifact(pr, "RocketChat/Rocket.Chat.Electron", ".deb") is None
    assert not bad.exists()
    assert not sidecar.exists()


# ---------------------------------------------------------------------------
# C2 — package-db version check after install
# ---------------------------------------------------------------------------

def _linux_rules(meta: str, db: str, *, meta_rc: int = 0) -> list:
    return [
        ("dpkg-deb -f", _result(meta_rc, meta, "" if meta_rc == 0 else "not a debian format archive")),
        ("dpkg-query -W", _result(0, db)),
        ("rpm -qp", _result(meta_rc, meta)),
        ("rpm -q --qf", _result(0, db)),
        ("ls /opt/*/resources/app.asar", _result(0, "/opt/Rocket.Chat/resources/app.asar\n")),
        ('"version"', _result(0, '"version":"4.18.4"\n')),
    ]


def test_deploy_to_vm_dpkg_version_mismatch_sets_error(monkeypatch, tmp_path) -> None:
    deb = _write(tmp_path / "rocketchat-4.18.4-linux-amd64.deb", b"!<arch>\n")
    fake = ScriptedSSH(_linux_rules(
        "Package: rocketchat\nVersion: 4.18.4\n", "install ok installed|4.9.1",
    ))
    _patch_ssh(monkeypatch, fake)

    res = deploy_to_vm(
        "ubuntu2204", "192.168.13.81", "jean", deb, resolve_target("deb"), ["sym"],
        dry_run=False,
    )

    assert res.error == (
        "installed rocketchat version 4.9.1 != artifact version 4.18.4 — "
        "install did not take effect"
    )
    assert res.install_ok is False
    assert res.ok is False
    assert res.installed_version == "4.9.1"
    # The old app.asar must not be inspected / verified.
    assert "app.asar" not in fake.joined()
    # Install template is unchanged; version reads use the scp'd path.
    assert "sudo dpkg -i /tmp/rocketchat-4.18.4-linux-amd64.deb || sudo apt-get install -y -f" in fake.joined()
    assert "dpkg-deb -f /tmp/rocketchat-4.18.4-linux-amd64.deb Package Version" in fake.joined()
    assert "dpkg-query -W -f='${Status}|${Version}' rocketchat" in fake.joined()


def test_deploy_to_vm_dpkg_version_match_continues_to_verify(monkeypatch, tmp_path) -> None:
    deb = _write(tmp_path / "rc.deb", b"!<arch>\n")
    fake = ScriptedSSH(_linux_rules(
        "Package: rocketchat\nVersion: 4.18.4\n", "install ok installed|4.18.4",
    ))
    _patch_ssh(monkeypatch, fake)
    # Patch the module object build.py will import: a dotted-string target
    # breaks when another test file leaves a stub ``automation`` package.
    import importlib
    monkeypatch.setattr(
        importlib.import_module("automation.commands.vm_lifecycle"),
        "_asar_package_version", lambda ssh, asar: "4.18.4",
    )

    res = deploy_to_vm(
        "ubuntu2204", "192.168.13.81", "jean", deb, resolve_target("deb"), [],
        dry_run=False,
    )

    assert res.error == ""
    assert res.ok is True
    assert res.installed_version == "4.18.4"
    assert res.artifact == str(deb)


def test_deploy_to_vm_deb_not_installed_state_is_error(monkeypatch, tmp_path) -> None:
    deb = _write(tmp_path / "rc.deb", b"!<arch>\n")
    fake = ScriptedSSH(_linux_rules(
        "Package: rocketchat\nVersion: 4.18.4\n", "deinstall ok config-files|4.18.4",
    ))
    _patch_ssh(monkeypatch, fake)

    res = deploy_to_vm(
        "ubuntu2204", "192.168.13.81", "jean", deb, resolve_target("deb"), [],
        dry_run=False,
    )

    assert "install did not take effect" in res.error
    assert "deinstall ok config-files" in res.error


def test_deploy_to_vm_unreadable_deb_metadata_is_error(monkeypatch, tmp_path) -> None:
    deb = _write(tmp_path / "rc.deb", b"!<arch>\n")
    fake = ScriptedSSH(_linux_rules("", "", meta_rc=2))
    _patch_ssh(monkeypatch, fake)

    res = deploy_to_vm(
        "ubuntu2204", "192.168.13.81", "jean", deb, resolve_target("deb"), [],
        dry_run=False,
    )

    assert "could not read Package/Version" in res.error
    assert res.ok is False


def test_deploy_to_vm_rpm_version_mismatch_sets_error(monkeypatch, tmp_path) -> None:
    rpm = _write(tmp_path / "rc.rpm", b"\xed\xab\xee\xdb")
    fake = ScriptedSSH(_linux_rules("rocketchat 4.18.4-1", "4.9.1-1\n"))
    _patch_ssh(monkeypatch, fake)

    res = deploy_to_vm(
        "fedora", "192.168.13.90", "jean", rpm, resolve_target("rpm"), [],
        dry_run=False,
    )

    assert res.error == (
        "installed rocketchat version 4.9.1-1 != artifact version 4.18.4-1 — "
        "install did not take effect"
    )
    assert "rpm -qp --qf '%{NAME} %{VERSION}-%{RELEASE}' /tmp/rc.rpm" in fake.joined()


def test_deploy_to_vm_rpm_version_match(monkeypatch, tmp_path) -> None:
    rpm = _write(tmp_path / "rc.rpm", b"\xed\xab\xee\xdb")
    fake = ScriptedSSH(_linux_rules("rocketchat 4.18.4-1", "4.18.4-1\n"))
    _patch_ssh(monkeypatch, fake)

    res = deploy_to_vm(
        "fedora", "192.168.13.90", "jean", rpm, resolve_target("rpm"), [],
        dry_run=False,
    )
    assert res.ok is True


def test_deploy_to_vm_appimage_skips_package_db(monkeypatch, tmp_path) -> None:
    app = _write(tmp_path / "rc.AppImage", b"\x7fELF")
    fake = ScriptedSSH(_linux_rules("", ""))
    _patch_ssh(monkeypatch, fake)

    res = deploy_to_vm(
        "ubuntu2204", "192.168.13.81", "jean", app, resolve_target("appimage"), [],
        dry_run=False,
    )

    assert res.ok is True
    assert "dpkg" not in fake.joined().replace("sudo dpkg", "")
    assert "rpm -q" not in fake.joined()


# ---------------------------------------------------------------------------
# C4 — reinstall without a rebuild
# ---------------------------------------------------------------------------

def test_find_windows_build_artifact_returns_newest_path() -> None:
    path = r"C:\mosdat-builds\pr3325\dist\rocketchat-4.18.4-win-x64.exe"
    fake = ScriptedSSH([("*-win-x64.exe", _result(0, f"ARTIFACT={path}\n"))])

    assert find_windows_build_artifact(fake, 3325) == path
    assert r"'C:\mosdat-builds\pr3325'" in fake.commands[0]
    assert "Sort-Object LastWriteTime -Descending" in fake.commands[0]


def test_find_windows_build_artifact_none() -> None:
    fake = ScriptedSSH([("*-win-x64.exe", _result(1, "", "no *-win-x64.exe in dist"))])
    assert find_windows_build_artifact(fake, 3325) == ""


_EXISTING = r"C:\mosdat-builds\pr3325\dist\rocketchat-4.18.4-win-x64.exe"


def test_install_existing_on_windows_vm_happy_path(monkeypatch) -> None:
    fake = ScriptedSSH([
        ("Test-Path -LiteralPath $p", _result(0, f"SIZE={117 * MIB}\nMAGIC=4D5A\n")),
        ("/S /D=", _result(0, "INSTALL_OK\n")),
        ("VersionInfo", _VERIFY_PATH_OK),
        ("Select-String", _result(0, "COUNT=3\n")),
    ])
    _patch_ssh(monkeypatch, fake)

    res = install_existing_on_windows_vm(
        "windows10", "192.168.13.87", "jean", _EXISTING, ["isTelephonyEnabled"],
        dry_run=False,
    )

    assert res.ok is True, res.error
    assert res.artifact == _EXISTING
    assert res.installed_version == "4.18.4"
    assert fake.scp_calls == []
    install = next(c for c in fake.commands if "/S /D=" in c)
    assert f"'{_EXISTING}'" in install


def test_install_existing_on_windows_vm_missing_file(monkeypatch) -> None:
    fake = ScriptedSSH([("Test-Path -LiteralPath $p", _result(1, "MISSING\n"))])
    _patch_ssh(monkeypatch, fake)

    res = install_existing_on_windows_vm(
        "windows10", "192.168.13.87", "jean", _EXISTING, [], dry_run=False,
    )

    assert "installer not found on windows10" in res.error
    assert len(fake.commands) == 1  # no install attempted
    assert res.ok is False


@pytest.mark.parametrize(
    "stdout,needle",
    [
        ("SIZE=96\nMAGIC=4D5A\n", "96 bytes"),
        (f"SIZE={50 * MIB}\nMAGIC=3C68\n", "not a Windows executable"),
    ],
)
def test_install_existing_on_windows_vm_rejects_bad_file(monkeypatch, stdout, needle) -> None:
    fake = ScriptedSSH([("Test-Path -LiteralPath $p", _result(0, stdout))])
    _patch_ssh(monkeypatch, fake)

    res = install_existing_on_windows_vm(
        "windows10", "192.168.13.87", "jean", _EXISTING, [], dry_run=False,
    )

    assert needle in res.error
    assert not any("/S /D=" in c for c in fake.commands)


def test_release_asset_names_and_v_prefix() -> None:
    tag, name, url = release_asset("v4.18.4", "exe")
    assert tag == "4.18.4"
    assert name == "rocketchat-4.18.4-win-x64.exe"
    assert url == (
        "https://github.com/RocketChat/Rocket.Chat.Electron/releases/download/"
        "4.18.4/rocketchat-4.18.4-win-x64.exe"
    )
    assert release_asset("4.18.4", "deb")[1] == "rocketchat-4.18.4-linux-amd64.deb"
    assert release_asset("4.18.4", "rpm")[1] == "rocketchat-4.18.4-linux-x86_64.rpm"
    assert release_asset("4.18.4", "appimage")[1] == "rocketchat-4.18.4-linux-x86_64.AppImage"


def test_install_release_on_vm_windows_downloads_in_foreground(monkeypatch) -> None:
    vm_path = r"C:\mosdat\tmp\rocketchat-4.18.4-win-x64.exe"
    fake = ScriptedSSH([
        ("curl.exe", _result(0, "DOWNLOAD_OK\n")),
        ("Test-Path -LiteralPath $p", _result(0, f"SIZE={117 * MIB}\nMAGIC=4D5A\n")),
        ("/S /D=", _result(0, "INSTALL_OK\n")),
        ("VersionInfo", _VERIFY_PATH_OK),
    ])
    _patch_ssh(monkeypatch, fake)

    res = install_release_on_vm(
        "windows10", "192.168.13.87", "jean", "windows", "4.18.4", [], dry_run=False,
    )

    assert res.ok is True, res.error
    assert res.artifact == vm_path
    download = fake.commands[0]
    assert (
        f"curl.exe -fsSL -o '{vm_path}' 'https://github.com/RocketChat/Rocket.Chat.Electron"
        "/releases/download/4.18.4/rocketchat-4.18.4-win-x64.exe'"
    ) in download
    assert "Start-Job" not in download and "Start-Process" not in download
    assert any(f"'{vm_path}'" in c and "/S /D=" in c for c in fake.commands)


def test_install_release_on_vm_windows_download_failure(monkeypatch) -> None:
    fake = ScriptedSSH([("curl.exe", _result(22, "", "curl: (22) 404"))])
    _patch_ssh(monkeypatch, fake)

    res = install_release_on_vm(
        "windows10", "192.168.13.87", "jean", "windows", "9.9.9", [], dry_run=False,
    )

    assert "download of release 9.9.9 failed" in res.error
    assert len(fake.commands) == 1
    assert res.ok is False


def test_install_release_on_vm_linux_downloads_then_deploys(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(build_mod.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(build_mod.shutil, "which", lambda _n: None)  # force curl
    dl_calls: list = []

    def _fake_run(cmd, **kw):
        dl_calls.append(cmd)
        _write(Path(cmd[cmd.index("-o") + 1]), b"!<arch>\n")
        return 0

    deployed: dict = {}

    def _fake_deploy(vm_name, vm_ip, vm_user, artifact, target, symbols, *, dry_run):
        deployed.update(artifact=artifact, target=target.name, dry_run=dry_run)
        return DeployResult(vm=vm_name, scp_ok=True, install_ok=True, artifact=str(artifact))

    monkeypatch.setattr(build_mod, "_run", _fake_run)
    monkeypatch.setattr(build_mod, "deploy_to_vm", _fake_deploy)

    res = install_release_on_vm(
        "ubuntu2204", "192.168.13.81", "jean", "linux", "4.18.4", [], dry_run=False,
    )

    expected = tmp_path / ".cache" / "mosdat" / "release-4.18.4" / "rocketchat-4.18.4-linux-amd64.deb"
    assert res.ok is True
    assert deployed == {"artifact": expected, "target": "deb", "dry_run": False}
    assert dl_calls[0][-1] == (
        "https://github.com/RocketChat/Rocket.Chat.Electron/releases/download/"
        "4.18.4/rocketchat-4.18.4-linux-amd64.deb"
    )

    # Second call: valid cached file → no new download.
    dl_calls.clear()
    install_release_on_vm(
        "ubuntu2204", "192.168.13.81", "jean", "linux", "4.18.4", [], dry_run=False,
    )
    assert dl_calls == []


def test_install_release_on_vm_linux_bad_download(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(build_mod.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(build_mod.shutil, "which", lambda _n: None)

    def _fake_run(cmd, **kw):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"Not Found")
        return 0

    def _refuse(*a, **kw):
        raise AssertionError("deploy_to_vm must not run after a bad download")

    monkeypatch.setattr(build_mod, "_run", _fake_run)
    monkeypatch.setattr(build_mod, "deploy_to_vm", _refuse)

    res = install_release_on_vm(
        "ubuntu2204", "192.168.13.81", "jean", "linux", "4.18.4", [], dry_run=False,
    )
    assert "download of release 4.18.4 failed" in res.error
    assert not Path(res.artifact).exists()


# ---------------------------------------------------------------------------
# C5 — --source: tar the local tree and build it on the Windows VM
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


@pytest.fixture
def source_repo(tmp_path) -> Path:
    repo = tmp_path / "Rocket.Chat.Electron-Jean"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("/app\n/dist\nnode_modules/\n*.log\n")
    (repo / "package.json").write_text("{}")
    (repo / "src").mkdir()
    (repo / "src" / "main.ts").write_text("committed")
    (repo / "gone.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "gone.txt").unlink()  # tracked but deleted
    (repo / "src" / "main.ts").write_text("modified, uncommitted")
    (repo / "src" / "new.ts").write_text("untracked")
    (repo / "debug.log").write_text("ignored")
    for d in ("app", "dist", "node_modules"):
        (repo / d).mkdir()
        (repo / d / "x.js").write_text("output")
    return repo


def test_make_source_tarball_includes_uncommitted_excludes_output(source_repo, tmp_path) -> None:
    dest = tmp_path / "src.tar"
    count, err = make_source_tarball(source_repo, dest)

    assert err == ""
    with tarfile.open(dest) as tf:
        names = sorted(tf.getnames())
        main = tf.extractfile("src/main.ts").read()
    assert names == [".gitignore", "package.json", "src/main.ts", "src/new.ts"]
    assert count == 4
    assert main == b"modified, uncommitted"


def test_make_source_tarball_not_a_repo(tmp_path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    count, err = make_source_tarball(plain, tmp_path / "x.tar")
    assert count == 0
    assert "git ls-files failed" in err


def _native_build_rules(free=_FREE_100) -> list:
    return [
        ("rmdir /s /q", _result(0, f"FREE_BYTES={100 * GIB}\n")),
        ("Get-PSDrive C", free),
        ("PREP_OK", _result(0, "PREP_OK\n")),
        ("tar.exe -xf", _result(0, "EXTRACT_OK\n")),
        ("Get-Command git", _result(0, "GIT_OK\n")),
        ("git clone", _result(0, "CLONE_OK\n")),
        ("node --version", _result(0, "PACKAGE_MANAGER=\nENGINES_NODE=\nNODE_VERSION=v24.11.1\n")),
        ("*-win-x64.exe", _result(
            0, r"ARTIFACT=C:\mosdat-builds\local-rocket.chat.electron-jean\dist\rocketchat-4.18.4-win-x64.exe" "\n",
        )),
        ("/S /D=", _result(0, "INSTALL_OK\n")),
        ("VersionInfo", _VERIFY_PATH_OK),
    ]


def test_build_on_windows_vm_from_source(monkeypatch, source_repo) -> None:
    fake = ScriptedSSH(_native_build_rules())
    _patch_ssh(monkeypatch, fake)

    def _refuse_gh(*a, **kw):
        raise AssertionError("no PR lookup for a --source build")

    monkeypatch.setattr(build_mod, "gh_pr_head_ref", _refuse_gh)

    res = build_on_windows_vm(
        "windows10", "192.168.13.87", "jean", 0, "RocketChat/Rocket.Chat.Electron",
        resolve_target("exe"), [], dry_run=False, source_dir=source_repo,
    )

    workspace = r"C:\mosdat-builds\local-rocket.chat.electron-jean"
    assert res.ok is True, res.error
    assert res.artifact == workspace + r"\dist\rocketchat-4.18.4-win-x64.exe"
    joined = fake.joined()
    assert "git clone" not in joined
    assert "Get-Command git" not in joined
    assert len(fake.scp_calls) == 1
    assert fake.scp_calls[0][1] == "C:/mosdat-builds/local-rocket.chat.electron-jean.tar"
    extract = next(c for c in fake.commands if "tar.exe -xf" in c)
    assert f"'{workspace}'" in extract
    assert "node_modules" in extract  # kept across rebuilds
    assert "$LASTEXITCODE" in extract
    # Same yarn steps as a PR build, in order, in the local workspace.
    steps = [c for c in fake.commands if "Set-Location" in c and "yarn" in c]
    assert [s for s in ("yarn install", "yarn build", "electron-builder")
            if any(s in c for c in steps)] == ["yarn install", "yarn build", "electron-builder"]
    assert all(f"'{workspace}'" in c for c in steps)
    # Local tarball is cleaned up.
    assert not fake.scp_calls[0][0].exists()


def test_run_build_source_routes_to_windows_build(monkeypatch, tmp_path, source_repo) -> None:
    from dataclasses import dataclass

    @dataclass
    class _VM:
        name: str
        ip: str
        user: str
        os_type: str

    @dataclass
    class _Cfg:
        vms: list

    import automation.config as cfg_mod
    monkeypatch.setattr(
        cfg_mod, "load_config",
        lambda _p: _Cfg(vms=[_VM("windows10", "192.168.13.87", "jean", "windows")]),
        raising=False,
    )
    seen: dict = {}

    def _fake_on_vm(*a, **kw):
        seen["args"], seen["kw"] = a, kw
        return DeployResult(vm=a[0], scp_ok=True, install_ok=True)

    monkeypatch.setattr(build_mod, "build_on_windows_vm", _fake_on_vm)

    from automation.commands.parser import build_parser
    args = build_parser().parse_args([
        "build", "--source", str(source_repo), "--target", "exe",
        "--deploy", "windows10", "--config", str(tmp_path / "x.toml"),
    ])
    assert args.pr is None

    assert build_mod.run_build(args) == 0
    assert seen["args"][3] == 0  # pr
    assert seen["kw"]["source_dir"] == source_repo.resolve()


def test_run_build_source_rejected_for_linux_target(tmp_path, source_repo) -> None:
    from automation.commands.parser import build_parser
    args = build_parser().parse_args([
        "build", "--source", str(source_repo), "--target", "deb", "--deploy", "ubuntu2204",
    ])
    assert build_mod.run_build(args) == 5


# ---------------------------------------------------------------------------
# C6 — free space on C: before a native Windows build
# ---------------------------------------------------------------------------

_WS = r"C:\mosdat-builds\pr3325"


def test_disk_guard_enough_space_no_cleanup() -> None:
    fake = ScriptedSSH([("Get-PSDrive C", _FREE_100)])
    assert build_mod._windows_disk_guard(fake, "windows10", _WS) == ""
    assert len(fake.commands) == 1
    assert "rmdir" not in fake.joined()


def test_disk_guard_low_space_cleans_old_workspaces(capsys) -> None:
    fake = ScriptedSSH([
        ("rmdir /s /q", _result(
            0,
            r"REMOVED=C:\mosdat-builds\pr3100" "\n"
            r"REMOVED=C:\mosdat-builds\pr3001" "\n"
            f"FREE_BYTES={22 * GIB}\n",
        )),
        ("Get-PSDrive C", _result(0, f"FREE_BYTES={10 * GIB}\n")),
    ])

    assert build_mod._windows_disk_guard(fake, "windows10", _WS) == ""

    cleanup = fake.commands[1]
    assert "$root = 'C:\\mosdat-builds'" in cleanup
    assert f"$keep = '{_WS}'" in cleanup
    assert "Where-Object { $_.FullName -ne $keep }" in cleanup
    assert "Sort-Object LastWriteTime -Descending" in cleanup
    assert "Select-Object -Skip 2" in cleanup
    out = capsys.readouterr().out
    assert r"removed C:\mosdat-builds\pr3100" in out
    assert "freed 12.0 GB" in out


def test_disk_guard_still_low_after_cleanup_fails() -> None:
    fake = ScriptedSSH([
        ("rmdir /s /q", _result(0, f"FREE_BYTES={6 * GIB}\n")),
        ("Get-PSDrive C", _result(0, f"FREE_BYTES={5 * GIB}\n")),
    ])
    err = build_mod._windows_disk_guard(fake, "windows10", _WS)
    assert "only 6.0 GB free on C:" in err
    assert "need >= 8 GB" in err


def test_disk_guard_unreadable_probe_does_not_block() -> None:
    fake = ScriptedSSH([("Get-PSDrive C", _result(1, "", "boom"))])
    assert build_mod._windows_disk_guard(fake, "windows10", _WS) == ""


def test_build_on_windows_vm_low_disk_fails_before_clone(monkeypatch) -> None:
    rules = _native_build_rules(free=_result(0, f"FREE_BYTES={3 * GIB}\n"))
    rules[0] = ("rmdir /s /q", _result(0, f"FREE_BYTES={4 * GIB}\n"))
    fake = ScriptedSSH(rules)
    _patch_ssh(monkeypatch, fake)
    monkeypatch.setattr(build_mod, "gh_pr_head_ref", lambda *a, **kw: "fix/foo")

    res = build_on_windows_vm(
        "windows10", "192.168.13.87", "jean", 3325, "RocketChat/Rocket.Chat.Electron",
        resolve_target("exe"), [], dry_run=False,
    )

    assert "only 4.0 GB free on C:" in res.error
    assert "git clone" not in fake.joined()
    assert "yarn" not in fake.joined()
    assert res.ok is False


def test_build_on_windows_vm_sets_artifact_to_vm_path(monkeypatch) -> None:
    rules = _native_build_rules()
    rules[7] = ("*-win-x64.exe", _result(0, r"ARTIFACT=C:\mosdat-builds\pr3325\dist\rc-win-x64.exe" "\n"))
    fake = ScriptedSSH(rules)
    _patch_ssh(monkeypatch, fake)
    monkeypatch.setattr(build_mod, "gh_pr_head_ref", lambda *a, **kw: "fix/foo")

    res = build_on_windows_vm(
        "windows10", "192.168.13.87", "jean", 3325, "RocketChat/Rocket.Chat.Electron",
        resolve_target("exe"), [], dry_run=False,
    )

    assert res.ok is True, res.error
    assert res.artifact == r"C:\mosdat-builds\pr3325\dist\rc-win-x64.exe"
    # Disk probe runs after the git check and before the clone.
    order = [i for i, c in enumerate(fake.commands)
             if "Get-Command git" in c or "Get-PSDrive C" in c or "git clone" in c]
    kinds = ["git" if "Get-Command" in fake.commands[i] else
             "disk" if "Get-PSDrive" in fake.commands[i] else "clone" for i in order]
    assert kinds == ["git", "disk", "clone"]


def test_verify_windows_install_checks_uninstall_registry_first(monkeypatch) -> None:
    """A stale NSIS InstallLocation put the app outside every default path."""
    scripts: list[str] = []

    class _Res:
        success = False
        stdout = ""

    def fake_ps_run(ssh, script, timeout=60):
        scripts.append(script)
        return _Res()

    monkeypatch.setattr(build_mod, "_ps_run", fake_ps_run)
    assert build_mod._verify_windows_install(object(), []) == ("", "", [])
    script = scripts[0]
    assert "CurrentVersion\\Uninstall" in script
    assert "InstallLocation" in script
    assert script.index("$reg") < script.index("$cands = @($reg)")


def test_windows_silent_install_pins_install_dir() -> None:
    script = build_mod._windows_silent_install_script("C:\\mosdat\\tmp\\x.exe")
    assert "/S /D=" in script
