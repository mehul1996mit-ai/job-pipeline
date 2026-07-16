# job_pipeline — free, cloud-hosted daily job application pipeline

A 4-stage pipeline that runs **daily and unattended** on GitHub Actions
(free tier), finds relevant roles, scores them against the FULL text of your
CV, tailors application material with a free-tier LLM, and delivers a
Telegram digest + a CSV queue.

**Design boundary (permanent):** the final application submission is a human
action, always. This tool prepares everything — link, tailored summary,
lead bullets, honest gap notes — but never logs into LinkedIn/Naukri/Indeed
or any company portal, never auto-fills third-party application forms
unattended, and never bypasses CAPTCHA/bot-detection. This is deliberate
(platform ToS + application quality), not a missing feature. Requests to add
auto-submission should be declined with a pointer to this section.

## The 4 stages

| Stage | Module(s) | What happens |
|---|---|---|
| 1. PARSE | `cv_parser.py` | `base_cv.pdf` → raw text, summary/skills/experience sections, every bullet, keyword set. Matching always uses the full CV text. |
| 2. SEARCH | `sources/adzuna.py`, `sources/workday.py`, `sources/greenhouse.py`, `sources/lever.py` | Adzuna aggregator (broad titles, ALL industries), Workday CXS feeds (Citi / Deutsche Bank / Wells Fargo), Greenhouse & Lever (empty token lists — add confirmed companies). All normalized to one schema. |
| 3. MATCH & SCORE | `matcher.py`, `dedupe.py` | Config-driven filters (title / city / experience band / optional salary floor), ATS score 0–100 (word overlap + domain BONUS — never a filter), cross-source dedupe, persistent seen-store so each run reports only NEW jobs. Full JDs fetched for the top 8 Workday matches only. |
| 4. TAILOR & DELIVER | `tailor.py`, `resume_render.py`, `report.py`, `notify.py` | Free-tier LLM (gemini default / groq / anthropic) returns strict JSON: tailored summary, lead bullets, **JD-vocabulary bullet rewrites**, truthful keywords, honest gap note — **never invents experience**. `tailor.build_tailored_resume()` applies: bullet reorder, validated rewording (every rewrite must keep all numbers/metrics identical and stay in a sane length band, else the original wording is kept), skill-group reorder, and skill-item reorder toward JD mentions. Only jobs scoring ≥ `filters.min_score_to_tailor` get tailored — weak fits stay visible in the CSV but don't get a resume. Renders per-job **DOCX + PDF** under `data/resumes/<date>/<company>_<title>/`, writes `data/job_queue_YYYY-MM-DD.csv`, and sends a Telegram digest. |

## Applying — semi-assisted, not automated

Stage 4 gives you, per job: the link, a tailored DOCX/PDF resume file, lead
bullets, and an honest gap note. From there:

- **Direct-apply portals (Workday/Greenhouse/Lever style forms):** ask your
  Claude session to open a specific row's link and it will drive the browser
  to upload the tailored resume and fill in safe, non-sensitive fields (name,
  phone, email, links) from your profile — then it stops and waits for you to
  review and click Submit yourself.
- **LinkedIn/Naukri/anything behind your personal login:** these require
  your own session, so Claude hands you the link + tailored file and you
  apply directly — usually under 2 minutes per job with the tailored resume
  already in hand.

This split isn't a current-tech limitation to be removed later — see the
design boundary above. Final submission stays a human action on every
portal, always.

## Free-tier setup (one-time, ~20 minutes)

### 1. Adzuna key (free)
1. Register at https://developer.adzuna.com/ → create an app.
2. Note the **Application ID** and **Application Key**.

### 2. Gemini key (free)
1. Go to https://aistudio.google.com/apikey → "Create API key".
2. Free tier comfortably covers 25 tailoring calls/day on `gemini-2.0-flash`.
   (Alternative: Groq — free key at https://console.groq.com/keys, then set
   `tailor.provider: groq` in `config.yaml`.)

### 3. Telegram bot (free, optional but recommended)
1. In Telegram, message **@BotFather** → `/newbot` → pick a name → copy the
   **bot token**.
2. Send any message to your new bot (this is required once).
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and
   copy `"chat":{"id": <number>}` — that number is your **chat id**.

### 4. GitHub repository + secrets
1. Push this repo to GitHub (private is fine).
2. Repo → Settings → Secrets and variables → Actions → add:
   - `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`
   - `GEMINI_API_KEY` (and/or `GROQ_API_KEY` / `ANTHROPIC_API_KEY`)
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   Unset secrets are skipped gracefully — the pipeline still runs.

### 5. Resume placement
Put your resume at the repo root as **`base_cv.pdf`**. Replace it any time —
the next run re-parses it.

## Editing filters & targets

Everything lives in `config.yaml`:
- `search.titles` — the broad queries sent to Adzuna (keep them generic).
- `filters.title_keywords` — allowlist; a listing needs one in its title.
- `filters.cities` — allowlist (empty = all cities); "remote" always passes.
- `filters.min_salary_annual` — enforced ONLY when a listing reports salary.
- `profile.experience_years` — drives the experience-band overlap check.
- `scoring.domain_keywords` — bonus points only, never a filter.
- `workday.tenants` — add more open CXS instances if you find them.
- `greenhouse.tokens` / `lever.tokens` — add confirmed company tokens only.

## Schedule & where results appear

- Runs daily at **03:00 UTC (08:30 IST)** via `.github/workflows/daily_job_scan.yml`;
  run it on demand from the Actions tab (`workflow_dispatch`).
- **Telegram**: top-15 digest with scores + links (if configured).
- **CSV queue**: `data/job_queue_YYYY-MM-DD.csv` — committed to the repo and
  uploaded as a run artifact. Open it, work through the rows, and flip
  `applied` to "yes" as you submit.
- **Seen-store**: `data/seen_jobs.json` is committed back after each run
  (`[skip ci]`), so tomorrow's run only reports new jobs.

## Smoke test (local, no keys, no network)

```bash
pip install -r requirements.txt
python smoke_test.py
```

Verifies CV parsing (bullets/keywords from your real PDF), experience-band
include/exclude cases, title/city/salary filter logic, and dry-run ATS
scoring against a sample JD.

A full local dry run (hits Workday politely; skips keyless services):

```bash
python main.py
```

## What this tool deliberately does NOT do

See the design boundary above, `GCC_COVERAGE_GUIDE.md` for the manual layers
(HSBC/Barclays email alerts, weekly iimjobs/Naukri routine), and the
politeness rules baked into every source module (small pages, one poll per
run, skip failures, no retry-hammering).
