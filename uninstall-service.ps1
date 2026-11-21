$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName "HostPanel"
Unregister-ScheduledTask -TaskName "HostPanel" -Confirm:$false
Write-Host "HostPanel startup task removed. Password and application data were kept."
