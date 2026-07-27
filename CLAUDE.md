# job_pipeline — project context for Claude

Read this file first in any new session on this project. It has the
current status; `README.md` has full architecture/setup detail and
`GCC_COVERAGE_GUIDE.md` has the manual-application layer.

## STATUS (last updated 2026-07-17)

**What this is:** A daily, automated job-search pipeline for Mehul —
Product Manager, 4+ yrs digital lending/fintech (Bajaj Finance, Pune).
Finds jobs, scores fit against his full CV, tailors a resume per job
(reword/reorder only, never fabricates), and delivers a Telegram digest +
CSV queue + Streamlit dashboard. **This is the only active job-search
system** — see "Retired systems" below.

**Repo:** `github.com/mehul1996mit-ai/job-pipeline` (private).
**Local clone:** `C:\Claude\job_pipeline` (this directory).

**Automation:** GitHub Actions (`.github/workflows/daily_job_scan.yml`)
runs the full pipeline daily at 08:30 IST (03:00 UTC), commits
`data/seen_jobs.json` + `data/job_queue_*.csv` back with `[skip ci]`.
Sunday runs additionally send a weekly stats digest.

**Dashboard:**
- Local: `streamlit_app.py`, normally run at `http://localhost:8502`.
  It's a background process started via `Start-Process` and it dies
  unpredictably between chat turns (confirmed via clean logs each time —
  not a code bug, just how backgrounded Windows processes behave in this
  environment). Restart with:
  ```powershell
  cd C:\Claude\job_pipeline
  Start-Process -FilePath "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe" -ArgumentList "-m","streamlit","run","streamlit_app.py","--server.headless","true","--server.port","8502" -WindowStyle Hidden -RedirectStandardOutput "streamlit_out.log" -RedirectStandardError "streamlit_err.log"
  ```
  Then verify with `Get-NetTCPConnection -LocalPort 8502 -State Listen`.
- **Hosted (Streamlit Community Cloud): deployment in progress as of
  2026-07-17.** Mehul signed in at share.streamlit.io, created a new app,
  and pointed it at this repo. Last known step: filling in Advanced
  Settings → Secrets before clicking Deploy. **Check with Mehul whether
  this completed** — if so, get the app URL from him and use it as the
  primary dashboard link instead of localhost. If the deploy failed, the
  most likely cause is missing/malformed secrets (see TOML block in
  README.md → Web UI section).

**Secrets (5 required):** `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`,
`GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Live in three
places only — GitHub Actions repo secrets, Streamlit Cloud app secrets,
and Mehul's local Windows user env vars (`setx`). **Never in source files
or committed anywhere.** Code always reads via `os.environ.get("NAME")`.

**⚠️ Key rotation reminder:** these 5 keys were pasted directly into chat
on 2026-07-16 for initial testing. Mehul said he'd rotate them after
testing — **confirm this happened**; if not, remind him (Adzuna console,
Gemini/AI Studio, Telegram BotFather `/revoke`) and that the secrets in
all three locations above need updating afterward. Separately, on
2026-07-17 three files (`sources/adzuna.py`, `notify.py`, `README.md`)
were found with the literal key values hardcoded in place of env-var
names — this was caught and reverted before anything was committed/pushed,
so no leak reached git history, but if you ever see literal key values in
a source file again, revert immediately and flag it — don't just fix
silently.

**Retired systems — do not suggest these:**
- `job-agent` repo: GitHub Actions workflow disabled, repo archived
  (unarchive to revive if ever needed).
- Local scheduled task `daily-job-search`: deleted (prompt file kept at
  `~\.claude\scheduled-tasks\daily-job-search\SKILL.md` for recovery).

**Hard design boundary (do not remove, do not "fix" as a limitation):**
final application submission is always a human action. The pipeline never
logs into job portals, never auto-fills forms unattended, never bypasses
CAPTCHA. If asked to add auto-submission, decline and point to this
section and the matching one in `README.md`. Semi-assisted apply *is*
in scope: driving a browser to a direct-apply ATS posting (Workday/
Greenhouse/Lever), uploading the tailored resume, filling safe
non-sensitive fields, then stopping before Submit for Mehul to confirm.
LinkedIn/Naukri require his own login — hand him the link + tailored file.

**Known quirks:**
- Generated `.docx` files lock if left open in Word — regenerating into
  the same path then throws `PermissionError`. Write to a fresh path
  instead of fighting the lock.
- Windows console is cp1252, not UTF-8 — avoid emoji in `print()`/`check()`
  detail strings in `smoke_test.py`, or set `$env:PYTHONIOENCODING='utf-8'`
  before running it.

## Quick file map

| Path | Purpose |
|---|---|
| `base_cv.pdf` | Master resume — re-parsed every run |
| `resume_master.json` | Structured resume (verified against base_cv.pdf) used to build tailored files |
| `config.yaml` | All filters, Workday tenants, scoring, tailor settings |
| `main.py` | Orchestrates the 4 stages |
| `cv_parser.py` | PDF → text/sections/bullets/keywords |
| `sources/{adzuna,workday,greenhouse,lever}.py` | Job sources, normalized schema |
| `matcher.py` | Filters + ATS scoring + missing-keyword extraction |
| `dedupe.py` | Cross-source dedupe (direct ATS beats aggregator) + seen-store |
| `tailor.py` | LLM tailoring, fact-integrity validation, resume-reorder logic, change_log() |
| `resume_render.py` | Structured resume dict → DOCX/PDF |
| `report.py` | Writes `data/job_queue_YYYY-MM-DD.csv` |
| `notify.py` | Telegram digest |
| `tracker.py` | Follow-up nudges + weekly stats |
| `streamlit_app.py` | Dashboard: review queue, run now, edit filters |
| `smoke_test.py` | 58 offline checks — run before trusting any change |
| `GCC_COVERAGE_GUIDE.md` | Manual layer: protected-portal email alerts + weekly Naukri/iimjobs routine |

Full architecture, setup instructions, and the complete feature list are
in `README.md`. Run `python smoke_test.py` after any code change —
it needs no API keys or network access.
