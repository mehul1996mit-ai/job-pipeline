# Restarts the local Streamlit dashboard if it isn't listening on 8502.
# Run on a schedule by the "JobPipelineDashboardWatchdog" Task Scheduler
# task (independent of any Claude Code session) so the dashboard survives
# crashes without anyone noticing or intervening.
$port = 8502
$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Set-Location "C:\Claude\job_pipeline"
    Start-Process -FilePath "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe" `
        -ArgumentList "-m","streamlit","run","streamlit_app.py","--server.headless","true","--server.port","$port" `
        -WindowStyle Hidden `
        -RedirectStandardOutput "C:\Claude\job_pipeline\streamlit_out.log" `
        -RedirectStandardError "C:\Claude\job_pipeline\streamlit_err.log"
}
