# job_pipeline — project context for Claude

Read this file first in any new session on this project. It has the
current status; `README.md` has full architecture/setup detail and
`GCC_COVERAGE_GUIDE.md` has the manual-application layer.

## STATUS (last updated 2026-07-27)

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

**⚠️ Bug fixed 2026-07-27 — verify it stuck:** the commit-back step had
been silently no-oping every day since 2026-07-16 (`git add
data/seen_jobs.json data/followups.json data/job_queue_*.csv || true`
failed whole-command whenever `followups.json` didn't exist yet, so
nothing staged, but `|| true` masked it and the run still reported
"success"). Fixed in commit `b50ed34` by splitting the `followups.json`
add into a conditional. **Check the next scheduled run's "Commit
seen-store back to the repo" step** — it should say "Update seen-store &
queue ..." instead of "Nothing to commit." If it's still not committing,
look there first.

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
- **Hosted (Streamlit Community Cloud): confirmed live as of 2026-07-27**
  at **https://job-1357.streamlit.app/** — use this as the primary
  dashboard link instead of localhost. Verified via the apps list at
  share.streamlit.io (app `job-pipeline · main · streamlit_app.py`,
  public/no error badge); direct in-page verification was blocked by a
  browser-extension domain permission, so if data looks stale there,
  open it manually to double check.

