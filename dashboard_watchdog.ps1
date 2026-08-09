# Keeps the LOCAL dashboard both (a) running and (b) showing current data.
# Run every 5 minutes by the "JobPipelineDashboardWatchdog" Task Scheduler
# task (independent of any Claude Code session) so both survive without
# anyone noticing or intervening.
$repo = "C:\Claude\job_pipeline"
$port = 8502

# ---------------------------------------------------------------- data sync
# The pipeline runs in GitHub Actions and commits each day's queue back to
# origin/main. This local clone only ever saw that data when a human happened
# to run `git pull` -- and twice (2026-08-05, 2026-08-09) a clone that had
# silently drifted 4 days behind was reported as "the pipeline stopped
# running" when Actions had in fact succeeded every single day. The dashboard
# reads local files, so a stale clone IS a broken dashboard.
#
# --ff-only is deliberate and load-bearing: it can only ever move the clone
# forward to what the pipeline already pushed. If the user has local commits,
# or uncommitted edits to a queue CSV (the dashboard writes match_feedback /
# applied edits straight into those files), the merge REFUSES rather than
# rewriting or discarding their work. A refusal is not silently swallowed --
# it is written to the status file below, which the dashboard surfaces as a
# visible banner. Never auto-resolve here; losing a day of hand-entered
# ratings to an automatic merge is far worse than showing stale data loudly.
$syncStatus = "$repo\dashboard_sync.log"
try {
    git -C $repo fetch origin main --quiet 2>&1 | Out-Null
    $behind = (git -C $repo rev-list --count HEAD..origin/main 2>$null)
    if ($behind -and [int]$behind -gt 0) {
        $mergeOut = git -C $repo merge --ff-only origin/main 2>&1
        if ($LASTEXITCODE -eq 0) {
            $msg = "OK  fast-forwarded $behind commit(s) from origin/main"
        } else {
            $msg = "BLOCKED  $behind commit(s) behind origin/main; fast-forward refused (local commits or uncommitted edits to a file the pipeline also changed). Resolve by hand: git -C $repo status. Detail: $mergeOut"
        }
    } else {
        $msg = "OK  already current with origin/main"
    }
} catch {
    $msg = "ERROR  sync failed: $_"
}
# Timestamp format is deliberate, not cosmetic: PowerShell's round-trip 'o'
# format emits 7-digit fractional seconds, which Python's datetime
# .fromisoformat() rejects — the dashboard silently lost its "last checked N
# min ago" reading (caught 2026-08-09). That reading is what proves this
# watchdog is still ALIVE; without it a dead watchdog renders identically to
# a healthy one. Seconds precision, explicit offset, parses cleanly.
Set-Content -Path $syncStatus -Value "$((Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz'))  $msg" -Encoding utf8

# ------------------------------------------------------------ process check
$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Set-Location $repo
    Start-Process -FilePath "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe" `
        -ArgumentList "-m","streamlit","run","streamlit_app.py","--server.headless","true","--server.port","$port" `
        -WindowStyle Hidden `
        -RedirectStandardOutput "$repo\streamlit_out.log" `
        -RedirectStandardError "$repo\streamlit_err.log"
}
