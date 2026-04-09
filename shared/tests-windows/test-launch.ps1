# Test: Basic app launch - does it start without crashing?
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$ScriptDir\common.ps1"

Write-Host "=== Test: Basic Launch ==="
Write-Host "App: $AppPath"
Write-Host "Args: $($AppArgs -join ' ')"
Write-Host "Timeout: ${Timeout}s"

$code = Run-App
$result = Report-Result -Name "launch" -ExitCode $code
exit $result
