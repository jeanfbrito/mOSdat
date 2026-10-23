"""Input injection for functional tests.

Mouse and keyboard events are delivered through the VNC/RFB channel
(no guest-side tools, works during boot and at the login screen).
`launch` and `focus_app` still use SSH because RFB can't spawn processes
or reorder window stacks.
"""

import base64
import time
from typing import Optional

from ..config import CursorConfig
from ..transport.ssh import SSHClient
from ..transport.vnc import VncClient


def _ps_encoded(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return f"$ProgressPreference='SilentlyContinue'; powershell.exe -ExecutionPolicy Bypass -EncodedCommand {encoded}"


# mouse_event flags per VNC-style button number: (down, up).
_NATIVE_BUTTON_FLAGS = {
    1: (0x0002, 0x0004),  # MOUSEEVENTF_LEFTDOWN / LEFTUP
    3: (0x0008, 0x0010),  # MOUSEEVENTF_RIGHTDOWN / RIGHTUP
}
_NATIVE_CLICK_DONE = "C:\\tmp\\mosdat_click.done"


def _native_click_inner_ps(x: int, y: int, button: int = 1) -> str:
    """PowerShell that runs in Session 1 and clicks with user32.

    SetProcessDPIAware comes first so SetCursorPos takes physical pixels,
    the same pixels as the VNC framebuffer. The script writes a marker file
    after the button-up, so the SSH caller can wait for the click.
    """
    if button not in _NATIVE_BUTTON_FLAGS:
        raise ValueError(f"native_click: unsupported button {button} (use 1=left or 3=right)")
    down, up = _NATIVE_BUTTON_FLAGS[button]
    return f"""
Add-Type @"
using System; using System.Runtime.InteropServices;
public class MosdatClick {{
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e);
}}
"@ -ErrorAction SilentlyContinue
[MosdatClick]::SetProcessDPIAware() | Out-Null
[MosdatClick]::SetCursorPos({int(x)}, {int(y)}) | Out-Null
Start-Sleep -Milliseconds 100
[MosdatClick]::mouse_event(0x{down:04X}, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 50
[MosdatClick]::mouse_event(0x{up:04X}, 0, 0, 0, [UIntPtr]::Zero)
[System.IO.File]::WriteAllText('{_NATIVE_CLICK_DONE}', 'ok')
"""


class InputInjector:
    def __init__(
        self,
        vnc: VncClient,
        ssh: SSHClient,
        is_windows: bool,
        cursor_config: Optional[CursorConfig] = None,
    ):
        self.vnc = vnc
        self.ssh = ssh
        self.is_windows = is_windows
        self._cursor_config: CursorConfig = cursor_config if cursor_config is not None else CursorConfig()

    def _position_cursor(
        self,
        x: int,
        y: int,
        motion: Optional[str] = None,
        dwell_ms: Optional[int] = None,
    ) -> None:
        """Move cursor to (x, y) using human_move, then optionally dwell."""
        effective_profile = motion if motion is not None else self._cursor_config.profile
        cfg = self._cursor_config
        self.vnc.human_move(
            x,
            y,
            profile=effective_profile,
            duration_ms=float(cfg.duration_ms) if cfg.duration_ms else None,
            seed=cfg.seed,
        )
        effective_dwell = dwell_ms if dwell_ms is not None else cfg.hover_dwell_ms
        if effective_dwell:
            time.sleep(effective_dwell / 1000)

    # ---- RFB-backed input ----

    def move(self, x: int, y: int) -> None:
        """Move mouse to absolute coordinates (no click, wakes DPMS)."""
        self.vnc.move(x, y)

    def click(
        self,
        x: int,
        y: int,
        button: int = 1,
        motion: Optional[str] = None,
        dwell_ms: Optional[int] = None,
    ) -> None:
        self._position_cursor(x, y, motion=motion, dwell_ms=dwell_ms)
        self.vnc.click(x, y, button=button)

    def native_click(self, x: int, y: int, button: int = 1, timeout_s: int = 15) -> None:
        """Click at (x, y) from inside the Windows desktop session (Session 1).

        A VNC pointer click on a Windows toast dismisses the toast; a click
        made with user32 SetCursorPos + mouse_event in Session 1 activates it.
        Runs a schtasks InteractiveToken task (same pattern as focus_app) and
        waits for the task's marker file, so the click is done on return.
        Does not use VNC. Windows only.
        """
        if not self.is_windows:
            raise RuntimeError("native_click is Windows-only")
        inner = _native_click_inner_ps(x, y, button)
        enc = base64.b64encode(inner.encode("utf-16-le")).decode()
        ps = f"""
$ProgressPreference='SilentlyContinue'
$null = New-Item -Force -ItemType Directory C:\\tmp
$done = '{_NATIVE_CLICK_DONE}'
Remove-Item -Force -ErrorAction SilentlyContinue $done
$xml = @'
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Principals><Principal id="A"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals><Settings><ExecutionTimeLimit>PT1M</ExecutionTimeLimit><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries></Settings><Actions Context="A"><Exec><Command>powershell.exe</Command><Arguments>-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand {enc}</Arguments></Exec></Actions></Task>
'@
[System.IO.File]::WriteAllText('C:\\tmp\\mosdat_click.xml', $xml, [System.Text.Encoding]::Unicode)
schtasks /Delete /F /TN "mosdat-click" 2>$null | Out-Null
schtasks /Create /F /TN "mosdat-click" /XML 'C:\\tmp\\mosdat_click.xml' 2>$null | Out-Null
schtasks /Run /TN "mosdat-click" 2>$null | Out-Null
$deadline = (Get-Date).AddSeconds({int(timeout_s)})
while (-not (Test-Path $done) -and (Get-Date) -lt $deadline) {{ Start-Sleep -Milliseconds 100 }}
$ok = Test-Path $done
schtasks /Delete /F /TN "mosdat-click" 2>$null | Out-Null
Remove-Item -Force -ErrorAction SilentlyContinue $done, 'C:\\tmp\\mosdat_click.xml'
if ($ok) {{ Write-Output "native_click:ok" }} else {{ Write-Output "native_click:timeout" }}
"""
        result = self.ssh.run(_ps_encoded(ps), timeout=int(timeout_s) + 15)
        if "native_click:ok" not in (getattr(result, "stdout", "") or ""):
            raise RuntimeError(
                f"native_click ({x}, {y}) did not complete in Session 1 within "
                f"{timeout_s}s: {getattr(result, 'stdout', '')!r} {getattr(result, 'stderr', '')!r}"
            )

    def hover(
        self,
        x: int,
        y: int,
        motion: Optional[str] = None,
        dwell_ms: Optional[int] = None,
    ) -> None:
        """Position cursor at (x, y) with optional human-like path and dwell; no button press."""
        self._position_cursor(x, y, motion=motion, dwell_ms=dwell_ms)

    def type_text(self, text: str) -> None:
        self.vnc.type_text(text)

    def key(self, key: str) -> None:
        self.vnc.key(key)

    def shell(self, cmd: str, timeout: int = 60) -> None:
        """Run an arbitrary shell command on the VM.

        On Windows the command is executed as a PowerShell script via
        base64-encoded -EncodedCommand (so quotes, newlines, and $env: all work).
        On Linux the command is passed through to the SSH default shell.
        """
        if self.is_windows:
            self.ssh.run(_ps_encoded(cmd), timeout=timeout)
        else:
            self.ssh.run(cmd, timeout=timeout)

    def shell_result(self, cmd: str, timeout: int = 60):
        """Run a shell command and return the SSHResult (exit code, stdout, stderr).

        I14: Used by the functional runner to capture shell output for HTML reports.
        Callers that don't need the result can continue using shell().
        """
        from ..transport.ssh import SSHResult  # noqa: F401 — for type reference
        if self.is_windows:
            return self.ssh.run(_ps_encoded(cmd), timeout=timeout)
        return self.ssh.run(cmd, timeout=timeout)

    def focus_app(self, window_title: str) -> None:
        """Bring a named window to the foreground in the interactive desktop session.

        Uses schtasks InteractiveToken so the call runs in Session 1 (visible
        desktop) rather than the SSH Session 0.
        """
        if not self.is_windows:
            self.ssh.run(
                f"DISPLAY=:0 wmctrl -a '{window_title}' 2>/dev/null || true",
                timeout=5,
            )
            return
        # Escape title for embedding in a here-string
        safe_title = window_title.replace('"', '`"')
        inner = f"""
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class WF {{
    public delegate bool EWP(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EWP e, IntPtr l);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int m);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr hi, int x, int y, int cx, int cy, uint f);
    public static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
}}
"@ -ErrorAction SilentlyContinue
$sb = New-Object System.Text.StringBuilder 256
[WF]::EnumWindows({{param($h,$l)
    $sb.Clear() | Out-Null; [WF]::GetWindowText($h,$sb,256) | Out-Null; $t = $sb.ToString()
    if ([WF]::IsWindowVisible($h) -and $t -eq "{safe_title}") {{
        [WF]::ShowWindow($h, 9) | Out-Null
        [WF]::SetForegroundWindow($h) | Out-Null
        [WF]::SetWindowPos($h, [WF]::HWND_TOPMOST, 0,0,0,0, 3) | Out-Null
    }}
    return $true
}}, [IntPtr]::Zero) | Out-Null
"""
        enc = base64.b64encode(inner.encode("utf-16-le")).decode()
        ps = f"""
$ProgressPreference='SilentlyContinue'
$null = New-Item -Force -ItemType Directory C:\\tmp
$xml = @'
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Principals><Principal id="A"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals><Settings><ExecutionTimeLimit>PT1M</ExecutionTimeLimit><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries></Settings><Actions Context="A"><Exec><Command>powershell.exe</Command><Arguments>-ExecutionPolicy Bypass -EncodedCommand {enc}</Arguments></Exec></Actions></Task>
'@
[System.IO.File]::WriteAllText('C:\\tmp\\mosdat_focus.xml', $xml, [System.Text.Encoding]::Unicode)
schtasks /Delete /F /TN "mosdat-focus" 2>$null | Out-Null
schtasks /Create /F /TN "mosdat-focus" /XML 'C:\\tmp\\mosdat_focus.xml' 2>$null | Out-Null
schtasks /Run /TN "mosdat-focus" 2>$null | Out-Null
Start-Sleep -Seconds 1
schtasks /Delete /F /TN "mosdat-focus" 2>$null | Out-Null
Write-Output "focus:ok"
"""
        self.ssh.run(_ps_encoded(ps), timeout=15)

    def process_running(self, name: str) -> bool:
        """Return True if a process matching `name` is running on the VM.

        Uses pgrep -f on Linux and Get-Process on Windows.
        The name is the basename of the executable (or any substring).
        """
        if self.is_windows:
            # Get-Process returns Name WITHOUT the .exe extension, so strip it
            # before matching. "Rocket.Chat.exe" → "Rocket.Chat" else the
            # wildcard *Rocket.Chat.exe* never matches.
            # Use _ps_encoded so the Windows SSH server shell never touches $_ —
            # a raw `powershell.exe -Command "... | Where-Object {$_.Name ...}"``
            # sent over SSH has $_ stripped by the Windows OpenSSH default shell
            # before PowerShell ever sees it.
            search_name = name[:-4] if name.lower().endswith(".exe") else name
            ps = (
                f"Get-Process -ErrorAction SilentlyContinue | "
                f"Where-Object {{$_.Name -like '*{search_name}*'}} | "
                f"Measure-Object | Select-Object -ExpandProperty Count"
            )
            result = self.ssh.run(_ps_encoded(ps), timeout=10)
            try:
                return int(result.stdout.strip()) > 0
            except (ValueError, AttributeError):
                return False
        else:
            # pgrep -f over SSH self-matches the ssh bash cmdline (the search
            # string appears as a literal argument). Filter via /proc/<pid>/exe
            # which is a symlink to the real executable — bash/sshd/pgrep won't
            # have the target name in their exe path.
            base = name.rsplit("/", 1)[-1]
            cmd = (
                "for p in $(pgrep -f " + repr(base) + " 2>/dev/null); do "
                "exe=$(readlink /proc/$p/exe 2>/dev/null); "
                f"case \"$exe\" in *{base}*) echo $p; exit 0;; esac; "
                "done; exit 1"
            )
            result = self.ssh.run(cmd, timeout=10)
            return result.returncode == 0 and bool(result.stdout.strip())

    def launch(self, cmd: str) -> None:
        """Launch an application in the user's interactive desktop session.

        On Windows, SSH runs in Session 0 so a plain Start-Process would be
        invisible.  We use a scheduled task with InteractiveToken so the process
        appears on the logged-in user's visible desktop.

        On Linux, nohup with DISPLAY=:0 handles it.
        """
        if self.is_windows:
            safe = cmd.replace("'", "''")
            inner = f"Start-Process -FilePath '{safe}'"
            # Build schtasks XML that runs as the interactive (desktop) user
            ps = r"""
$ProgressPreference='SilentlyContinue'
$null = New-Item -Force -ItemType Directory C:\tmp 2>$null
$inner = '""" + inner.replace("'", "''") + r"""'
$enc = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($inner))
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><ExecutionTimeLimit>PT1M</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec><Command>powershell.exe</Command><Arguments>-ExecutionPolicy Bypass -EncodedCommand $enc</Arguments></Exec></Actions>
</Task>
"@
[System.IO.File]::WriteAllText('C:\tmp\mosdat_launch.xml', $xml, [System.Text.Encoding]::Unicode)
schtasks /Delete /F /TN "mosdat-launch" 2>$null | Out-Null
schtasks /Create /F /TN "mosdat-launch" /XML 'C:\tmp\mosdat_launch.xml' 2>$null | Out-Null
schtasks /Run /TN "mosdat-launch" 2>$null | Out-Null
Start-Sleep -Seconds 1
schtasks /Delete /F /TN "mosdat-launch" 2>$null | Out-Null
Write-Output "launch:ok"
"""
            self.ssh.run(_ps_encoded(ps), timeout=15)
        else:
            # Launch in the user's graphical session. Grab DISPLAY / XAUTHORITY /
            # WAYLAND_DISPLAY / DBUS_SESSION_BUS_ADDRESS / XDG_RUNTIME_DIR from
            # gnome-shell's /proc/<pid>/environ so Electron (and any X or
            # Wayland client) picks the same compositor and auth file the
            # logged-in user is using. Falls back to DISPLAY=:0 if gnome-shell
            # isn't running (other DEs are handled similarly — the only thing
            # that matters is finding the session leader's env block).
            # cmd may include args (e.g. "/bin/app --no-sandbox"); pass it
            # through to sh -c so the shell tokenizes it correctly.
            escaped = cmd.replace("'", "'\\''")
            launcher = (
                "SESSION_PID=$(pgrep -u \"$USER\" -x gnome-shell || "
                "pgrep -u \"$USER\" -x plasmashell || "
                "pgrep -u \"$USER\" -x xfce4-session); "
                "if [ -n \"$SESSION_PID\" ]; then "
                # Export each KEY=VALUE directly into the current shell so
                # word-splitting never mangles values that contain '=' (e.g.
                # DBUS_SESSION_BUS_ADDRESS=unix:path=...). `xargs` + unquoted
                # variable expansion was causing env to misparse those values.
                "  while IFS= read -r _line; do export \"$_line\"; done < "
                "    <(tr '\\0' '\\n' </proc/\"$SESSION_PID\"/environ "
                "    | grep -E '^(DISPLAY|XAUTHORITY|WAYLAND_DISPLAY|"
                "DBUS_SESSION_BUS_ADDRESS|XDG_RUNTIME_DIR|XDG_SESSION_TYPE)='); "
                "else "
                "  export DISPLAY=:0; "
                "fi; "
                f"setsid sh -c '{escaped}' "
                "</dev/null >/dev/null 2>&1 & disown"
            )
            self.ssh.run(launcher, timeout=10)
