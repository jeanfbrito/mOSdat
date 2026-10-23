"""Run a multi-line script on a VM through an uploaded file.

Inline commands over SSH lose `$vars` on Windows (the OpenSSH default shell
is PowerShell, which expands them before the child sees them), die with the
SSH session, and `-EncodedCommand` is blocked by Defender. This module avoids
all three: it writes the script to a file, copies it with scp, and runs the
file.

Modes:
  * plain (default): run the file and wait; return stdout/stderr/exit code.
  * detach=True: start the file so that it outlives the SSH session; return
    the remote log path and PID. returncode 0 means "started".
  * session="desktop" (Windows only): run the file in the logged-in user's
    interactive session (session 1) through an InteractiveToken scheduled
    task. Output comes back from a log file.

Windows assumption: the OpenSSH DefaultShell is powershell.exe (the lab VMs
use it). The plain run appends `; exit $LASTEXITCODE` because PowerShell
`-c` otherwise reports 1 for any non-zero child exit code.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape as _xml_escape

from automation.transport.ssh import SSHResult

WIN_TMP_DIR = r"C:\mosdat\tmp"
LINUX_TMP_DIR = "/tmp"
DONE_MARKER = "__MOSDAT_DONE__"

# PowerShell flags shared by every Windows run. -File (not -EncodedCommand):
# Defender blocks encoded commands.
_PS_FLAGS = "-NoProfile -NonInteractive -ExecutionPolicy Bypass"

# Prepended to every Windows script. UTF-8 output keeps non-ASCII text intact
# (the console default is the OEM codepage); the try/catch covers hosts that
# have no console handle.
_WIN_HEADER = (
    "$ProgressPreference = 'SilentlyContinue'\r\n"
    "try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}\r\n"
)


@dataclass
class ScriptResult:
    returncode: int
    stdout: str
    stderr: str
    remote_path: str
    log_path: Optional[str] = None
    pid: Optional[int] = None

    @property
    def success(self) -> bool:
        return self.returncode == 0


# ------------------------------------------------------------------ helpers
def _win_scp_path(win_path: str) -> str:
    """`C:\\mosdat\\tmp\\x.ps1` -> `C:/mosdat/tmp/x.ps1` (scp target form)."""
    return win_path.replace("\\", "/")


def _upload(ssh, content: bytes, remote_path: str) -> SSHResult:
    """Write `content` to a local temp file and scp it to `remote_path`."""
    fd, local = tempfile.mkstemp(prefix="mosdat-script-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        return ssh.scp_to(Path(local), remote_path)
    finally:
        try:
            os.unlink(local)
        except OSError:
            pass


def _win_bytes(text: str) -> bytes:
    """UTF-8 with BOM, so PowerShell 5.1 reads non-ASCII text correctly."""
    return b"\xef\xbb\xbf" + text.encode("utf-8")


def _win_del(*paths: str) -> str:
    return "cmd /c del /f /q " + " ".join(paths)


def _failed(step: str, r: SSHResult, remote_path: str) -> ScriptResult:
    err = (r.stderr or r.stdout or "").strip()
    return ScriptResult(
        returncode=r.returncode or 1,
        stdout=r.stdout,
        stderr=f"{step} failed: {err}" if err else f"{step} failed",
        remote_path=remote_path,
    )


def _parse_pid(stdout: str) -> Optional[int]:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


# ------------------------------------------------------------- public API
def run_script(
    ssh,
    script: str,
    *,
    is_windows: bool,
    timeout: int = 60,
    detach: bool = False,
    session: str = "ssh",
) -> ScriptResult:
    """Upload `script` to the VM and run it.

    `ssh` is an `SSHClient` (or any object with `run(cmd, timeout=)` and
    `scp_to(local, remote)`). `timeout` bounds the wait for the script in
    plain and desktop modes; it is ignored for a detached start.
    """
    if session not in ("ssh", "desktop"):
        raise ValueError(f"session must be 'ssh' or 'desktop', not {session!r}")
    if session == "desktop" and not is_windows:
        raise ValueError("session='desktop' is supported on Windows only")

    rid = uuid.uuid4().hex
    if is_windows:
        if session == "desktop":
            return _run_windows_desktop(ssh, script, rid, timeout, detach)
        return _run_windows(ssh, script, rid, timeout, detach)
    return _run_linux(ssh, script, rid, timeout, detach)


# ------------------------------------------------------------------ Linux
def _run_linux(ssh, script: str, rid: str, timeout: int, detach: bool) -> ScriptResult:
    remote = f"{LINUX_TMP_DIR}/mosdat-{rid}.sh"
    up = _upload(ssh, script.encode("utf-8"), remote)
    if not up.success:
        return _failed("scp", up, remote)

    if detach:
        log = f"{LINUX_TMP_DIR}/mosdat-{rid}.log"
        # setsid: new session, so the SSH hangup does not reach the job.
        # All three std streams are redirected, so ssh returns at once.
        r = ssh.run(
            f"setsid nohup bash {remote} > {log} 2>&1 < /dev/null & echo $!",
            timeout=30,
        )
        pid = _parse_pid(r.stdout)
        if not r.success or pid is None:
            return _failed("detached start", r, remote)
        return ScriptResult(0, r.stdout, r.stderr, remote, log_path=log, pid=pid)

    r = ssh.run(f"bash {remote}", timeout=timeout)
    ssh.run(f"rm -f {remote}", timeout=15)
    return ScriptResult(r.returncode, r.stdout, r.stderr, remote)


# ---------------------------------------------------------------- Windows
def _win_mkdir(ssh) -> SSHResult:
    # cmd syntax works under both a PowerShell and a cmd DefaultShell.
    return ssh.run(
        f"cmd /c if not exist {WIN_TMP_DIR} mkdir {WIN_TMP_DIR}", timeout=20
    )


def _run_windows(ssh, script: str, rid: str, timeout: int, detach: bool) -> ScriptResult:
    remote = f"{WIN_TMP_DIR}\\{rid}.ps1"
    mk = _win_mkdir(ssh)
    if not mk.success:
        return _failed("mkdir", mk, remote)
    up = _upload(ssh, _win_bytes(_WIN_HEADER + script), _win_scp_path(remote))
    if not up.success:
        return _failed("scp", up, remote)

    run_cmd = f"powershell {_PS_FLAGS} -File {remote}"

    if detach:
        log = f"{WIN_TMP_DIR}\\{rid}.log"
        launcher = f"{WIN_TMP_DIR}\\{rid}-launch.ps1"
        # Win32_Process.Create runs the process from the WMI service, so it
        # is not in the sshd job object and survives the session end.
        command_line = f'cmd /c "{run_cmd} > {log} 2>&1"'
        launch_ps = (
            _WIN_HEADER
            + "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            + "-Arguments @{CommandLine='" + command_line.replace("'", "''") + "'}\r\n"
            + "if ($r.ReturnValue -ne 0) { "
            + "[Console]::Error.WriteLine('Win32_Process.Create returned ' + $r.ReturnValue); exit 1 }\r\n"
            + "Write-Output $r.ProcessId\r\n"
        )
        up = _upload(ssh, _win_bytes(launch_ps), _win_scp_path(launcher))
        if not up.success:
            return _failed("scp", up, remote)
        r = ssh.run(
            f"powershell {_PS_FLAGS} -File {launcher}; exit $LASTEXITCODE",
            timeout=60,
        )
        ssh.run(_win_del(launcher), timeout=15)
        pid = _parse_pid(r.stdout)
        if not r.success or pid is None:
            return _failed("detached start", r, remote)
        return ScriptResult(0, r.stdout, r.stderr, remote, log_path=log, pid=pid)

    r = ssh.run(f"{run_cmd}; exit $LASTEXITCODE", timeout=timeout)
    ssh.run(_win_del(remote), timeout=15)
    return ScriptResult(r.returncode, r.stdout, r.stderr, remote)


def _task_xml(action_args: str, time_limit: str) -> str:
    """Scheduled task XML: run powershell.exe in the interactive session.

    Same pattern as `automation/vlm/input.py` `launch` (InteractiveToken,
    no UserId: the task runs as the registering user, in session 1).
    """
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\r\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        '<Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType>'
        "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>"
        "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
        f"<ExecutionTimeLimit>{time_limit}</ExecutionTimeLimit></Settings>"
        '<Actions Context="Author"><Exec><Command>powershell.exe</Command>'
        f"<Arguments>{_xml_escape(action_args)}</Arguments></Exec></Actions>"
        "</Task>"
    )


def _run_windows_desktop(
    ssh, script: str, rid: str, timeout: int, detach: bool
) -> ScriptResult:
    remote = f"{WIN_TMP_DIR}\\{rid}.ps1"
    wrapper = f"{WIN_TMP_DIR}\\{rid}-run.ps1"
    launcher = f"{WIN_TMP_DIR}\\{rid}-launch.ps1"
    log = f"{WIN_TMP_DIR}\\{rid}.log"
    task = f"mosdat-script-{rid}"

    mk = _win_mkdir(ssh)
    if not mk.success:
        return _failed("mkdir", mk, remote)

    # The wrapper is the task action. It runs hidden in session 1 and starts
    # the script as a child of its (hidden) console, so no window appears.
    # cmd redirection captures stdout and stderr of the script; the marker
    # line carries the exit code, also after an `exit N` in the script.
    # The wrapper cannot delete its own task (its LeastPrivilege token gets
    # access denied on a task registered from the SSH session), so the
    # launcher deletes it.
    wrapper_ps = (
        f"cmd /c \"powershell {_PS_FLAGS} -File {remote} > {log} 2>&1\"\r\n"
        "$code = $LASTEXITCODE\r\n"
        f"[System.IO.File]::AppendAllText('{log}', \"`r`n{DONE_MARKER} $code`r`n\")\r\n"
    )
    action_args = f"{_PS_FLAGS} -WindowStyle Hidden -File {wrapper}"
    xml = _task_xml(action_args, "PT0S" if detach else f"PT{int(timeout) + 60}S")

    launch_ps = (
        _WIN_HEADER
        + f"$task = '{task}'\r\n"
        + f"$log = '{log}'\r\n"
        + f"$xmlPath = '{WIN_TMP_DIR}\\{rid}.xml'\r\n"
        + "$xml = @'\r\n" + xml + "\r\n'@\r\n"
        + "[System.IO.File]::WriteAllText($xmlPath, $xml, [System.Text.Encoding]::Unicode)\r\n"
        + "schtasks /Create /F /TN $task /XML $xmlPath 2>&1 | Out-Null\r\n"
        + "$created = $LASTEXITCODE\r\n"
        + "Remove-Item -Force -LiteralPath $xmlPath -ErrorAction SilentlyContinue\r\n"
        + "if ($created -ne 0) { [Console]::Error.WriteLine('schtasks /Create failed'); exit 2 }\r\n"
        + "schtasks /Run /TN $task 2>&1 | Out-Null\r\n"
        + "if ($LASTEXITCODE -ne 0) { schtasks /Delete /F /TN $task 2>$null | Out-Null; "
        + "[Console]::Error.WriteLine('schtasks /Run failed'); exit 2 }\r\n"
    )
    if detach:
        # Deleting a running task does not stop its instance. Wait until the
        # log exists (the action has started), then delete the task.
        launch_ps += (
            "$deadline = (Get-Date).AddSeconds(15)\r\n"
            "while (-not (Test-Path -LiteralPath $log) -and (Get-Date) -lt $deadline) "
            "{ Start-Sleep -Milliseconds 250 }\r\n"
            "$started = Test-Path -LiteralPath $log\r\n"
            "schtasks /Delete /F /TN $task 2>$null | Out-Null\r\n"
            "if (-not $started) { [Console]::Error.WriteLine('task did not start within 15 s'); exit 3 }\r\n"
            "Write-Output 'started'\r\nexit 0\r\n"
        )
    else:
        launch_ps += (
            f"$deadline = (Get-Date).AddSeconds({int(timeout)})\r\n"
            "$content = ''\r\n"
            "$done = $false\r\n"
            "while ((Get-Date) -lt $deadline) {\r\n"
            "  if (Test-Path -LiteralPath $log) {\r\n"
            "    try { $content = [System.IO.File]::ReadAllText($log, [System.Text.Encoding]::UTF8) } catch {}\r\n"
            f"    if ($content.Contains('{DONE_MARKER}')) {{ $done = $true; break }}\r\n"
            "  }\r\n"
            "  Start-Sleep -Milliseconds 500\r\n"
            "}\r\n"
            "if (-not $done) { schtasks /End /TN $task 2>$null | Out-Null }\r\n"
            "schtasks /Delete /F /TN $task 2>$null | Out-Null\r\n"
            "[Console]::Out.Write($content)\r\n"
            "if ($done) { exit 0 } else { exit 124 }\r\n"
        )

    for path, body in ((remote, _WIN_HEADER + script), (wrapper, wrapper_ps), (launcher, launch_ps)):
        up = _upload(ssh, _win_bytes(body), _win_scp_path(path))
        if not up.success:
            return _failed("scp", up, remote)

    ssh_timeout = 60 if detach else int(timeout) + 60
    r = ssh.run(
        f"powershell {_PS_FLAGS} -File {launcher}; exit $LASTEXITCODE",
        timeout=ssh_timeout,
    )

    if detach:
        ssh.run(_win_del(launcher), timeout=15)
        if not r.success:
            return _failed("desktop start", r, remote)
        return ScriptResult(0, r.stdout, r.stderr, remote, log_path=log)

    ssh.run(_win_del(remote, wrapper, launcher, log), timeout=15)
    return _parse_desktop_output(r, remote)


def _parse_desktop_output(r: SSHResult, remote: str) -> ScriptResult:
    """Split the log content returned by the launcher into output + exit code."""
    out = r.stdout or ""
    idx = out.rfind(DONE_MARKER)
    if idx < 0:
        if r.returncode == 124:
            return ScriptResult(124, out, "desktop script timed out", remote)
        return _failed("desktop run", r, remote)
    tail = out[idx + len(DONE_MARKER):].strip()
    try:
        code = int(tail.split()[0]) if tail else 0
    except ValueError:
        code = 1
    body = out[:idx]
    # Drop the "\r\n" the wrapper writes before the marker.
    if body.endswith("\r\n"):
        body = body[:-2]
    elif body.endswith("\n"):
        body = body[:-1]
    return ScriptResult(code, body, r.stderr, remote)
