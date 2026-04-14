"""SSH-based mouse and keyboard input injection for Windows and Linux VMs."""

import base64
import time

from .ssh import SSHClient

# Windows key name → SendKeys notation
_WIN_KEY_MAP = {
    "enter":     "{ENTER}",
    "return":    "{ENTER}",
    "tab":       "{TAB}",
    "escape":    "{ESC}",
    "esc":       "{ESC}",
    "backspace": "{BACKSPACE}",
    "delete":    "{DELETE}",
    "home":      "{HOME}",
    "end":       "{END}",
    "pgup":      "{PGUP}",
    "pgdn":      "{PGDN}",
    "up":        "{UP}",
    "down":      "{DOWN}",
    "left":      "{LEFT}",
    "right":     "{RIGHT}",
    "ctrl+a":    "^a",
    "ctrl+c":    "^c",
    "ctrl+v":    "^v",
    "ctrl+z":    "^z",
    "ctrl+w":    "^w",
    "alt+f4":    "%{F4}",
}

# Linux key name → xdotool notation
_LINUX_KEY_MAP = {
    "enter":     "Return",
    "return":    "Return",
    "tab":       "Tab",
    "escape":    "Escape",
    "esc":       "Escape",
    "backspace": "BackSpace",
    "delete":    "Delete",
    "home":      "Home",
    "end":       "End",
    "pgup":      "Prior",
    "pgdn":      "Next",
    "up":        "Up",
    "down":      "Down",
    "left":      "Left",
    "right":     "Right",
    "ctrl+a":    "ctrl+a",
    "ctrl+c":    "ctrl+c",
    "ctrl+v":    "ctrl+v",
    "ctrl+z":    "ctrl+z",
    "ctrl+w":    "ctrl+w",
    "alt+f4":    "alt+F4",
}

# SendKeys special characters that must be escaped with braces
_SENDKEYS_SPECIAL = set("^%~+(){}[]")


def _escape_sendkeys(text: str) -> str:
    """Escape special characters for PowerShell SendKeys."""
    result = []
    for ch in text:
        if ch in _SENDKEYS_SPECIAL:
            result.append(f"{{{ch}}}")
        else:
            result.append(ch)
    return "".join(result)


def _ps_encoded(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return f"$ProgressPreference='SilentlyContinue'; powershell.exe -ExecutionPolicy Bypass -EncodedCommand {encoded}"


# Win32 mouse/keyboard PowerShell helper (compiled once per session via Add-Type)
_WIN32_CLICK_PS = """
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class WinInput {{
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,int e);
    public const uint LEFTDOWN = 0x02;
    public const uint LEFTUP   = 0x04;
    public static void Click(int x, int y) {{
        SetCursorPos(x, y);
        System.Threading.Thread.Sleep(80);
        mouse_event(LEFTDOWN,0,0,0,0);
        System.Threading.Thread.Sleep(30);
        mouse_event(LEFTUP,0,0,0,0);
    }}
}}
"@ -ErrorAction SilentlyContinue
[WinInput]::Click({x}, {y})
Start-Sleep -Milliseconds 100
Write-Output "click:ok"
"""

_WIN32_TYPE_PS = """
Add-Type -AssemblyName System.Windows.Forms
Start-Sleep -Milliseconds 150
[System.Windows.Forms.SendKeys]::SendWait('{keys}')
Start-Sleep -Milliseconds 100
Write-Output "type:ok"
"""

_WIN32_KEY_PS = """
Add-Type -AssemblyName System.Windows.Forms
Start-Sleep -Milliseconds 100
[System.Windows.Forms.SendKeys]::SendWait('{keys}')
Start-Sleep -Milliseconds 100
Write-Output "key:ok"
"""


class InputInjector:
    def __init__(self, ssh: SSHClient, is_windows: bool):
        self.ssh = ssh
        self.is_windows = is_windows

    def click(self, x: int, y: int) -> None:
        """Click at pixel coordinates (x, y)."""
        if self.is_windows:
            ps = _WIN32_CLICK_PS.format(x=x, y=y)
            self.ssh.run(_ps_encoded(ps), timeout=10)
        else:
            self.ssh.run(f"DISPLAY=:0 xdotool mousemove {x} {y} click 1", timeout=10)

    def type_text(self, text: str) -> None:
        """Type a string of text into the focused element."""
        if self.is_windows:
            escaped = _escape_sendkeys(text)
            ps = _WIN32_TYPE_PS.format(keys=escaped)
            self.ssh.run(_ps_encoded(ps), timeout=15)
        else:
            # xdotool type with a small delay between characters for reliability
            safe = text.replace("'", "'\\''")
            self.ssh.run(f"DISPLAY=:0 xdotool type --delay 50 '{safe}'", timeout=15)

    def key(self, key: str) -> None:
        """Press a named key (enter, tab, escape, ctrl+v, etc.)."""
        key_lower = key.lower()
        if self.is_windows:
            sendkeys = _WIN_KEY_MAP.get(key_lower, f"{{{key_lower}}}")
            ps = _WIN32_KEY_PS.format(keys=sendkeys)
            self.ssh.run(_ps_encoded(ps), timeout=10)
        else:
            xdotool_key = _LINUX_KEY_MAP.get(key_lower, key_lower)
            self.ssh.run(f"DISPLAY=:0 xdotool key {xdotool_key}", timeout=10)

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
            safe = cmd.replace("'", "'\\''")
            self.ssh.run(
                f"DISPLAY=:0 nohup '{safe}' </dev/null >/dev/null 2>&1 &",
                timeout=5,
            )
