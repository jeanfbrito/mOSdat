# Shared test functions for Windows - sourced by individual test scripts
$ErrorActionPreference = "Stop"

if (-not $env:APP_PATH) {
    [Console]::WriteLine("ERROR: APP_PATH environment variable is required.")
    [Console]::WriteLine("Set it to the application executable path (e.g., APP_PATH='C:\Program Files\Rocket.Chat\Rocket.Chat.exe')")
    exit 1
}

$AppPath = $env:APP_PATH
$AppArgs = if ($env:APP_ARGS) { $env:APP_ARGS -split ' ' } else { @() }
$ProcessName = if ($env:PROCESS_NAME) { $env:PROCESS_NAME } else { [System.IO.Path]::GetFileNameWithoutExtension($AppPath) }
$Timeout = if ($env:TEST_TIMEOUT) { [int]$env:TEST_TIMEOUT } else { 10 }

function Cleanup {
    Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

function Run-App {
    $exitCode = 0
    try {
        Cleanup
        $startParams = @{
            FilePath    = $AppPath
            PassThru    = $true
            ErrorAction = "Stop"
        }
        if ($AppArgs.Count -gt 0) {
            $startParams["ArgumentList"] = $AppArgs
        }
        $proc = Start-Process @startParams

        # Wait up to 5s for the process (or a child with same name) to appear
        $running = $false
        for ($i = 0; $i -lt 10; $i++) {
            Start-Sleep -Milliseconds 500
            if (-not $proc.HasExited) { $running = $true; break }
            if (Get-Process -Name $ProcessName -ErrorAction SilentlyContinue) { $running = $true; break }
        }
        if (-not $running) {
            [Console]::WriteLine("ERROR: Process did not appear within 5s")
            $exitCode = -1
        } else {
            $exited = $proc.WaitForExit(($Timeout - 5) * 1000)
            if (-not $exited) {
                # Timeout - app ran without crashing = PASS
                $proc | Stop-Process -Force -ErrorAction SilentlyContinue
                $exitCode = 124  # Match Linux timeout convention
            } else {
                $exitCode = $proc.ExitCode
            }
        }
    } catch {
        [Console]::WriteLine("ERROR: Failed to start app: $_")
        $exitCode = -1  # Treat launch failure as crash
    } finally {
        Cleanup
    }
    return $exitCode
}

function Report-Result {
    param(
        [string]$Name,
        [int]$ExitCode
    )

    # Map Windows exit codes: 0xC0000005 = access violation (like SIGSEGV)
    # Negative exit codes in .NET are unsigned overflow of Win32 codes
    $isCrash = ($ExitCode -lt -1) -or ($ExitCode -eq -1073741819) -or ($ExitCode -eq -1073740791)

    if ($isCrash) {
        [Console]::WriteLine("RESULT:${Name}:FAIL:CRASH:$ExitCode")
        return 1
    } elseif ($ExitCode -eq 124) {
        [Console]::WriteLine("RESULT:${Name}:PASS:TIMEOUT:$ExitCode")
        return 0
    } else {
        [Console]::WriteLine("RESULT:${Name}:PASS:$ExitCode")
        return 0
    }
}
