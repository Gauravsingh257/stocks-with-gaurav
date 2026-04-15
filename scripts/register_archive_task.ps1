$python  = "C:\Users\g6666\Trading Algo\.venv\Scripts\python.exe"
$script  = "C:\Users\g6666\Trading Algo\scripts\archive_signals.py"
$workdir = "C:\Users\g6666\Trading Algo"
$helper  = "C:\Users\g6666\Trading Algo\scripts\archive_and_push.ps1"

$action   = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NonInteractive -ExecutionPolicy Bypass -File `"$helper`"" -WorkingDirectory $workdir
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "04:30PM"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -StartWhenAvailable -RunOnlyIfNetworkAvailable

Register-ScheduledTask -TaskName "SWG Archive Signals" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Description "Archives daily trade signals from Railway to GitHub" -Force | Out-Null
Write-Host "Task registered: SWG Archive Signals at 4:30 PM Mon-Fri" -ForegroundColor Green
