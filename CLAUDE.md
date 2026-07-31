# job_pipeline — project context for Claude

Read this file first in any new session on this project. It has the
current status; `README.md` has full architecture/setup detail and
`GCC_COVERAGE_GUIDE.md` has the manual-application layer.

## STATUS (last updated 2026-07-31)

**✅ Session wrap-up 2026-07-31 — SerpApi confirmed live in production,
dashboard watchdog added, queue-overwrite bug fixed.** Three things closed
out this session, in order:
1. **SerpApi confirmed working end-to-end** in the actual GitHub Actions
   environment (not just locally) via a manual `gh workflow run` trigger —
   log showed `serpapi: 99 listings (15/250 calls used this month)`, quota
   tracker persisting correctly across runs via the committed
   `data/serpapi_usage.json`.
2. **Local Streamlit dashboard (localhost:8502) kept dying between chat
   turns** because it was only ever started as a background process tied to
   the current session — not a real "always on" mechanism. Fixed with a
   Windows Task Scheduler job (`JobPipelineDashboardWatchdog`, runs
   `dashboard_watchdog.ps1` every 5 min, checks port 8502, restarts if down).
   Verified live: killed the process, triggered the task, it came back within
   seconds. Caveat: only runs while logged into Windows (the `on-logon`
   trigger hit a UAC deny-only restriction in this shell and couldn't be
   registered — the 5-min recurring trigger covers the gap with up to a
   5-minute delay after boot/login). For a dependency-free "always up," the
   hosted dashboard (https://job-1357.streamlit.app/) remains the better fit.
3. **`report.write_queue()` was overwriting, not appending, same-day runs** —
   discovered when a manual re-trigger (to verify #1) silently destroyed the
   morning's real 61-job queue, leaving only that afternoon's incremental 15.
   Fixed: it now reads back existing rows (preserving any `applied`/
   `match_feedback` edits already made in the dashboard) and only appends
   genuinely new URLs, re-sorting by score. Verified with a scripted
   run-edit-run-again test before committing. **This morning's 61 clobbered
   jobs are not recoverable from the CSV** (they were still tailored, sent in
   the Telegram digest, and sit in that run's GitHub Actions artifact) — going
   forward, same-day re-triggers append instead of destroy.

**Baseline note:** 2026-07-31's scheduled run (`30627617285`) was the first
run all week with exactly one execution that day — 24 new jobs. Don't read
that as a volume drop: the seen-store sat at 652 entries by then, saturated
from this week's repeated testing (four runs 07-29, two runs 07-30), so most
of that day's ~500 filtered matches were already-seen postings, not evidence
of fewer real listings. Raw source counts (Adzuna 1650, Greenhouse 2350,
SerpApi 99, etc.) were consistent with every other run this week. Treat
2026-08-01 onward as the first clean baseline for judging daily volume.

**🐛 Bug found and fixed 2026-07-30 — SerpApi silently skipped its first
scheduled run.** The 2026-07-30 08:30 IST run completed successfully (61 new
jobs, 23 tailored, seen-store/commit-back all fine) but logged `serpapi:
SERPAPI_KEY not set — skipping` even though the secret existed in `gh secret
list`. Root cause: when `sources/serpapi_jobs.py` and the `SERPAPI_KEY`
secret were added 2026-07-29, the workflow's commit-back step was updated to
handle `data/serpapi_usage.json`, but the `env:` block that actually feeds
`python main.py` (`.github/workflows/daily_job_scan.yml`) was never given a
`SERPAPI_KEY: ${{ secrets.SERPAPI_KEY }}` line — same class of gap as the
Adzuna/Gemini/Telegram secret issues found earlier in the week, different
mechanism (missing wiring, not a corrupted value). Fixed same day. **If any
newly-added source/secret ever "isn't set" in a live run despite `gh secret
list` showing it, check the workflow's `env:` block before assuming the
secret itself is bad** — this is the second distinct way a correctly-set
secret has failed to reach the running script.

**⚠️ Read before judging any volume/yield number from 2026-07-29.** `main.py`
was run FOUR times that day (env-var debugging, then testing the new title
list, then testing the new SerpApi source) — each run adds that day's
qualifying jobs to `data/seen_jobs.json`, so by the fourth run almost
everything had already been marked seen. That day's "9 new jobs" and its
`seen_jobs.json`/`job_queue_2026-07-29.csv` entry counts are testing-inflated,
not a real day's volume — don't compare a future day against them or read
them as a regression. Judge volume against a day with exactly one scheduled
run instead.

**New job-title list + SerpApi source added 2026-07-29** (see below in this
same STATUS block for the reasoning) — `config.yaml`'s `search.titles` grew
from 9 generic guesses to 17 skill-cluster-derived titles, `filters
.title_keywords` grew to match (12 entries), and a new `sources
/serpapi_jobs.py` (Google Jobs via SerpApi, gated behind `SERPAPI_KEY` +
`serpapi.enabled: true`) covers Naukri/LinkedIn/Indeed indirectly through
Google's job index. SerpApi quota is tracked in `data/serpapi_usage.json`
(committed back by the workflow, same as `seen_jobs.json`) so the monthly
250-search free-tier cap persists across daily Actions runs instead of
resetting on every fresh checkout. `serpapi.max_pages_per_title: 2` (20
results/title across the 5-title `search.serpapi_titles` subset) runs
~300 calls/month against that 250 cap — a deliberate choice Mehul made
knowing it exceeds the free tier some months; the `quota_buffer` guard stops
calling early and logs it rather than erroring, so this fails safe (fewer
results late in the month), not broken.

**✅ RESOLVED 2026-07-29 — all 5 GitHub Actions secrets now set and verified
working.** Mehul added `GEMINI_API_KEY`/`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
the same way he'd added Adzuna (`gh secret set NAME --repo
mehul1996mit-ai/job-pipeline --body "value"`, from his own terminal — never
by me; entering API keys into any field is hard-prohibited, tested and
declined twice this session even on direct request). All three had the SAME
angle-bracket corruption as Adzuna (`<value>` instead of `value`) — same fix,
`setx` with brackets stripped. Verified via `gh secret list` (5/5 present)
and a live run (`30442980079`) showing all five load (`***` masked) and
Adzuna pulling 893 listings. **Still unverified: Streamlit Cloud's separate
copy of all 5** — check that too if the hosted dashboard's "Run now" ever
needs them.

**🐛 Second, independent bug found immediately after — also fixed
2026-07-29:** with real secrets finally in place, tailoring started failing
with `400 INVALID_ARGUMENT`, not "not set". Root cause, confirmed by
bisecting the actual request against the live API: `tailor.py`'s
`generationConfig.thinkingConfig` field (added historically to stop a
"thinking" model from burning its whole output budget on hidden reasoning)
is now REJECTED outright by whatever `gemini-flash-lite-latest` currently
resolves to (`gemini-3.5-flash-lite` — confirmed via the API's own
`modelVersion` field). So even with a correct key, **every tailoring call
was failing silently** (the existing 429/503-only retry logic treated the 400
as a plain, final failure). Fixed in `tailor.py::_call_gemini`: don't send
`thinkingConfig` by default; only retry WITH it if the base call returns
genuinely empty text (the actual symptom it was meant to fix) — self-healing
in either direction rather than assuming today's model behavior is permanent,
since Google rotates `-latest` aliases over time. Verified end-to-end with a
real `tailor_job()` call: real `tailored_summary`, real `honest_gap_note`,
real `jd_analysis`, all grounded in the CV. **If tailoring ever silently
breaks again, bisect the actual request against the live API first** (see
this session's method) rather than assuming it's a credentials issue — it
looked exactly like one until the key started working.

**Adzuna — root cause found and fixed 2026-07-28.** Two independent bugs, both
now fixed and confirmed live:
1. Local Windows env vars had the value literally wrapped in angle brackets
   (`<195aa6d0>` instead of `195aa6d0`) — a copy-paste artifact. Every local
   call 401'd. Fixed via `setx` with the brackets stripped.
2. GitHub Actions secrets for Adzuna were **completely empty** (not
   malformed — never set). Mehul added them via `gh secret set`.
Verified via a live triggered run (`30386095721`): Adzuna went from "not
set — skipping" to **888 listings**, pushing total raw listings across all
sources to 3,672 → 302 after filters → 287 genuinely new in one day. This is
almost certainly the real reason job volume looked thin for weeks — Adzuna is
the broad multi-portal aggregator this pipeline is built around, and it
had been contributing zero jobs, cloud-side, since setup.

**`tailor.top_n` and `digest.top_n` raised 25/15 → 50/50 (2026-07-28)**,
directly because of the volume jump above — 25 tailored/day was covering
under 10% of a 287-job day. Both slice the SAME score-sorted `new_jobs` list
in `main.py` (`new_jobs.sort(key=lambda j: j["score"], reverse=True)` runs
before either cut), so "top 50 tailored" and "top 50 in the digest" are the
identical 50 jobs — selection is by structured match score against the CV, as
requested. Measured cost before raising: 25 tailored jobs took 76s with zero
429/503 retries in the 2026-07-28 run; 50 should land ~2.5 min, far under the
30-min workflow timeout. Gemini's exact free-tier quota isn't verifiable from
here — this was a measured-safe increase, not a maxed-out one. Watch for
"tailor: gemini 429/503" retry lines before raising further. (Moot until the
GEMINI_API_KEY gap above is closed — right now 0 of the 50 succeed.)

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
  **Fixed 2026-07-31**: it used to die unpredictably between chat turns
  because it was only ever started as a background process tied to whatever
  session launched it — not a real "always on" mechanism. A Windows Task
  Scheduler job (`JobPipelineDashboardWatchdog`) now runs
  `dashboard_watchdog.ps1` every 5 minutes, checking port 8502 and restarting
  Streamlit if it's down. Verified live (killed the process, triggered the
  task, it came back in seconds). If it's ever down for longer than 5
  minutes, check the task itself: `Get-ScheduledTask -TaskName
  JobPipelineDashboardWatchdog` should show `State: Ready`; `Start-
  ScheduledTask -TaskName JobPipelineDashboardWatchdog` runs it immediately.
  Caveat: this only runs while logged into Windows — the `on-logon` trigger
  couldn't be registered (UAC deny-only restriction in this shell), so after
  a fresh boot/login there can be up to a 5-minute gap before the recurring
  trigger catches it.
- **Hosted (Streamlit Community Cloud): confirmed live as of 2026-07-27**
  at **https://job-1357.streamlit.app/** — use this as the primary
  dashboard link instead of localhost, especially when you need a guarantee
  independent of this machine being on/logged in. Verified via the apps list
  at share.streamlit.io (app `job-pipeline · main · streamlit_app.py`,
  public/no error badge); direct in-page verification was blocked by a
  browser-extension domain permission, so if data looks stale there,
  open it manually to double check.
- **Hosted (Streamlit Community Cloud): confirmed live as of 2026-07-27**
  at **https://job-1357.streamlit.app/** — use this as the primary
  dashboard link instead of localhost. Verified via the apps list at
  share.streamlit.io (app `job-pipeline · main · streamlit_app.py`,
  public/no error badge); direct in-page verification was blocked by a
  browser-extension domain permission, so if data looks stale there,
  open it manually to double check.

**Secrets (5 required + 1 optional):** `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`,
`GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, plus
`SERPAPI_KEY` (optional — only needed if `serpapi.enabled: true`; set and
verified working 2026-07-29/30). Live in three places only — GitHub Actions
repo secrets, Streamlit Cloud app secrets, and Mehul's local Windows user
env vars (`setx`). **Never in source files
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
- Declined and never build: any direct Naukri/LinkedIn/Indeed scraper — the
  reasoning above stands regardless of what else changes.
- `sources/serpapi_jobs.py` — **built 2026-07-29, confirmed live 2026-07-30.**
  SerpApi Google Jobs (paid tier, off by default) was offered as the
  legitimate-indexing alternative and Mehul took it up. See "Job sources" in
  README.md and the STATUS entries above for setup and quota details.

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
| `sources/serpapi_jobs.py` | Google Jobs via SerpApi — paid-tier Naukri/LinkedIn/Indeed coverage, off by default, quota-tracked in `data/serpapi_usage.json` |
| `dashboard_watchdog.ps1` | Restarts local Streamlit (port 8502) if down; run every 5 min by the `JobPipelineDashboardWatchdog` Windows Task Scheduler job |
| `feedback.py` | good/partial/no labels → proposed search + weight changes |
| `matcher.py` | Filters + ATS scoring + missing-keyword extraction |
| `dedupe.py` | Cross-source dedupe (direct ATS beats aggregator) + seen-store |
| `tailor.py` | LLM tailoring, fact-integrity validation, resume-reorder logic, change_log() |
| `resume_render.py` | Structured resume dict → DOCX/PDF |
| `report.py` | Writes `data/job_queue_YYYY-MM-DD.csv` |
| `notify.py` | Telegram digest |
| `tracker.py` | Follow-up nudges + weekly stats |
| `streamlit_app.py` | Dashboard: review queue, run now, edit filters |
| `smoke_test.py` | 127 offline checks — run before trusting any change |
| `GCC_COVERAGE_GUIDE.md` | Manual layer: protected-portal email alerts + weekly Naukri/iimjobs routine |

Full architecture, setup instructions, and the complete feature list are
in `README.md`. Run `python smoke_test.py` after any code change —
it needs no API keys or network access. (`scratch_*.py` files if you ever see
one are one-off verification scripts — delete after use, never commit.)
