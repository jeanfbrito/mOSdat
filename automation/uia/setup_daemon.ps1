# mOSdat UIA daemon -- register the persistent worker as a logon-triggered
# scheduled task running under the interactive user's token (Session 1).
# Idempotent: re-running is safe (Register-ScheduledTask -Force replaces).
#
# Usage (from Linux host via SSH):
#   ssh user@vm "powershell -ExecutionPolicy Bypass -File C:\tmp\mosdat_uia_setup_daemon.ps1"
#
# Optional params:
#   -WorkerPath  path to worker.py on the VM (default C:\tmp\mosdat_uia_worker.py)
#   -Port        TCP port the daemon binds on 127.0.0.1 (default 5555)
#
# What this does that the per-op schtasks transport did NOT:
#   * Uses pythonw.exe (NOT python.exe) -- no cmd.exe console window flashes.
#   * Trigger is AtLogOn -- task fires once at user login and the daemon
#     stays up for the entire session.
#   * ExecutionTimeLimit PT0S = Windows Task Scheduler never kills the daemon.
#   * RestartCount 3 / RestartInterval 1m -- auto-recover if the daemon crashes.
#   * Start-ScheduledTask kicks it immediately so we don't have to log out.

param(
    [string]$WorkerPath = "C:\tmp\mosdat_uia_worker.py",
    [int]$Port = 5555
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------- locate pythonw
$pythonw = $null
$cmd = Get-Command pythonw -ErrorAction SilentlyContinue
if ($cmd) { $pythonw = $cmd.Source }
if (-not $pythonw) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\pythonw.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\pythonw.exe",
        "C:\Python313\pythonw.exe",
        "C:\Python312\pythonw.exe",
        "C:\Python311\pythonw.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { $pythonw = $p; break }
    }
}
if (-not $pythonw) {
    Write-Error "pythonw.exe not found in PATH or standard install paths"
    exit 1
}
Write-Host "[setup-daemon] pythonw: $pythonw"

if (-not (Test-Path $WorkerPath)) {
    Write-Error "worker not found at $WorkerPath -- scp it before running setup_daemon"
    exit 1
}

# ---------------------------------------------------------- task registration
$taskName = "MosdatUiaDaemon"
$user = "$env:COMPUTERNAME\$env:USERNAME"

$action = New-ScheduledTaskAction `
    -Execute $pythonw `
    -Argument "`"$WorkerPath`" --daemon --port $Port"

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user

$principal = New-ScheduledTaskPrincipal `
    -UserId $user `
    -LogonType Interactive `
    -RunLevel Limited

# ExecutionTimeLimit (New-TimeSpan -Hours 0) = PT0S = unlimited (TS won't kill us).
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "mOSdat UIA persistent daemon"

# -Force replaces any prior task with the same name (idempotent re-run).
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Write-Host "[setup-daemon] registered task '$taskName' for user $user"

# Stop any prior instance so the Start picks up the (possibly updated) worker.
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

# Also kill any stray pythonw running the worker so the new task spawns fresh.
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*mosdat_uia_worker*" } |
    ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
    }

Start-Sleep -Milliseconds 500
Start-ScheduledTask -TaskName $taskName
Write-Host "[setup-daemon] started task -- probing port $Port"

# ---------------------------------------------------------- verify listening
$deadline = (Get-Date).AddSeconds(30)
$ok = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
    try {
        $probe = New-Object System.Net.Sockets.TcpClient
        $iar = $probe.BeginConnect("127.0.0.1", $Port, $null, $null)
        $waited = $iar.AsyncWaitHandle.WaitOne(1000, $false)
        if ($waited -and $probe.Connected) {
            $probe.Close()
            $ok = $true
            break
        }
        $probe.Close()
    } catch {
        # not listening yet -- keep polling
    }
}

if ($ok) {
    Write-Host "DAEMON_LISTENING_PORT_$Port"
    exit 0
} else {
    Write-Error "Daemon failed to start listening on 127.0.0.1:$Port within 30s"
    exit 1
}
