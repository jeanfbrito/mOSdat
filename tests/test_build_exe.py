"""Unit tests for the ``mosdat build --target exe`` path.

Pure unit-level — no live VM, no network. SSHClient is replaced with a
recording stub so we can assert the install/verify command sequence.
"""

from __future__ import annotations


# Mirror test_build_cmd.py: clear sibling-test stub pollution before importing
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

import argparse
import base64
import json
from pathlib import Path

import pytest

from automation.commands import build as build_mod
from automation.commands.build import (
    TARGETS,
    _ps_b64,
    _ps_quote,
    deploy_to_windows_vm,
    pick_artifact_url,
    resolve_target,
)


# ---------------------------------------------------------------------------
# TARGETS table — exe entry
# ---------------------------------------------------------------------------

def test_exe_target_registered() -> None:
    target = resolve_target("exe")
    assert target.name == "exe"
    assert target.dist_glob == "*.exe"
    assert target.yarn_release_args == ["--win"]


# ---------------------------------------------------------------------------
# pick_artifact_url — .exe extension
# ---------------------------------------------------------------------------

def test_pick_artifact_url_handles_exe() -> None:
    pr_data = {
        "comments": [
            {
                "author": {"login": "github-actions[bot]"},
                "body": (
                    "Build succeeded. Linux: "
                    "https://s3.us-east-1.wasabisys.com/builds.cloud.rocket.chat"
                    "/pr-3325/ubuntu-latest/rocketchat-4.14.1-linux-amd64.deb "
                    "Windows: "
                    "https://s3.us-east-1.wasabisys.com/builds.cloud.rocket.chat"
                    "/pr-3325/windows-latest/rocketchat-4.14.1-win-x64.exe"
                ),
                "createdAt": "2026-05-10T10:30:00Z",
            },
        ],
        "commits": [{"oid": "abc", "committedDate": "2026-05-10T10:00:00Z"}],
    }

    deb_url = pick_artifact_url(pr_data, ".deb")
    exe_url = pick_artifact_url(pr_data, ".exe")

    assert deb_url and deb_url.endswith(".deb")
    assert exe_url and exe_url.endswith(".exe")
    assert "win-x64" in exe_url


def test_pick_artifact_url_returns_none_for_unknown_ext() -> None:
    pr_data = {
        "comments": [{
            "author": {"login": "github-actions[bot]"},
            "body": "https://example.com/file.dmg",
            "createdAt": "2026-05-10T10:30:00Z",
        }],
        "commits": [{"oid": "abc", "committedDate": "2026-05-10T10:00:00Z"}],
    }
    assert pick_artifact_url(pr_data, ".dmg") is None


def test_pick_artifact_url_prefers_x64_over_arm64() -> None:
    """When a single comment carries both arches, default vm_arch='x64' wins.

    Guards against the alphabetical-sort bug where matches[0] returned
    ``...-arm64.exe`` on Windows-x64 VMs.
    """
    pr_data = {
        "comments": [{
            "author": {"login": "github-actions[bot]"},
            "body": (
                "Windows builds: "
                "https://s3.us-east-1.wasabisys.com/builds.cloud.rocket.chat"
                "/pr-3325/windows-latest/rocketchat-4.14.1-win-arm64.exe "
                "https://s3.us-east-1.wasabisys.com/builds.cloud.rocket.chat"
                "/pr-3325/windows-latest/rocketchat-4.14.1-win-x64.exe"
            ),
            "createdAt": "2026-05-10T10:30:00Z",
        }],
        "commits": [{"oid": "abc", "committedDate": "2026-05-10T10:00:00Z"}],
    }

    url = pick_artifact_url(pr_data, ".exe")
    assert url is not None
    assert "win-x64" in url
    assert "arm64" not in url


def test_pick_artifact_url_handles_x64_only() -> None:
    """Single x64 URL returned regardless of vm_arch default."""
    pr_data = {
        "comments": [{
            "author": {"login": "github-actions[bot]"},
            "body": (
                "https://s3.us-east-1.wasabisys.com/builds.cloud.rocket.chat"
                "/pr-3325/windows-latest/rocketchat-4.14.1-win-x64.exe"
            ),
            "createdAt": "2026-05-10T10:30:00Z",
        }],
        "commits": [{"oid": "abc", "committedDate": "2026-05-10T10:00:00Z"}],
    }
    url = pick_artifact_url(pr_data, ".exe")
    assert url is not None
    assert url.endswith("-win-x64.exe")


