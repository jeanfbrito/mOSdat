# Test: GPU detection + app launch with GPU available
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$ScriptDir\common.ps1"

[Console]::WriteLine "=== Test: GPU Launch ==="

# Check for NVIDIA GPU
$gpu = $null
try {
    $gpu = & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null
} catch {}

if ($gpu) {
    [Console]::WriteLine "GPU: NVIDIA detected"
    [Console]::WriteLine "  $gpu"
} else {
    # Try checking via WMI
    $wmiGpu = Get-CimInstance -ClassName Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" }
    if ($wmiGpu) {
        [Console]::WriteLine "GPU: NVIDIA detected (via WMI)"
        [Console]::WriteLine "  $($wmiGpu.Name)"
    } else {
        [Console]::WriteLine "ERROR: No NVIDIA GPU detected"
        [Console]::WriteLine "RESULT:gpu-launch:FAIL:NO_GPU"
        exit 1
    }
}

[Console]::WriteLine "Starting app with GPU available..."
$code = Run-App
$result = Report-Result -Name "gpu-launch" -ExitCode $code
exit $result
