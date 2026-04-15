Set-Location "C:\Users\g6666\Trading Algo"
$log = "C:\Users\g6666\Trading Algo\signal_history\archive_log.txt"
$ts  = Get-Date -Format "yyyy-MM-dd HH:mm"

Add-Content $log "[$ts] Archive started"

# 1. Pull latest so push won't conflict
git pull --rebase origin main >> $log 2>&1

# 2. Run archiver (last 30 days, deduped)
& "C:\Users\g6666\Trading Algo\.venv\Scripts\python.exe" "C:\Users\g6666\Trading Algo\scripts\archive_signals.py" --days 30 >> $log 2>&1

# 3. Commit + push only if signal_history changed
git add "C:\Users\g6666\Trading Algo\signal_history\" >> $log 2>&1
$changed = git status --porcelain "signal_history/"
if ($changed) {
    $dateTag = Get-Date -Format "yyyy-MM-dd"
    git commit -m "archive: auto signal history $dateTag" >> $log 2>&1
    git push origin main >> $log 2>&1
    Add-Content $log "[$ts] Committed and pushed"
} else {
    Add-Content $log "[$ts] Nothing new"
}
