# Schedule daily pre-market and post-market carousel generation
# Run this script ONCE to register the Windows Task Scheduler tasks.
# After that, posts generate automatically every weekday.

$python  = "C:\Users\g6666\Trading Algo\.venv\Scripts\python.exe"
$main    = "C:\Users\g6666\Trading Algo\Content Creation\main.py"
$workdir = "C:\Users\g6666\Trading Algo"
$pypath  = "C:\Users\g6666\Trading Algo\Content Creation"

# ── Pre-market: 8:15 AM Mon-Fri ────────────────────────────────────────────
$preAction  = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "`"$main`" --now pre" `
    -WorkingDirectory $workdir
$preTrigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "08:15AM"
$preEnv     = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName   "StocksWithGaurav_PreMarket" `
    -Action     $preAction `
    -Trigger    $preTrigger `
    -Settings   $preEnv `
    -RunLevel   Highest `
    -Force | Out-Null

# Set PYTHONPATH env for the task
$task = Get-ScheduledTask -TaskName "StocksWithGaurav_PreMarket"
$task.Actions[0].EnvironmentVariables = @{ PYTHONPATH = $pypath }
$task | Set-ScheduledTask | Out-Null

Write-Host "Pre-market task registered: 8:15 AM Mon-Fri" -ForegroundColor Green

# ── Post-market: 3:45 PM Mon-Fri ───────────────────────────────────────────
$postAction  = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "`"$main`" --now post" `
    -WorkingDirectory $workdir
$postTrigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "03:45PM"

Register-ScheduledTask `
    -TaskName   "StocksWithGaurav_PostMarket" `
    -Action     $postAction `
    -Trigger    $postTrigger `
    -Settings   $preEnv `
    -RunLevel   Highest `
    -Force | Out-Null

$task2 = Get-ScheduledTask -TaskName "StocksWithGaurav_PostMarket"
$task2.Actions[0].EnvironmentVariables = @{ PYTHONPATH = $pypath }
$task2 | Set-ScheduledTask | Out-Null

Write-Host "Post-market task registered: 3:45 PM Mon-Fri" -ForegroundColor Green
Write-Host ""
Write-Host "To remove tasks later:"
Write-Host "  Unregister-ScheduledTask -TaskName StocksWithGaurav_PreMarket -Confirm:`$false"
Write-Host "  Unregister-ScheduledTask -TaskName StocksWithGaurav_PostMarket -Confirm:`$false"