def test_pick_artifact_url_handles_arm64_only() -> None:
    """When only arm64 is available, return it (no cross-arch alternative)."""
    pr_data = {
        "comments": [{
            "author": {"login": "github-actions[bot]"},
            "body": (
                "https://s3.us-east-1.wasabisys.com/builds.cloud.rocket.chat"
                "/pr-3325/windows-latest/rocketchat-4.14.1-win-arm64.exe"
            ),
            "createdAt": "2026-05-10T10:30:00Z",
        }],
        "commits": [{"oid": "abc", "committedDate": "2026-05-10T10:00:00Z"}],
    }
    # vm_arch='x64' default — but arm64 is the only match, so it wins.
    url = pick_artifact_url(pr_data, ".exe")
    assert url is not None
    assert url.endswith("-win-arm64.exe")

    # Explicit arm64 request → same result.
    url_arm = pick_artifact_url(pr_data, ".exe", vm_arch="arm64")
    assert url_arm == url


def test_pick_artifact_url_arm64_vm_prefers_arm64() -> None:
    """vm_arch='arm64' inverts preference; arm64 url wins over x64."""
    pr_data = {
        "comments": [{
            "author": {"login": "github-actions[bot]"},
            "body": (
                "https://s3.us-east-1.wasabisys.com/builds.cloud.rocket.chat"
                "/pr-3325/windows-latest/rocketchat-4.14.1-win-arm64.exe "
                "https://s3.us-east-1.wasabisys.com/builds.cloud.rocket.chat"
                "/pr-3325/windows-latest/rocketchat-4.14.1-win-x64.exe"
            ),
            "createdAt": "2026-05-10T10:30:00Z",
        }],
        "commits": [{"oid": "abc", "committedDate": "2026-05-10T10:00:00Z"}],
    }
    url = pick_artifact_url(pr_data, ".exe", vm_arch="arm64")
    assert url is not None
    assert "arm64" in url
    assert "x64" not in url.split("/")[-1]


# ---------------------------------------------------------------------------
# PowerShell helpers
# ---------------------------------------------------------------------------

def test_ps_quote_escapes_single_quotes() -> None:
    assert _ps_quote("plain") == "'plain'"
    assert _ps_quote("it's") == "'it''s'"


def test_ps_b64_roundtrips_via_utf16le() -> None:
    script = "Write-Host 'hello'"
    enc = _ps_b64(script)
    decoded = base64.b64decode(enc).decode("utf-16-le")
    assert decoded == script


# ---------------------------------------------------------------------------
# deploy_to_windows_vm — recorded SSHClient
# ---------------------------------------------------------------------------