**Secrets (5 required):** `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`,
`GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Live in three
places only — GitHub Actions repo secrets, Streamlit Cloud app secrets,
and Mehul's local Windows user env vars (`setx`). **Never in source files
or committed anywhere.** Code always reads via `os.environ.get("NAME")`.

**Key rotation — decided against, 2026-07-27:** these 5 keys were pasted
directly into chat on 2026-07-16 for initial testing. Mehul reviewed the
tradeoff (low-privilege free-tier keys — job-search API quota + bot
token, not financial/PII access) and chose to keep using the original
keys rather than rotate. **Do not re-raise this as an open item** unless
something changes (e.g. suspected misuse, quota abuse, or Mehul asks).
Separately, on 2026-07-17 three files (`sources/adzuna.py`, `notify.py`,
`README.md`) were found with the literal key values hardcoded in place of
env-var names — this was caught and reverted before anything was
committed/pushed, so no leak reached git history, but if you ever see
literal key values in a source file again, revert immediately and flag
it — don't just fix silently.

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

**Scoring stack (ported from `cv-match-copilot-gemini`, 2026-07-28).** The old
scorer was a flat JD-vs-CV word overlap. It's been replaced by a port of the
Chrome extension's engine, in Python, in three layers — all **deterministic**,
so every job gets all three at zero API cost:

1. `scoring_core.py` — the frozen formula: light stemmer + synonym folding,
   requirement lines weighted ×2, **bigrams are separate competencies** (having
   "credit" and "risk" apart does NOT match "credit risk"), per-term weight cap
   8, `base = sqrt(coverage) × 80`, domain bonus +5/keyword capped at 20 and
   **never a filter**.
2. `cv_structure.py` + `skill_match.py` — structured CV (roles, tenure via
   interval union, education-explained gaps, declared-vs-demonstrated skills)
   and layered skill matching that weights a must-have you can evidence in a
   bullet above a keyword you merely listed.
3. `aggregate.py` + `calibrate.py` — six sub-scores → structured score, minus
   penalties, with a hard cap when a *checkable* eligibility gate fails; plus a
   percentile calibrated against how demanding the posting itself is, and
   impact-ranked counterfactual gaps ("closing this moves you 62 → 78").

`matcher.score_job()` is the entry point; `matcher.ats_score()` is the OLD
scorer, kept only for formula comparison — don't rank on it.

**Two guards in `smoke_test.py` are standing, not ordinary unit tests** (same
convention as the source repo): the **acceptance regression** (a credit-risk JD
must beat a marketing JD by >25 points while marketing still scores nonzero)
and the **fairness audit** (an education-explained gap, a single-role CV, a
step down in seniority, and a JD stating no minimum must never be scored near
zero; an unverifiable gate is flagged for review, never auto-failed). If either
fails, **fix the scorer, never the tolerance.**

**Score floor recalibrated 55 → 50 (2026-07-28).** The old 55 was silently
starving the pipeline — on the real 2026-07-16..27 queues only **1 of 12** jobs
cleared it, so almost nothing was ever tailored (the dashboard's "Tailored: 0"
was this, not a bug). Under the new structured score, 50 qualifies 7/12.
Caveat: that measurement used `description_snippet`, which is truncated or
empty for many rows — full JDs are only fetched for the top 8 Workday matches,
so real scores run a little higher. Re-measure before changing the floor; the
distribution is formula-specific.

**JD analyst — why there are two.** The structured layer needs must-have vs
preferred requirements. The extension called an LLM per posting because it only
ever sees one page; this pipeline sees hundreds of listings a day, so a call
per job would exhaust the Gemini free tier. So `jd_analyst.analyze_jd()` is a
deterministic regex/clause extractor that runs on everything, and the
**tailoring call for the top-N jobs also returns `jd_analysis`** — same call,
extra JSON fields, zero additional API cost — which is merged over the
deterministic read via `merge_llm_analysis()`. An empty or failed LLM analysis
never blanks out a real regex finding, and `analyst` records which produced the
result so a deterministic read is never presented as a model's.

**Portal coverage — asked for, and how it was answered (2026-07-28).** Mehul
asked for "all top-25 Indian job portals including Naukri, LinkedIn, Indeed".
Those are **still not scraped**, and shouldn't be: it breaches their terms and
risks bans on the very accounts he job-hunts with (Naukri recruiter inbound is
worth more than the listings), and their bot protection makes scrapers a
permanent maintenance tax on an unattended pipeline. Instead:
- `sources/job_alert_email.py` — the real answer. Those portals all EMAIL job
  alerts; his own inbox is his own data. Read-only IMAP (`BODY.PEEK`, never
  marks read, never sends/deletes), parses posting links out of the alert
  mails. Off by default; needs `JOB_ALERT_EMAIL` + `JOB_ALERT_APP_PASSWORD`
  (a Gmail **app password**, never the account password) and
  `job_alert_email.enabled: true`. Emails carry a link but no JD, so these
  score on title alone and rank accordingly — that's expected, not a bug.
- `sources/smartrecruiters.py`, `sources/ashby.py` — public no-auth ATS feeds,
  joining Workday/Greenhouse/Lever. Token/board lists ship EMPTY; add only
  confirmed ones.
- Declined and not built: any direct Naukri/LinkedIn/Indeed scraper. Offered
  but not taken up: SerpApi Google Jobs (paid, legitimately indexes those
  portals). If Mehul asks again, this is the tradeoff to re-present — don't
  just build the scraper.

**`greenhouse.tokens`/`lever.tokens`/`smartrecruiters.companies` populated
2026-07-28** with 10 verified tokens (Razorpay, Stripe, Coinbase, Databricks,
MongoDB, Okta, PhonePe, CRED, Meesho, ixigo). **Method — repeat this, don't
guess slugs:** hit the real public API for each candidate (`GET
boards-api.greenhouse.io/v1/boards/{token}/jobs`, `api.lever.co/v0/postings/
{token}?mode=json`, `api.smartrecruiters.com/v1/companies/{token}/postings`,
`api.ashbyhq.com/posting-api/job-board/{board}`), then check the RETURNED
locations for a genuine India city/country string — a 200 response alone
proves nothing, most tested companies (Groww, Zeta, Airbnb, Brex, Pinterest,
Postman, and ~30 more) return valid JSON with zero actual India PM/BA
postings. `ashby.boards` ships EMPTY on the same basis — none of ~40 checked
companies (Notion, Ramp, Cursor, Vanta, Harvey, Replit, Zapier, Deliveroo, and
more) had one. Re-check periodically; this is a snapshot, not a permanent
fact about these companies.

**Two real bugs found while verifying, both fixed:**
1. `sources/ashby.py` joined `secondaryLocations` as if it were a list of
   strings — it's actually a list of `{"location": ..., "address": {...}}`
   objects, so real usage would `TypeError` and silently drop every
   multi-location posting.
2. `matcher.city_ok()`'s "remote always passes" rule let through region-locked
   remote roles from global boards — "Chicago, IL, Remote", "US-Remote",
   "Remote (Canada)" all passed the India-only cities filter. Fixed by
   stripping filler words/separators from a "remote" location string and
   requiring the residual be empty (or say India) rather than pattern-matching
   a finite list of country names, which would have missed bare US city names
   like "Chicago"/"Seattle" anyway. A bare "Remote" or "Remote, India" still
   passes.

**Learning loop (`feedback.py`).** The queue CSV has a `match_feedback` column
(good/partial/no) you set in the dashboard's Learning tab. Once there are
**25+ labels with 5+ in each class**, it proposes: title keywords to drop
(70%+ rejection rate, min 6 samples) and re-weighted sub-scores (point-biserial
correlation of each sub-score against your 'good' labels). **It never
auto-applies** — the dashboard shows a proposal with an explicit accept button
that rewrites `config.yaml`. Two invariants hold simultaneously in
`propose_weights`: no weight moves more than ±25% relative per batch, and the
set still sums to its original total (clamp and renormalize are alternated
until both settle — doing either once breaks the other). `partial` is excluded
from the correlation rather than mapped to 0.5; forcing the ambiguous middle
onto a binary axis is how a weak signal becomes a confident wrong answer. The
loop also self-checks first: `score_separation()` reports whether the score
actually separates your good matches from your bad ones, because re-weighting
a scorer that doesn't separate is rearranging deck chairs.

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
| `cv_structure.py` | Structured CV: roles, tenure, gaps, declared-vs-demonstrated skills |
| `scoring_core.py` | Frozen match engine (stemmer, synonyms, bigrams, sqrt curve) |
| `skill_match.py` | Layered skill matching (tier/source/recency/depth) |
| `aggregate.py` | 6 sub-scores → structured score, penalties, eligibility gates |
| `calibrate.py` | Percentile vs JD difficulty, counterfactual gaps, explainability |
| `jd_analyst.py` | JD requirement extraction (deterministic + LLM merge) |
| `sources/{adzuna,workday,greenhouse,lever,smartrecruiters,ashby}.py` | Job sources, normalized schema |
| `sources/job_alert_email.py` | Read-only IMAP ingest of Naukri/LinkedIn/Indeed alert emails |
| `feedback.py` | good/partial/no labels → proposed search + weight changes |
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
