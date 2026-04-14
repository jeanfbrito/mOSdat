# Test: Basic app launch - does it start without crashing?
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$ScriptDir\common.ps1"

[Console]::WriteLine("=== Test: Basic Launch ===")
[Console]::WriteLine("App: $AppPath")
[Console]::WriteLine("Args: $($AppArgs -join ' ')")
[Console]::WriteLine("Timeout: ${Timeout}s")

$code = Run-App
$result = Report-Result -Name "launch" -ExitCode $code
exit $result
