# Run all Windows test scenarios
$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

[Console]::WriteLine("========================================")
[Console]::WriteLine("Windows Display Tests")
[Console]::WriteLine("========================================")
[Console]::WriteLine("App: $env:APP_PATH")
[Console]::WriteLine("Timeout: $($env:TEST_TIMEOUT)s")
[Console]::WriteLine("========================================")
[Console]::WriteLine("")

$pass = 0
$fail = 0

function Run-Test {
    param([string]$Script)
    $output = & powershell.exe -ExecutionPolicy Bypass -File "$ScriptDir\$Script" 2>&1
    $output | ForEach-Object { [Console]::WriteLine($_) }

    if ($output -match "RESULT:.*:PASS") {
        $script:pass++
    } else {
        $script:fail++
    }
    [Console]::WriteLine("")
}

# Check if GPU is attached (run GPU test only if present)
$hasGpu = $false
try {
    $nvsmi = & nvidia-smi 2>$null
    if ($LASTEXITCODE -eq 0) { $hasGpu = $true }
} catch {}

if (-not $hasGpu) {
    $wmiGpu = Get-CimInstance -ClassName Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "NVIDIA" }
    if ($wmiGpu) { $hasGpu = $true }
}

# Always run basic launch test
Run-Test "test-launch.ps1"

# Run GPU test only if GPU is present
if ($hasGpu) {
    Run-Test "test-gpu.ps1"
} else {
    [Console]::WriteLine("=== Skipping GPU test (no NVIDIA GPU detected) ===")
    [Console]::WriteLine("RESULT:gpu-launch:SKIP:NO_GPU")
    [Console]::WriteLine("")
}

[Console]::WriteLine("========================================")
[Console]::WriteLine("Summary: PASS=$pass FAIL=$fail")
[Console]::WriteLine("========================================")

if ($fail -gt 0) { exit 1 } else { exit 0 }
