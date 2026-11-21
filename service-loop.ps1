$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
Set-Location $Root
while ($true) {
  & $Python (Join-Path $Root "start.py")
  Start-Sleep -Seconds 5
}
