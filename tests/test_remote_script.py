"""Tests for automation.transport.remote_script.run_script.

Hermetic: a fake ssh object records every run/scp call and the bytes of
each uploaded file. The tests pin the exact remote commands for each mode.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Callable, List, Optional

import pytest

from automation.transport import remote_script as rs
from automation.transport.remote_script import DONE_MARKER, run_script


class SSHResult:
    # Local stand-in: other test files replace automation.transport.ssh in
    # sys.modules with stubs, so importing the real class is order-dependent.
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    @property
    def success(self) -> bool:
        return self.returncode == 0


RID = "0123456789abcdef0123456789abcdef"
BOM = b"\xef\xbb\xbf"
FLAGS = "-NoProfile -NonInteractive -ExecutionPolicy Bypass"
PS = f"powershell {FLAGS}"
WIN = "C:\\mosdat\\tmp\\"


class FakeSSH:
    """Records run() and scp_to() calls. `responder(cmd)` returns SSHResult."""

    def __init__(self, responder: Optional[Callable[[str], SSHResult]] = None,
                 scp_rc: int = 0) -> None:
        self.calls: List[tuple] = []
        self.uploads: dict[str, bytes] = {}
        self._responder = responder or (lambda cmd: SSHResult(0, "", ""))
        self._scp_rc = scp_rc

    def run(self, command: str, timeout: Optional[int] = None, **_kw) -> SSHResult:
        self.calls.append(("run", command, timeout))
        return self._responder(command)

    def scp_to(self, local_path: Path, remote_path: str) -> SSHResult:
        self.calls.append(("scp", remote_path))
        self.uploads[remote_path] = Path(local_path).read_bytes()
        return SSHResult(self._scp_rc, "", "scp: denied" if self._scp_rc else "")

    def commands(self) -> List[str]:
        return [c[1] for c in self.calls if c[0] == "run"]


@pytest.fixture(autouse=True)
def fixed_uuid(monkeypatch):
    monkeypatch.setattr(
        rs, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=RID))
    )


# ------------------------------------------------------------- validation
def test_desktop_session_on_linux_raises():
    with pytest.raises(ValueError, match="Windows only"):
        run_script(FakeSSH(), "echo hi", is_windows=False, session="desktop")


def test_unknown_session_raises():
    with pytest.raises(ValueError, match="session must be"):
        run_script(FakeSSH(), "echo hi", is_windows=True, session="rdp")


# ------------------------------------------------------------------ Linux
def test_linux_plain_commands_and_result():
    ssh = FakeSSH(lambda cmd: SSHResult(4, "val=hello\n", "err\n")
                  if cmd.startswith("bash ") else SSHResult(0, "", ""))
    r = run_script(ssh, 'x=hello\necho "val=$x"\n', is_windows=False, timeout=42)
    remote = f"/tmp/mosdat-{RID}.sh"
    assert ssh.calls == [
        ("scp", remote),
        ("run", f"bash {remote}", 42),
        ("run", f"rm -f {remote}", 15),
    ]
    assert ssh.uploads[remote] == b'x=hello\necho "val=$x"\n'
    assert (r.returncode, r.stdout, r.stderr) == (4, "val=hello\n", "err\n")
    assert r.remote_path == remote
    assert r.log_path is None and r.pid is None


def test_linux_detach_commands_and_pid():
    ssh = FakeSSH(lambda cmd: SSHResult(0, "4242\n", ""))
    r = run_script(ssh, "sleep 10", is_windows=False, detach=True)
    remote = f"/tmp/mosdat-{RID}.sh"
    log = f"/tmp/mosdat-{RID}.log"
    assert ssh.commands() == [
        f"setsid nohup bash {remote} > {log} 2>&1 < /dev/null & echo $!",
    ]
    assert r.returncode == 0
    assert r.pid == 4242
    assert r.log_path == log
    # The detached job still needs its file: no rm.
    assert not any(c.startswith("rm ") for c in ssh.commands())


def test_linux_detach_without_pid_is_failure():
    ssh = FakeSSH(lambda cmd: SSHResult(0, "", ""))
    r = run_script(ssh, "sleep 10", is_windows=False, detach=True)
    assert r.returncode != 0
    assert "detached start failed" in r.stderr


def test_scp_failure_stops_before_run():
    ssh = FakeSSH(scp_rc=1)
    r = run_script(ssh, "echo hi", is_windows=False)
    assert ssh.commands() == []
    assert r.returncode == 1
    assert "scp failed" in r.stderr


# ---------------------------------------------------------------- Windows
def test_windows_plain_commands_and_file_content():
    ssh = FakeSSH(lambda cmd: SSHResult(0, "val=hello\n", "")
                  if cmd.startswith("powershell") else SSHResult(0, "", ""))
    script = "$x = 'hello'; Write-Output \"val=$x\" # caf\u00e9"
    r = run_script(ssh, script, is_windows=True, timeout=30)
    remote = f"{WIN}{RID}.ps1"
    assert ssh.calls == [
        ("run", "cmd /c if not exist C:\\mosdat\\tmp mkdir C:\\mosdat\\tmp", 20),
        ("scp", f"C:/mosdat/tmp/{RID}.ps1"),
        ("run", f"{PS} -File {remote}; exit $LASTEXITCODE", 30),
        ("run", f"cmd /c del /f /q {remote}", 15),
    ]
    body = ssh.uploads[f"C:/mosdat/tmp/{RID}.ps1"]
    assert body.startswith(BOM)
    text = body[len(BOM):].decode("utf-8")
    assert text.startswith("$ProgressPreference = 'SilentlyContinue'")
    assert text.endswith(script)
    assert r.stdout == "val=hello\n" and r.returncode == 0


def test_windows_never_uses_encoded_command():
    ssh = FakeSSH(lambda cmd: SSHResult(0, "1\n", ""))
    for kw in ({}, {"detach": True}, {"session": "desktop"},
               {"session": "desktop", "detach": True}):
        run_script(ssh, "Write-Output 1", is_windows=True, **kw)
    blobs = ssh.commands() + [b.decode("utf-8", "replace") for b in ssh.uploads.values()]
    assert not any("EncodedCommand" in b for b in blobs)


def test_windows_mkdir_failure_stops():
    ssh = FakeSSH(lambda cmd: SSHResult(1, "", "Access denied"))
    r = run_script(ssh, "Write-Output 1", is_windows=True)
    assert [c[0] for c in ssh.calls] == ["run"]
    assert r.returncode == 1 and "mkdir failed: Access denied" in r.stderr


def test_windows_detach_uses_win32_process_create():
    ssh = FakeSSH(lambda cmd: SSHResult(0, "10764\n", "")
                  if "launch.ps1" in cmd and cmd.startswith("powershell")
                  else SSHResult(0, "", ""))
    r = run_script(ssh, "Start-Sleep 20", is_windows=True, detach=True)
    remote = f"{WIN}{RID}.ps1"
    launcher = f"{WIN}{RID}-launch.ps1"
    log = f"{WIN}{RID}.log"
    assert ssh.calls == [
        ("run", "cmd /c if not exist C:\\mosdat\\tmp mkdir C:\\mosdat\\tmp", 20),
        ("scp", f"C:/mosdat/tmp/{RID}.ps1"),
        ("scp", f"C:/mosdat/tmp/{RID}-launch.ps1"),
        ("run", f"{PS} -File {launcher}; exit $LASTEXITCODE", 60),
        ("run", f"cmd /c del /f /q {launcher}", 15),
    ]
    launch = ssh.uploads[f"C:/mosdat/tmp/{RID}-launch.ps1"].decode("utf-8-sig")
    expected_cl = f'cmd /c "{PS} -File {remote} > {log} 2>&1"'
    assert (
        "Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{CommandLine='{expected_cl}'}}"
    ) in launch
    assert (r.returncode, r.pid, r.log_path, r.remote_path) == (0, 10764, log, remote)


def test_windows_detach_create_failure():
    ssh = FakeSSH(lambda cmd: SSHResult(1, "", "Win32_Process.Create returned 2")
                  if "launch.ps1" in cmd and cmd.startswith("powershell")
                  else SSHResult(0, "", ""))
    r = run_script(ssh, "Start-Sleep 20", is_windows=True, detach=True)
    assert r.returncode == 1
    assert "Win32_Process.Create returned 2" in r.stderr
    assert r.pid is None


# ------------------------------------------------------------ Windows desktop
def _desktop_ssh(launcher_out: SSHResult) -> FakeSSH:
    return FakeSSH(lambda cmd: launcher_out
                   if cmd.startswith("powershell") else SSHResult(0, "", ""))


def test_desktop_commands_task_xml_and_output():
    ssh = _desktop_ssh(SSHResult(0, f"1\r\n\r\n{DONE_MARKER} 0\r\n", ""))
    r = run_script(ssh, "[System.Diagnostics.Process]::GetCurrentProcess().SessionId",
                   is_windows=True, session="desktop", timeout=30)
    remote = f"{WIN}{RID}.ps1"
    wrapper = f"{WIN}{RID}-run.ps1"
    launcher = f"{WIN}{RID}-launch.ps1"
    log = f"{WIN}{RID}.log"
    assert ssh.calls == [
        ("run", "cmd /c if not exist C:\\mosdat\\tmp mkdir C:\\mosdat\\tmp", 20),
        ("scp", f"C:/mosdat/tmp/{RID}.ps1"),
        ("scp", f"C:/mosdat/tmp/{RID}-run.ps1"),
        ("scp", f"C:/mosdat/tmp/{RID}-launch.ps1"),
        ("run", f"{PS} -File {launcher}; exit $LASTEXITCODE", 90),
        ("run", f"cmd /c del /f /q {remote} {wrapper} {launcher} {log}", 15),
    ]
    wrap = ssh.uploads[f"C:/mosdat/tmp/{RID}-run.ps1"].decode("utf-8-sig")
    assert f'cmd /c "{PS} -File {remote} > {log} 2>&1"' in wrap
    assert DONE_MARKER in wrap and "$LASTEXITCODE" in wrap

    launch = ssh.uploads[f"C:/mosdat/tmp/{RID}-launch.ps1"].decode("utf-8-sig")
    assert "<LogonType>InteractiveToken</LogonType>" in launch
    assert (f"<Arguments>{FLAGS} -WindowStyle Hidden -File {wrapper}</Arguments>"
            in launch)
    assert "<ExecutionTimeLimit>PT90S</ExecutionTimeLimit>" in launch
    assert f"$task = 'mosdat-script-{RID}'" in launch
    assert "schtasks /Create /F /TN $task /XML $xmlPath" in launch
    assert "schtasks /Run /TN $task" in launch
    assert "Start-Sleep -Milliseconds 500" in launch
    assert "schtasks /Delete /F /TN $task" in launch

    assert (r.returncode, r.stdout) == (0, "1\r\n")
    assert r.log_path is None


def test_desktop_exit_code_from_marker():
    ssh = _desktop_ssh(SSHResult(0, f"v=caf\u00e9\n\n{DONE_MARKER} 5\n", ""))
    r = run_script(ssh, "exit 5", is_windows=True, session="desktop")
    assert (r.returncode, r.stdout) == (5, "v=caf\u00e9\n")


def test_desktop_timeout_returns_124_and_partial_output():
    ssh = _desktop_ssh(SSHResult(124, "partial\n", ""))
    r = run_script(ssh, "Start-Sleep 30", is_windows=True, session="desktop", timeout=3)
    assert r.returncode == 124
    assert r.stdout == "partial\n"
    assert "timed out" in r.stderr
    # Files are still cleaned up after a timeout.
    assert ssh.commands()[-1].startswith("cmd /c del /f /q ")


def test_desktop_detach_returns_log_without_waiting():
    ssh = _desktop_ssh(SSHResult(0, "started\n", ""))
    r = run_script(ssh, "Start-Sleep 5", is_windows=True, session="desktop", detach=True)
    launcher = f"{WIN}{RID}-launch.ps1"
    assert ssh.commands()[-2:] == [
        f"{PS} -File {launcher}; exit $LASTEXITCODE",
        f"cmd /c del /f /q {launcher}",
    ]
    launch = ssh.uploads[f"C:/mosdat/tmp/{RID}-launch.ps1"].decode("utf-8-sig")
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in launch
    # Waits for the log (task started), then deletes the task; no poll loop.
    assert "Test-Path -LiteralPath $log" in launch
    assert DONE_MARKER not in launch
    assert (r.returncode, r.log_path, r.pid) == (0, f"{WIN}{RID}.log", None)


def test_desktop_detach_start_failure():
    ssh = _desktop_ssh(SSHResult(3, "", "task did not start within 15 s"))
    r = run_script(ssh, "Start-Sleep 5", is_windows=True, session="desktop", detach=True)
    assert r.returncode == 3
    assert "task did not start" in r.stderr
