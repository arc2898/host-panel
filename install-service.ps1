$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  $Launcher = Get-Command py -ErrorAction SilentlyContinue
  if ($Launcher) { & py -3 -m venv (Join-Path $Root ".venv") }
  else { & python -m venv (Join-Path $Root ".venv") }
}
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Root "requirements.txt")
$PowerShell = (Get-Command powershell.exe).Source
$Loop = Join-Path $Root "service-loop.ps1"
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument ("-NoProfile -ExecutionPolicy Bypass -File `"{0}`"" -f $Loop) -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Task = New-ScheduledTask -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal
Register-ScheduledTask -TaskName "HostPanel" -InputObject $Task -Force | Out-Null
Start-ScheduledTask -TaskName "HostPanel"
Write-Host "HostPanel scheduled to start at your Windows sign-in and restart after failures."
Write-Host "Local URL: http://127.0.0.1:8765"
Write-Host "For private remote access, install Tailscale on this PC and your other devices."
