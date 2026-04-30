"""Input injection for functional tests.

Mouse and keyboard events are delivered through the VNC/RFB channel
(no guest-side tools, works during boot and at the login screen).
`launch` and `focus_app` still use SSH because RFB can't spawn processes
or reorder window stacks.
"""

import base64

from ..transport.ssh import SSHClient
from ..transport.vnc import VncClient


def _ps_encoded(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return f"$ProgressPreference='SilentlyContinue'; powershell.exe -ExecutionPolicy Bypass -EncodedCommand {encoded}"


class InputInjector:
    def __init__(self, vnc: VncClient, ssh: SSHClient, is_windows: bool):
        self.vnc = vnc
        self.ssh = ssh
        self.is_windows = is_windows

    # ---- RFB-backed input ----

    def click(self, x: int, y: int) -> None:
        self.vnc.click(x, y)

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
                "  ENV_ARGS=$(tr '\\0' '\\n' </proc/\"$SESSION_PID\"/environ "
                "    | grep -E '^(DISPLAY|XAUTHORITY|WAYLAND_DISPLAY|"
                "DBUS_SESSION_BUS_ADDRESS|XDG_RUNTIME_DIR)=' | xargs); "
                "else "
                "  ENV_ARGS='DISPLAY=:0'; "
                "fi; "
                f"setsid env $ENV_ARGS sh -c '{escaped}' "
                "</dev/null >/dev/null 2>&1 & disown"
            )
            self.ssh.run(launcher, timeout=10)
