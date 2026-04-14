"""Screen capture for Windows and Linux VMs.

Primary method: Proxmox API screendump (works on any OS, no guest scripts).
Fallback:       SSH-based capture (BitBlt on Windows, scrot/grim on Linux).
"""

import base64
import io
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PIL import Image

from .ssh import SSHClient

if TYPE_CHECKING:
    from .proxmox import ProxmoxAPI


class ScreenshotError(Exception):
    pass


# PowerShell screen capture using schtasks to run in the interactive desktop session.
# SSH sessions run in Session 0 (isolated from the desktop), so CopyFromScreen fails.
# Scheduling a task under the logged-in user runs it in their Session 1 context.
_PS_CAPTURE_TASK = r"""
$ProgressPreference='SilentlyContinue'
$outFile = 'C:\tmp\mosdat_ss.png'
$null = New-Item -Force -ItemType Directory C:\tmp 2>$null

$inner = @'
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen([System.Drawing.Point]::Empty, [System.Drawing.Point]::Empty, $screen.Size)
$bmp.Save('C:\tmp\mosdat_ss.png')
$g.Dispose(); $bmp.Dispose()
'@
$enc = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($inner))

# Remove stale output and old task
Remove-Item -Force $outFile -ErrorAction SilentlyContinue
schtasks /Delete /F /TN "mosdat-screenshot" 2>$null | Out-Null

# Create task running as the interactive user (INTERACTIVE = current desktop session)
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><ExecutionTimeLimit>PT1M</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec><Command>powershell.exe</Command><Arguments>-ExecutionPolicy Bypass -EncodedCommand $enc</Arguments></Exec></Actions>
</Task>
"@
$xmlFile = "C:\tmp\mosdat_ss_task.xml"
[System.IO.File]::WriteAllText($xmlFile, $xml, [System.Text.Encoding]::Unicode)
schtasks /Create /F /TN "mosdat-screenshot" /XML $xmlFile 2>$null | Out-Null
schtasks /Run /TN "mosdat-screenshot" 2>$null | Out-Null

# Wait up to 8 seconds for the file to appear
$deadline = (Get-Date).AddSeconds(8)
while ((Get-Date) -lt $deadline) {
    if (Test-Path $outFile) {
        $sz = (Get-Item $outFile).Length
        if ($sz -gt 10000) { break }
    }
    Start-Sleep -Milliseconds 300
}
schtasks /Delete /F /TN "mosdat-screenshot" 2>$null | Out-Null

if (Test-Path $outFile) {
    $sz = (Get-Item $outFile).Length
    $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    if ($sz -gt 10000) {
        Write-Output "OK:$($screen.Width)x$($screen.Height)"
    } else {
        Write-Output "FAIL:file too small ($sz bytes) - possibly black screen"
    }
} else {
    Write-Output "FAIL:file not created"
}
"""


class Screenshotter:
    def __init__(
        self,
        ssh: SSHClient,
        is_windows: bool,
        proxmox: Optional["ProxmoxAPI"] = None,
        vmid: Optional[int] = None,
    ):
        self.ssh = ssh
        self.is_windows = is_windows
        self._proxmox = proxmox
        self._vmid = vmid

    def capture(self) -> tuple[Image.Image, tuple[int, int]]:
        """Take a screenshot of the VM desktop.

        Returns (PIL Image, (width, height)).
        Raises ScreenshotError on failure.
        """
        if self._proxmox and self._vmid is not None:
            return self._capture_proxmox()
        if self.is_windows:
            return self._capture_windows()
        return self._capture_linux()

    def _capture_proxmox(self) -> tuple[Image.Image, tuple[int, int]]:
        """Use Proxmox API screendump — works on any OS, no guest-side scripts.

        Falls back to SSH capture if the Proxmox endpoint is unavailable (e.g. older PVE).
        """
        try:
            png_bytes = self._proxmox.screenshot(self._vmid)  # type: ignore[union-attr]
            img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            return img, img.size
        except Exception:
            # Proxmox screenshot API not available on this version — use SSH fallback
            if self.is_windows:
                return self._capture_windows()
            return self._capture_linux()

    def _capture_windows(self) -> tuple[Image.Image, tuple[int, int]]:
        # Forward slashes required for Windows OpenSSH SCP
        scp_remote = "C:/tmp/mosdat_ss.png"
        encoded = base64.b64encode(_PS_CAPTURE_TASK.encode("utf-16-le")).decode()
        cmd = f"$ProgressPreference='SilentlyContinue'; powershell.exe -ExecutionPolicy Bypass -EncodedCommand {encoded}"

        result = self.ssh.run(cmd, timeout=25)
        if not result.success:
            raise ScreenshotError(f"Screenshot capture failed: {result.stderr[:200]}")

        ok = False
        for line in result.stdout.splitlines():
            if line.startswith("OK:"):
                ok = True
            elif line.startswith("FAIL:"):
                raise ScreenshotError(f"Screenshot failed on VM: {line[5:]}")
        if not ok:
            raise ScreenshotError(f"Unexpected screenshot output: {result.stdout[:200]}")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            local_path = Path(tmp.name)

        scp_result = self.ssh.scp_from(scp_remote, local_path)
        if not scp_result.success:
            raise ScreenshotError(f"SCP failed: {scp_result.stderr[:200]}")

        img = Image.open(local_path).copy()
        local_path.unlink(missing_ok=True)
        # Use actual image dimensions — the task runs at desktop resolution
        return img, img.size

    def _capture_linux(self) -> tuple[Image.Image, tuple[int, int]]:
        remote_path = "/tmp/mosdat_ss.png"

        # Try scrot (X11), fall back to grim (Wayland), then gnome-screenshot
        for cmd in [
            f"DISPLAY=:0 scrot {remote_path}",
            f"WAYLAND_DISPLAY=wayland-0 grim {remote_path}",
            f"DISPLAY=:0 import -window root {remote_path}",
        ]:
            result = self.ssh.run(cmd, timeout=10)
            if result.success:
                break
        else:
            raise ScreenshotError("No screenshot tool available (tried scrot, grim, import)")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            local_path = Path(tmp.name)

        scp_result = self.ssh.scp_from(remote_path, local_path)
        if not scp_result.success:
            raise ScreenshotError(f"SCP failed: {scp_result.stderr[:200]}")

        img = Image.open(local_path).copy()
        local_path.unlink(missing_ok=True)
        size = img.size
        return img, size
