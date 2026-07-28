# job_pipeline — free, cloud-hosted daily job application pipeline

> **Starting a new session on this project?** Read [`CLAUDE.md`](CLAUDE.md)
> first — it has current deployment status, key-rotation state, and
> operational notes that this file doesn't cover.

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
| 2. SEARCH | `sources/adzuna.py`, `sources/workday.py`, `sources/greenhouse.py`, `sources/lever.py` | Adzuna aggregator (broad titles, ALL industries) + Workday CXS feeds for 9 employers (Citi, Deutsche Bank, Wells Fargo, Mastercard, PayPal, State Street, BlackRock, Adobe, Salesforce — all probed and confirmed publicly open) + Greenhouse & Lever (empty token lists — add confirmed companies). Cross-source duplicates keep the DIRECT employer-ATS link over aggregator redirects. All normalized to one schema. |
| 3. MATCH & SCORE | `matcher.py`, `dedupe.py`, `cv_structure.py`, `scoring_core.py`, `skill_match.py`, `aggregate.py`, `calibrate.py`, `jd_analyst.py` | Config-driven filters (title / city / experience band / optional salary floor), then a three-layer **deterministic** fit score (see below), cross-source dedupe, persistent seen-store so each run reports only NEW jobs. Full JDs fetched for the top 8 Workday matches only. |
| 4. TAILOR & DELIVER | `tailor.py`, `resume_render.py`, `report.py`, `notify.py` | Free-tier LLM (gemini default / groq / anthropic) returns strict JSON: tailored summary, lead bullets, **JD-vocabulary bullet rewrites**, truthful keywords, honest gap note — **never invents experience**. `tailor.build_tailored_resume()` applies: bullet reorder, validated rewording (every rewrite must keep all numbers/metrics identical and stay in a sane length band, else the original wording is kept), skill-group reorder, and skill-item reorder toward JD mentions. Only jobs scoring ≥ `filters.min_score_to_tailor` get tailored — weak fits stay visible in the CSV but don't get a resume. Renders per-job **DOCX + PDF** under `data/resumes/<date>/<company>_<title>/`, writes `data/job_queue_YYYY-MM-DD.csv`, and sends a Telegram digest. |

## Fit scoring — three layers, no API calls

Ported from the CV Match Copilot Chrome extension (2026-07-28). Every layer is
deterministic, so every listing gets scored at zero API cost.

**1. Frozen keyword engine (`scoring_core.py`).**
`score = base(0–80) + domain bonus(0–20)`, `base = round(sqrt(coverage) × 80)`.
Tokens are canonicalized before matching (light stemmer: modelling/models →
model; synonym folding: js → javascript), requirement-looking JD lines weigh
×2, and **consecutive-word bigrams are separate competencies at ×2 that only
match the CV's own bigrams** — having "credit" and "risk" in different
sentences does not count as "credit risk". Per-term weight cap 8. The sqrt
curve exists because real JDs rarely exceed ~60% raw coverage, so linear
scaling pins every honest score in the low band. The domain bonus is additive
only and **never a filter** — strong matches from other industries are never
hidden.

**2. Structured CV + layered skill match (`cv_structure.py`, `skill_match.py`).**
The CV is parsed into roles, tenure (interval *union*, so overlapping roles
aren't double-counted), employment gaps, education, and — kept deliberately
separate — **declared** skills (your Skills section) vs **demonstrated** ones
(those that also appear in an experience bullet). Skills then match in layers
(exact > alias > stem > phrase) and are weighted by requirement tier (must-have
3× vs preferred 1×), source (demonstrated 1.0 vs declared-only 0.5 — an
anti-gaming discount), recency decay, and depth of evidence. A CV that mirrors
the posting's exact phrasing is flagged as possible keyword stuffing.

**3. Sub-scores, penalties, calibration (`aggregate.py`, `calibrate.py`).**
Six sub-scores — skill match, experience fit, education, domain, achievement
density, trajectory — blended by configurable weights, minus penalties for
unexplained gaps and verbatim mirroring. A **checkable** eligibility gate that
fails (a required degree the CV lacks) hard-caps the score, because skill
overlap doesn't outscore ineligibility; an **unverifiable** gate (visa,
clearance, licence) is *flagged for human review, never auto-failed*. Finally
the score is calibrated into a percentile against how demanding that specific
posting is, and unmet requirements are ranked by **score impact** rather than
by how often the JD repeats a word.

**Fairness is a standing test, not a footnote.** `smoke_test.py` permanently
asserts that an education-explained career gap, a single-role CV, a step down
in seniority, and a posting stating no minimum experience are never scored near
zero — and that trajectory carries the *smallest* weight, because a non-linear
career is not a defect. If those checks fail, the scorer gets fixed, not the
tolerance. The same file carries the frozen engine's acceptance regression (a
credit-risk JD must beat a marketing JD by >25 points, with marketing still
scoring nonzero).

**Requirement extraction.** `jd_analyst.py` reads must-have vs preferred
skills, minimum years, degree level and eligibility gates from the posting. It
runs deterministically on every job; for the top-N jobs that get tailored, the
tailoring LLM call *also* returns its own read of the posting (same call, extra
fields, no additional cost), which is merged over the deterministic one. A
failed or empty model response never blanks out a real regex finding, and every
result records which analyst produced it.

## Follow-ups & weekly stats (`tracker.py`)

- **Follow-up nudges (daily):** any job you've marked `applied = yes` that
  still has no recorded outcome after `digest.followup_days` (default 7)
  triggers a one-time Telegram nudge with the link, so applications don't
  silently go stale. Record outcomes by setting the row's status to
  `response` / `interview` / `rejected` / `offer` in the dashboard.
- **Weekly stats (Sundays):** Telegram summary of jobs surfaced/tailored/
  applied, outcome counts, and positive-response rate broken down by score
  band and by source — evidence for tuning the score floor and deciding
  where your application time actually converts. The same stats panel is
  always visible in the dashboard.

## Web UI (Streamlit)

`streamlit_app.py` gives the pipeline a dashboard:

- **Review queue** — pick any day's queue, see scores/links, flip an
  `applied` status per job, and for every tailored match: the tailored
  summary, validated JD-aligned rewording, the honest gap note, and
  one-click **DOCX/PDF downloads** (rebuilt deterministically from the CSV —
  no LLM call, works even for queues produced by the cloud run).
- **Run now** — trigger a full scan on demand with live logs (keys read
  from Streamlit secrets or environment; missing keys just skip that step).
- **Filters** — edit title keywords, cities, salary floor, fit-score floor,
  experience years, and tailor count; saves `config.yaml`.

Run locally: `streamlit run streamlit_app.py`

Host free: [share.streamlit.io](https://share.streamlit.io) → New app →
this repo / `main` / `streamlit_app.py` (works with private repos via the
GitHub authorization). Add the same five keys under **App settings →
Secrets**:

```toml
ADZUNA_APP_ID = "..."
ADZUNA_APP_KEY = "..."
GEMINI_API_KEY = "..."
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_CHAT_ID = "..."
```

Because the daily GitHub Actions run commits each day's queue CSV back to
the repo, Streamlit Cloud auto-redeploys and the dashboard always shows the
latest scan without the app itself doing any scheduled work.

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
- `filters.min_score_to_tailor` — jobs below this score (default 55) stay
  in the CSV/digest but don't get a tailored resume.
- `filters.remote_only` — `true` drops all on-site/hybrid listings.
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
