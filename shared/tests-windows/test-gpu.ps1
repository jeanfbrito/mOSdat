# Test: GPU detection + app launch with GPU available
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$ScriptDir\common.ps1"

Write-Host "=== Test: GPU Launch ==="

# Check for NVIDIA GPU
$gpu = $null
try {
    $gpu = & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null
} catch {}

if ($gpu) {
    Write-Host "GPU: NVIDIA detected"
    Write-Host "  $gpu"
} else {
    # Try checking via WMI
    $wmiGpu = Get-CimInstance -ClassName Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" }
    if ($wmiGpu) {
        Write-Host "GPU: NVIDIA detected (via WMI)"
        Write-Host "  $($wmiGpu.Name)"
    } else {
        Write-Host "ERROR: No NVIDIA GPU detected"
        Write-Host "RESULT:gpu-launch:FAIL:NO_GPU"
        exit 1
    }
}

Write-Host "Starting app with GPU available..."
$code = Run-App
$result = Report-Result -Name "gpu-launch" -ExitCode $code
exit $result