class _RecordingSSH:
    """Drop-in for SSHClient that records all run/scp_to invocations and
    returns scripted responses keyed by call order.
    """

    def __init__(self, host: str, user: str) -> None:  # noqa: D401
        self.host = host
        self.user = user
        self.scp_calls: list[tuple[Path, str]] = []
        self.run_calls: list[str] = []
        # Sequence of SSHResult-like values for successive run() calls.
        self._responses: list = []

    def queue(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        from automation.transport.ssh import SSHResult
        self._responses.append(SSHResult(returncode=returncode, stdout=stdout, stderr=stderr))

    def scp_to(self, local_path: Path, remote_path: str):
        from automation.transport.ssh import SSHResult
        self.scp_calls.append((local_path, remote_path))
        return SSHResult(returncode=0, stdout="", stderr="")

    def run(self, command: str, timeout=None):
        from automation.transport.ssh import SSHResult
        self.run_calls.append(command)
        if self._responses:
            return self._responses.pop(0)
        return SSHResult(returncode=0, stdout="", stderr="")


def _patch_sshclient(monkeypatch, recorder: _RecordingSSH) -> None:
    """Make ``from automation.transport.ssh import SSHClient`` return our recorder.

    The function imports SSHClient lazily inside deploy_to_windows_vm. We
    monkeypatch the module attribute that the lazy import resolves to.
    """
    import automation.transport.ssh as ssh_mod

    class _Factory:
        def __call__(self, host, user, *a, **kw):
            recorder.host = host
            recorder.user = user
            return recorder

    monkeypatch.setattr(ssh_mod, "SSHClient", _Factory())


def test_deploy_to_windows_vm_dry_run_no_ssh(monkeypatch, capsys) -> None:
    """Dry-run path: no SSHClient instantiated, no commands issued."""
    def _refuse(*a, **kw):
        raise AssertionError("SSHClient must not be created in dry-run")

    import automation.transport.ssh as ssh_mod
    monkeypatch.setattr(ssh_mod, "SSHClient", _refuse)

    target = resolve_target("exe")
    res = deploy_to_windows_vm(
        "windows10", "192.168.13.87", "jean",
        Path("/tmp/fake.exe"), target, ["myPrSymbol"], dry_run=True,
    )
    assert res.scp_ok is True
    assert res.install_ok is True
    assert res.installed_version == "(dry-run)"


def test_deploy_to_windows_vm_install_sequence(monkeypatch) -> None:
    """Happy-path: scp + install + verify-path + verify-symbol all succeed."""
    recorder = _RecordingSSH("192.168.13.87", "jean")
    # Sequence:
    #   1) install script → exit 0 with INSTALL_OK
    #   2) verify-path script → exit 0 with INSTALLED_AT + VERSION
    #   3) verify-symbol script → exit 0 with COUNT=2
    recorder.queue(0, stdout="INSTALL_OK\n")
    recorder.queue(
        0,
        stdout=(
            r"INSTALLED_AT=C:\Users\jean\AppData\Local\Programs\rocketchat-desktop\Rocket.Chat.exe"
            "\nVERSION=4.14.1\n"
        ),
    )
    recorder.queue(0, stdout="COUNT=2\n")

    _patch_sshclient(monkeypatch, recorder)

    target = resolve_target("exe")
    res = deploy_to_windows_vm(
        "windows10", "192.168.13.87", "jean",
        Path("/tmp/rocketchat-4.14.1-win-x64.exe"),
        target, ["isTelephonyEnabled"], dry_run=False,
    )

    # SCP went to C:/tmp/rc-installer.exe.
    assert len(recorder.scp_calls) == 1
    assert recorder.scp_calls[0][1] == "C:/tmp/rc-installer.exe"
    assert recorder.scp_calls[0][0] == Path("/tmp/rocketchat-4.14.1-win-x64.exe")

    # 3 PowerShell calls: install, verify-path, verify-symbol.
    assert len(recorder.run_calls) == 3
    for cmd in recorder.run_calls:
        assert cmd.startswith("powershell -NoProfile -EncodedCommand ")

    # Decode the install script and assert it contains the load-bearing pieces.
    install_b64 = recorder.run_calls[0].split()[-1]
    install_script = base64.b64decode(install_b64).decode("utf-16-le")
    assert "Get-Process" in install_script
    assert "Stop-Process" in install_script
    assert "Uninstall-Package" in install_script
    assert "'C:\\tmp\\rc-installer.exe'" in install_script
    assert "'/S'" in install_script  # NSIS silent flag

    # Verify-path script probes candidate paths.
    verify_b64 = recorder.run_calls[1].split()[-1]
    verify_script = base64.b64decode(verify_b64).decode("utf-16-le")
    assert "LOCALAPPDATA" in verify_script
    assert "rocketchat-desktop" in verify_script
    assert "VersionInfo.FileVersion" in verify_script

    # Verify-symbol script greps app.asar.
    sym_b64 = recorder.run_calls[2].split()[-1]
    sym_script = base64.b64decode(sym_b64).decode("utf-16-le")
    assert "app.asar" in sym_script
    assert "'isTelephonyEnabled'" in sym_script
    assert "Select-String" in sym_script

    # Result reflects the scripted output.
    assert res.ok is True
    assert res.installed_version == "4.14.1"
    assert res.missing_symbols == []
    assert res.error == ""


def test_deploy_to_windows_vm_missing_symbol(monkeypatch) -> None:
    """COUNT=0 from verify-symbol → record symbol as missing → res.ok False."""
    recorder = _RecordingSSH("192.168.13.87", "jean")
    recorder.queue(0, stdout="INSTALL_OK\n")
    recorder.queue(
        0,
        stdout=(
            r"INSTALLED_AT=C:\Users\jean\AppData\Local\Programs\rocketchat-desktop\Rocket.Chat.exe"
            "\nVERSION=4.14.1\n"
        ),
    )
    recorder.queue(0, stdout="COUNT=0\n")

    _patch_sshclient(monkeypatch, recorder)

    res = deploy_to_windows_vm(
        "windows10", "192.168.13.87", "jean",
        Path("/tmp/rocketchat-4.14.1-win-x64.exe"),
        resolve_target("exe"), ["nonExistentSymbol"], dry_run=False,
    )

    assert res.missing_symbols == ["nonExistentSymbol"]
    assert res.ok is False


def test_deploy_to_windows_vm_install_failure(monkeypatch) -> None:
    """Installer returns non-zero → res.install_ok False, error set, no verify."""
    recorder = _RecordingSSH("192.168.13.87", "jean")
    recorder.queue(1, stdout="", stderr="installer exit code 1")

    _patch_sshclient(monkeypatch, recorder)

    res = deploy_to_windows_vm(
        "windows10", "192.168.13.87", "jean",
        Path("/tmp/fake.exe"),
        resolve_target("exe"), [], dry_run=False,
    )

    # Only the install command should have been attempted; no verify.
    assert len(recorder.run_calls) == 1
    assert res.scp_ok is True
    assert res.install_ok is False
    assert "installer exit code 1" in res.error


def test_deploy_to_windows_vm_path_not_found(monkeypatch) -> None:
    """Install succeeds but no candidate path resolves → res.error set."""
    recorder = _RecordingSSH("192.168.13.87", "jean")
    recorder.queue(0, stdout="INSTALL_OK\n")
    # Verify-path script exits 1 (NOT_FOUND).
    recorder.queue(1, stdout="", stderr="NOT_FOUND")

    _patch_sshclient(monkeypatch, recorder)

    res = deploy_to_windows_vm(
        "windows10", "192.168.13.87", "jean",
        Path("/tmp/fake.exe"),
        resolve_target("exe"), [], dry_run=False,
    )

    assert res.install_ok is True
    assert "could not locate installed Rocket.Chat.exe" in res.error


# ---------------------------------------------------------------------------
# run_build — dispatcher routes Windows VMs to deploy_to_windows_vm
# ---------------------------------------------------------------------------

def test_run_build_dispatches_windows_vm(monkeypatch, capsys, tmp_path) -> None:
    """When config marks a VM os_type=windows, run_build routes the deploy to
    deploy_to_windows_vm instead of deploy_to_vm.
    """
    # Stub config loading.
    from dataclasses import dataclass

    @dataclass
    class _StubVM:
        name: str
        ip: str
        user: str
        os_type: str

    @dataclass
    class _StubCfg:
        vms: list

    def _load_cfg(_path):
        return _StubCfg(vms=[_StubVM("windows10", "192.168.13.87", "jean", "windows")])

    import automation.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load_config", _load_cfg, raising=False)

    # Record which deploy function is called.
    called: dict = {}

    def _fake_linux_deploy(*a, **kw):
        called["linux"] = (a, kw)
        from automation.commands.build import DeployResult
        return DeployResult(vm=a[0], scp_ok=True, install_ok=True)

    def _fake_windows_deploy(*a, **kw):
        called["windows"] = (a, kw)
        from automation.commands.build import DeployResult
        return DeployResult(vm=a[0], scp_ok=True, install_ok=True, installed_version="4.14.1")

    monkeypatch.setattr(build_mod, "deploy_to_vm", _fake_linux_deploy)
    monkeypatch.setattr(build_mod, "deploy_to_windows_vm", _fake_windows_deploy)

    args = argparse.Namespace(
        pr="3325",
        repo="RocketChat/Rocket.Chat.Electron",
        target="exe",
        clone_dir=str(tmp_path / "clone"),
        deploy="windows10",
        verify_symbol=[],
        config=str(tmp_path / "x.toml"),
        dry_run=True,
        artifact_first=False,
    )

    rc = build_mod.run_build(args)
    assert rc == 0
    assert "windows" in called
    assert "linux" not in called
    # First positional arg is vm_name.
    assert called["windows"][0][0] == "windows10"


def test_run_build_rejects_target_exe_with_linux_vm(monkeypatch, capsys, tmp_path) -> None:
    """--target exe + linux VM → guard fires, deploy returns error, exit code 3."""
    from dataclasses import dataclass

    @dataclass
    class _StubVM:
        name: str
        ip: str
        user: str
        os_type: str

    @dataclass
    class _StubCfg:
        vms: list

    def _load_cfg(_path):
        return _StubCfg(vms=[_StubVM("ubuntu2204", "192.168.13.10", "jean", "linux")])

    import automation.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load_config", _load_cfg, raising=False)

    def _refuse(*a, **kw):
        raise AssertionError("deploy_to_windows_vm must not be called for linux VM")

    monkeypatch.setattr(build_mod, "deploy_to_windows_vm", _refuse)

    args = argparse.Namespace(
        pr="3325",
        repo="RocketChat/Rocket.Chat.Electron",
        target="exe",
        clone_dir=str(tmp_path / "clone"),
        deploy="ubuntu2204",
        verify_symbol=[],
        config=str(tmp_path / "x.toml"),
        dry_run=True,
        artifact_first=False,
    )

    rc = build_mod.run_build(args)
    # exit 3 = deploy phase failure (one VM reported an error).
    assert rc == 3
    out = capsys.readouterr().out
    assert "--target exe requires a Windows VM" in out


def test_run_build_rejects_target_deb_with_windows_vm(monkeypatch, capsys, tmp_path) -> None:
    """--target deb + windows VM → guard fires before any deploy."""
    from dataclasses import dataclass

    @dataclass
    class _StubVM:
        name: str
        ip: str
        user: str
        os_type: str

    @dataclass
    class _StubCfg:
        vms: list

    def _load_cfg(_path):
        return _StubCfg(vms=[_StubVM("windows10", "192.168.13.87", "jean", "windows")])

    import automation.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load_config", _load_cfg, raising=False)

    def _refuse(*a, **kw):
        raise AssertionError("deploy_to_vm must not be called for windows VM")

    monkeypatch.setattr(build_mod, "deploy_to_vm", _refuse)

    args = argparse.Namespace(
        pr="3325",
        repo="RocketChat/Rocket.Chat.Electron",
        target="deb",
        clone_dir=str(tmp_path / "clone"),
        deploy="windows10",
        verify_symbol=[],
        config=str(tmp_path / "x.toml"),
        dry_run=True,
        artifact_first=False,
    )

    rc = build_mod.run_build(args)
    assert rc == 3
    out = capsys.readouterr().out
    assert "Windows VM requires --target exe" in out


# ---------------------------------------------------------------------------
# Local-fallback path triggers `yarn release --win`
# ---------------------------------------------------------------------------

def test_run_build_local_fallback_uses_yarn_win(monkeypatch, capsys, tmp_path) -> None:
    """When artifact-first is off and --target exe, the build phase runs yarn
    release with --win (electron-builder picks the NSIS .exe output).
    """
    # Capture all _run invocations so we can assert the release command shape.
    run_calls: list = []

    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    # Force has_yarn = True by writing yarn.lock.
    (clone_dir / "yarn.lock").write_text("")
    (clone_dir / "package.json").write_text("{}")
    # Pre-create a fake artifact so match_artifact finds it.
    dist = clone_dir / "dist"
    dist.mkdir()
    fake_exe = dist / "rocketchat-4.14.1-win-x64.exe"
    fake_exe.write_bytes(b"\x4dZ" + b"\x00" * 2_000_000)  # MZ header + bulk

    def _fake_run(cmd, *a, **kw):
        run_calls.append(cmd)
        return 0

    def _fake_capture(cmd, *a, **kw):
        # gh + git log calls. Return innocuous JSON / text.
        if "log" in cmd:
            return 0, "abcdef HEAD\n", ""
        return 0, "{}", ""

    monkeypatch.setattr(build_mod, "_run", _fake_run)
    monkeypatch.setattr(build_mod, "_capture", _fake_capture)

    # Stub clone_or_update so we don't try to talk to gh.
    monkeypatch.setattr(build_mod, "clone_or_update", lambda *a, **kw: 0)

    args = argparse.Namespace(
        pr="3325",
        repo="RocketChat/Rocket.Chat.Electron",
        target="exe",
        clone_dir=str(clone_dir),
        deploy="",  # no deploys — build phase only
        verify_symbol=[],
        config=None,
        dry_run=False,
        artifact_first=False,  # force local build path
    )

    rc = build_mod.run_build(args)
    assert rc == 0, f"unexpected exit: {rc} (run_calls={run_calls})"

    # Find the release command in the recorded _run calls.
    release_cmds = [c for c in run_calls if "release" in c]
    assert release_cmds, f"no release command in {run_calls}"
    rel = release_cmds[0]
    assert rel[:2] == ["yarn", "release"]
    assert "--win" in rel
