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
| 2. SEARCH | `sources/adzuna.py`, `sources/workday.py`, `sources/greenhouse.py`, `sources/lever.py`, `sources/smartrecruiters.py`, `sources/ashby.py`, `sources/job_alert_email.py`, `sources/serpapi_jobs.py` | Adzuna aggregator (skill-cluster-derived titles, ALL industries — see "Editing filters & targets") + Workday CXS feeds for 9 employers (Citi, Deutsche Bank, Wells Fargo, Mastercard, PayPal, State Street, BlackRock, Adobe, Salesforce) + Greenhouse/Lever/SmartRecruiters (verified India-posting tokens) + Ashby (empty — none of ~40 checked companies had an India posting yet) + read-only IMAP ingest of Naukri/LinkedIn/Indeed job-alert emails (off by default) + SerpApi Google Jobs (paid tier, off by default — see "Job sources" below). Cross-source duplicates keep the DIRECT employer-ATS link over aggregator redirects. All normalized to one schema. |
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

## Seniority judging (`seniority.py`, added 2026-08-10)

Title is a bad seniority signal and this pipeline doesn't pretend otherwise —
a bank "VP" is typically a ~6-10 year individual-contributor grade (State
Street, Natwest, Wells Fargo all use it that way, and this pipeline sources
from banks directly via Workday), while the same title elsewhere means an
executive. Instead, every posting is judged on the experience it actually
**requires**, extracted in three explicit trust tiers so a guess is never
treated as a fact:

- **stated** — a real requirement phrase in the JD ("Experience: 7 years").
- **repaired** — a mangled range reconstructed. Adzuna's own API ships
  ranges with the separator stripped (`"Experience: 48 years"` means 4-8,
  `"810"` means 8-10) — verified against their live API and raw response
  bytes, not this pipeline's bug, just inherited mess.
- **inferred** — no number anywhere; a band implied by title wording only
  (e.g. bare "VP" → 6-14y, "AVP" → 7-13y). Judged on the band's **centre**,
  not its floor — a floor-only read systematically under-calls seniority.

A posting judged `over_senior` against `profile.comfort_max_years` (your own
ceiling, default 8) takes a configurable score penalty
(`profile.over_senior_penalty`, default 25) — enough to usually drop it below
`filters.min_score_to_tailor` and out of the digest's top-N, but it's a
**penalty, not a filter**: the row stays in the CSV with its verdict and
confidence visible (`exp_verdict`/`exp_confidence`/`exp_required`/`exp_why`
columns), never silently deleted on what might be an inferred guess. The
same standing-guard discipline as the frozen scorer applies here: real
company-age text ("P&G was founded over 180 years ago") must never read as a
job requirement, and that's a permanent smoke-test assertion, not a one-off
fix.

## Rating matches, and the learning loop (`feedback.py`)

Every queued job carries a `match_feedback` column you set in the dashboard —
**good / partial / no**. It's separate from `applied`: one is "was this
relevant", the other is "did I submit".

Once there are **25+ ratings with at least 5 in each class**, the Learning tab
proposes changes:

- **Title keywords to drop** — any keyword whose jobs you reject 70%+ of the
  time (minimum 6 samples) is pulling the search in the wrong direction.
- **Re-weighted sub-scores** — each sub-score is correlated (point-biserial)
  against your "good" labels, and weights nudge toward whatever actually
  predicted a match.

Three deliberate constraints:

1. **Nothing is applied automatically.** You get a proposal and an accept
   button. A scorer that quietly re-tunes itself makes your own score history
   stop meaning anything — last month's 72 has to still mean what it meant.
2. **Nothing is concluded below the floors.** Under 25 labels it reports the
   shortfall instead of inventing a trend from six data points.
3. **It validates the scorer before tuning it.** If jobs you called "good"
   don't actually score higher than the ones you called "no", it says so and
   flags the weight proposal as low-confidence — re-weighting a score that
   doesn't separate is rearranging deck chairs.

Ratings live in the daily CSV, so they're stored day-wise and committed with
the queue like everything else.

## Job sources — and the portals this deliberately does not scrape

Automated, no auth, no scraping: **Adzuna** (aggregator) plus direct ATS feeds
from **Workday, Greenhouse, Lever, SmartRecruiters and Ashby**. These are the
feeds employers publish for exactly this purpose, and they're where most India
GCC and startup product roles appear first.

**Naukri, LinkedIn, Indeed, Shine, foundit, TimesJobs, iimjobs and Glassdoor
are never scraped.** They have no public API, their terms prohibit automated
access, and a scraper would run against *your own* accounts — a Naukri ban
costs you the recruiter inbound that platform generates, which is worth more
than the listings. Their bot protection also breaks scrapers constantly, which
is a poor foundation for something meant to run unattended.

Instead, `sources/job_alert_email.py` ingests the **job-alert emails those
portals already send you**. Your inbox is your data: no bot detection, no
account risk, no third-party terms involved — the portal mailed it to you on
purpose. It connects to IMAP **read-only** (`BODY.PEEK`, so nothing is even
marked as read), extracts posting links, and normalizes them into the same
schema as every other source. It never sends, replies, or deletes.

Setup: create alerts on each portal → filter them into a Gmail label →
generate a Gmail **app password** (Account → Security → 2-Step Verification →
App passwords; never your account password) → set `JOB_ALERT_EMAIL` and
`JOB_ALERT_APP_PASSWORD` as secrets → set `job_alert_email.enabled: true` in
`config.yaml`. Alert emails carry a link but no job description, so these
listings score on title alone and rank below fully-described ones — expected,
not a defect.

**SerpApi Google Jobs (`sources/serpapi_jobs.py`) — the paid route to the same
portals, added 2026-07-29.** Google's own job index surfaces postings from
sites (including Naukri/LinkedIn/Indeed) that implement `JobPosting`
structured data — real coverage, but only what Google has indexed, not a full
mirror of those portals. Off by default: set `SERPAPI_KEY` and
`serpapi.enabled: true`. Free tier is 250 searches/month; `search
.serpapi_titles` is a deliberately narrower subset of the main title list to
fit that budget, and `data/serpapi_usage.json` (committed back by the daily
workflow) tracks monthly usage so a debug session re-running `main.py`
repeatedly can't silently blow the quota — the `serpapi.quota_buffer` guard
stops calling early and logs it rather than erroring once near the cap.

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

Run locally: `streamlit run streamlit_app.py`. On Windows, a Task Scheduler
job (`JobPipelineDashboardWatchdog`, added 2026-07-31) runs
`dashboard_watchdog.ps1` every 5 minutes and restarts it on port 8502 if it
isn't listening — a plain backgrounded process kept dying unpredictably
between terminal sessions, and the watchdog is independent of any particular
session staying open. It only runs while you're logged into Windows; the
hosted dashboard below doesn't have that limitation.

Host free: [share.streamlit.io](https://share.streamlit.io) → New app →
this repo / `main` / `streamlit_app.py` (works with private repos via the
GitHub authorization). Add the same keys under **App settings → Secrets**
(SerpApi optional — only needed if `serpapi.enabled: true`):

```toml
ADZUNA_APP_ID = "..."
ADZUNA_APP_KEY = "..."
GEMINI_API_KEY = "..."
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_CHAT_ID = "..."
SERPAPI_KEY = "..."
```

Because the daily GitHub Actions run commits each day's queue CSV back to
the repo, Streamlit Cloud auto-redeploys and the dashboard always shows the
latest scan without the app itself doing any scheduled work.

## Applying — semi-assisted, not automated

Stage 4 gives you, per job: the link, a tailored DOCX/PDF resume file, lead
bullets, and an honest gap note. The dashboard's **Apply** link (next to
"Open job posting" in the queue) is the semi-assisted path — added
2026-07-31/08-01 alongside the **CV Match Copilot** Chrome extension
(`C:\Claude\cv-match-copilot-gemini`, a separate sibling project):

- **Apply** opens the job URL with one extra marker (`?jtApply=1`) — a
  plain link, no messaging, no extra permission. If the extension is
  installed and that host is one it covers (51 hosts as of 2026-08-01,
  including every ATS this pipeline sources from, plus Adzuna, LinkedIn,
  Naukri, Indeed, and more — see `config.yaml`'s `apply_bridge
.autofill_hosts`), it notices the marker and automatically reads the JD,
  tailors your resume against the FULL posting text (not this pipeline's
  truncated snippet), and fills the application form — then **stops**.
- **Adzuna listings** get one extra hop: Adzuna's own page isn't the real
  application, so the extension follows the posting's own "apply" link
  through to the actual employer (or, sometimes, another aggregator) before
  filling — automatically, in your own browser session.
- **Any other employer page** the extension doesn't auto-load on: click the
  extension's toolbar icon → "Run on this page" — one extra click, works on
  any domain, no new standing permission.
- **A host the extension doesn't cover at all**, or no extension installed:
  Apply just opens the link like a plain click — same as always.

**Whatever happens above, reviewing and clicking Submit is yours, every
single time — this is enforced in the extension's own code (hard-gated
behind a setting that defaults OFF and this integration never touches), not
just a UI convention.** This was asked to be changed to full auto-submit
during this feature's build (2026-08-01) and declined — see the design
boundary above. The friction that request was actually pointing at (review
feels slow) is being addressed by making the review step itself faster, not
by removing it.

## Career Agent — company-centric targeting & outreach (extension, added 2026-08-04 onward)

A separate capability layered onto this same repo (not a new project — see
`CLAUDE.md`'s 2026-08-04 entry for why extending in place beat a new repo):
instead of waiting for a job posting, it finds companies worth targeting,
identifies who actually owns hiring for a role there, and prepares outreach
as a Gmail **draft** for your review. The submission boundary from the rest
of this README still holds, restated precisely for outreach (updated
2026-08-10 — see below): **a message only ever sends after one explicit
human click on that exact draft, in the "📤 Outreach review" dashboard tab
— never unattended, never batched without a look, never on a schedule.**
Full auto-send was asked for and explicitly declined; see `CLAUDE.md`'s
2026-08-10 entry for the reasoning.

| Stage | Module(s) | What happens |
|---|---|---|
| A2 Company Targeting | `company_targeting.py` | Force-includes every company in `policy/company_allowlist.yaml` (a 147-company guaranteed floor across BFSI/fintech categories) plus anything discovered elsewhere, scores each on real hiring-activity signal from this pipeline's own `data/seen_jobs.json` — the other 4 spec'd signals (cluster-fit, sub-sector, size, geography, growth) have no data source yet and contribute 0 rather than a guessed number. Your current employer is flagged `is_conflict_of_interest` and excluded from outreach downstream regardless of score. |
| A3 Hiring Authority Graph | `authority_graph.py`, `policy/network.yaml` | Ranks people by who **owns the requisition** — function/P&L owner > hiring manager > function-specific TA > generic HR — not by title alphabet. Node discovery is restricted to public non-scraped sources (company pages, press releases, regulatory filings, speaker bios, bylines) or your own manual entry; no LinkedIn scraping, ever. `warm_path_distance` only ever comes from `network.yaml`, which you edit by hand — never inferred by traversing anything. |
| A5 Contact Resolution | `contact_resolution.py`, `policy/contact_allowlist.yaml` | A contact enters the outreach queue only with a real `consent_basis` (careers page, JD-listed email, a channel you already have a relationship through, etc.) — pattern-guessed addresses (`first.last@company.com`) are structurally impossible to write, not just discouraged. Every candidate is gated on RFC-5322 syntax, a real MX record (DNS lookup only, never an SMTP probe), and a domain that actually matches the company. **Most people don't have a findable, consented contact — the resolver returning nothing is the normal, expected outcome**, not a failure. |
| A8 Outreach Composer | `outreach.py`, `gmail_auth.py` | Creates a Gmail **draft** once every precondition holds: not a conflict-of-interest company, channel confidence ≥0.6, not on the suppression list, ≤20 drafts/day and ≥21 days since the last outreach to that company (both hard-clamped in code — `config.yaml` can lower these caps, never raise them), and for cold company-centric outreach specifically, a hiring-authority node with real ownership likelihood and a plausible warm path. Falls back to writing `.eml` files under `out/drafts/` if Gmail access is ever blocked. |
| A9 CRM & Calibration | `outreach_crm.py` | Tracks what actually happens after a draft becomes a real, human-sent email: a validated state machine (DRAFTED→SENT_BY_USER→REPLIED→INTERVIEW/REJECTED→CLOSED) that logs every hop as an `event`; sent/reply detection using the `gmail.readonly` scope (reads label state and thread messages only, never sends/modifies); a follow-up scheduler bounded by the same F4 caps as A8; and the "30-day weight refit" `authority_graph.py` has referenced since A3 — once ≥20 real outreach outcomes exist (≥4 per node type), proposes adjusted `NODE_TYPE_BASE_LIKELIHOOD` priors from observed reply rates. Same two hard rules as `feedback.py`'s learning loop: never auto-applies, never concludes below the floor. An explicit do-not-contact close (not a plain rejection or no-reply) auto-suppresses that channel. |
| Outreach review & send | `outreach_send.py`, dashboard "📤 Outreach review" tab | The ONLY code path anywhere allowed to call Gmail's send API — `send_approved_draft()` refuses without an explicit `confirmed=True`, which only the dashboard's "Approve & send" button ever passes, and refuses for anything that isn't a DRAFTED outreach with a real Gmail draft. Sends the EXISTING reviewed draft as-is (never recomposes it), then hands the state transition to A9. A standing whitelist test (`career_agent_smoke_test.py`, F1) confines the send scope string to `gmail_auth.py` and the live send call to this one file. |

**Current real state (2026-08-10), read before assuming this is generating outreach today:** A2/A3/A5/A8/A9 plus the send-review flow are all built and tested (103 checks in `career_agent_smoke_test.py`), and A8/OAuth are live-verified against a real Gmail account (`mehul.96.mit@gmail.com`). **There are zero real contacts or sent outreach in the system** — a genuine research pass across the companies with verified hiring-authority nodes found no automatable, verified contact for any of them, which is the expected honest outcome given how few companies publish that. A9's reply detection and weight refit are real and unit-tested against synthetic fixtures, but report the honest "nothing yet" against the live database — that's correct, not a bug, until a real contact and a real send exist. Outreach only starts once you supply a contact yourself (`user_existing_relationship`/`user_network_referral` in `policy/contact_allowlist.yaml`'s terms) or a real job posting surfaces an apply-by-email address.

Run: `python company_targeting.py` (A2) → `python authority_graph.py` (A3, reports existing nodes — discovery is manual research, see module docstring) → `python outreach_crm.py` (A9, prints refit readiness + due follow-ups, runs Gmail sent/reply detection if `gmail_auth` is set up) — contact resolution and outreach itself are still called programmatically (`contact_resolution.resolve_contact()`, `outreach.draft_outreach()`), no CLI wrapper yet. Full build history and every debugged gotcha (OAuth Cloud-Console bot-detection, the Google Auth Platform UI's Test-users location, WebSearch summaries that turned out wrong on verification) is in `CLAUDE.md`'s STATUS block, 2026-08-04 through 2026-08-09 entries.

## Interview Prep Toolkit — Phase 1 only (extension, added 2026-08-11,
## rebuilt around a ranked prep plan 2026-08-12)

A second, separate extension layered onto this same repo (own database,
`data/interview_prep.sqlite3`, `INTERVIEW_DB_PATH` overridable — never
touches `career_agent.sqlite3` or the job queue). Given a real job
description, it turns your resume into an interrogatable candidate model:
every bullet becomes a claim with a computed interview-risk score, gets a
question tree and (if it carries a metric) a ten-question metrics-defense
set, and the JD gets matched against your resume to surface real gaps and
a curated slice of an 83-question general PM/behavioral/HR bank. First-pass
answers are drafted for all of it (free-tier LLM, tailored to the specific
target company/role/JD — not a generic answer — and fact-checked against
`resume_master.json`: a fabricated number is a hard regeneration, then a
hard failure, never a silent pass), which you then edit, critique, drill
out loud, or regenerate. **Practice — an adaptive AI interviewer, live
evaluation, mock interviews, readiness scoring — does not exist.** This is
Phase 1 (Preparation) only; every "Practice"/"Readiness"/"Interview Day"
screen a master prompt asked for was deliberately left unbuilt rather than
shipped with no real data behind it. See `CLAUDE.md`'s 2026-08-11/08-12
entries for the full reasoning on what was built vs. declined.

The dashboard tab is organized into five tabs, each with one job:

| Tab | What it's for |
|---|---|
| 🎯 Prep Plan | The screen you actually prepare from — a short, ranked list of REAL questions (not internal topic labels), day-aware (10 items a day out, 40 two weeks out), with an explicit "re-read this JD properly" upgrade and the genuine JD gaps below it. |
| 📋 Resume Claims | Pick one resume bullet, generate its follow-up-question tree and (if it carries a number) its metrics-defense set. |
| 🗂️ Question Bank | Every question that exists, one sub-tab per category — all of them, not just the ranked slice — plus a searchable flat table. |
| 📖 Story Bank | Full SITAR stories, editable field by field, with a "fill in the details" section for the 8 supporting fields (team size, stakeholders, trade-offs, ...) a first draft can't know. |
| ✅ Fact Review | Confirm/reject/resolve any number your own edits introduced against what the resume already states. |

| Piece | Module(s) | What it does |
|---|---|---|
| Candidate model & claims | `interview_prep.py` | One `ResumeClaim` per bullet, extracted verbatim (never generated) — metric, ownership signal (I/we/passive/absent), and a computed risk score (metric + ambiguous ownership = the exact combination a follow-up exposes). Candidate differentiators pulled from `resume_master.json`'s achievements. All deterministic, zero API cost. |
| Question tree & metrics defense | `interview_prep.py` | Ten fixed question types per claim (What/Why/How/Who/Your role/Data/Impact/Trade-off/Failure/Change) and, for any claim with a number, a ten-dimension metrics interrogation (baseline/timeframe/measurement/causality/trade-offs/etc.) — both templated, not LLM-generated. |
| JD intake & gap mapping | `interview_prep.py` (`process_new_jd`) | Reuses `jd_analyst.analyze_jd()` — job_pipeline's own BULK, deliberately lexical scorer — for the synchronous intake read (§4.2 requires this stay call-free). A single-JD read of that output is noisy on purpose (it's built to average out across 500 postings/day, not to be read literally); `interview_prep.reanalyze_process_jd()` + `interview_llm.analyze_jd_llm()` offer one grounded LLM re-read per process (surfaced as an explicit dashboard button) that replaces it with real, JD-grounded requirements. |
| Ranked prep plan | `interview_prep.py` (`build_prep_plan`) | Real questions — not topics — ranked by likelihood × stakes, discounted by how prepared you already are, sized to how many days remain (`plan_size_for`). Near-certain openers ("tell me about yourself", "why us") are never scored out even once answered; a hard cap stops one templated question from flooding the list across claims. |
| Base question bank | `interview_question_bank.py` | 83 curated, static questions across 10 categories (intro/career, current role, PM fundamentals, product-sense cases, metrics, stakeholder management, execution, behavioral, target company/role, HR) — the categories a claim-derived tree structurally can't produce. Per-question `QUESTION_LIKELIHOOD` overrides the category-level prior for questions whose real frequency isn't their category's average. |
| Story bank | `interview_stories.py` | SITAR (Situation/Task/Action/Result/Reflection) stories, guided or LLM-drafted from a claim, with the same fact-integrity check as answer drafting. Fully editable after drafting (`update_story()`/`delete_story()` — a hand-edit is trusted the same as a hand-typed answer, not re-run through the fact-integrity regenerate path). Any field the candidate hasn't supplied is a literal `[YOU FILL: ...]` placeholder, never guessed; flags competencies with zero mapped stories as styled chips. |
| Answer Bank | `interview_answers.py` | Append-only versioned answers (`generate`/`author`/`revise`/`correct_extraction`/`correct_import`), batch generation across a claim's full question set, and per-answer fact-candidate detection feeding a confirm/reject/conflict-resolution queue — a candidate value that contradicts the resume halts and asks, never silently overwrites `resume_master.json`. Generation reads the active process's own target company/role/JD text and threads it into the prompt, so two different target roles produce genuinely different emphasis from the same underlying facts, not interchangeable generic answers. |
| LLM plumbing | `interview_llm.py` | Free-tier only (Gemini/Groq — Anthropic is deliberately excluded, it's a paid model in this repo's config). Every generation function takes an injectable `call_fn` so tests run with zero API keys. One regeneration on a fact violation, then a hard failure. Plain, conversational language is enforced by prompt (no corporate jargon, no AI-cliché filler) — this is spoken practice material, not a LinkedIn post. Includes `critique_answer()` — Observation/Why it matters/How to improve feedback with a verbatim-quote check, no numeric score (a real score belongs to the evaluation engine that doesn't exist yet). |
| Dashboard tab | `interview_ui.py`, dashboard "🗂️ Interview Prep" tab | Process switcher (multiple concurrent target companies, T§14 isolation), the five tabs above, drill mode (hide the answer, say it out loud, then reveal — retrieval practice over re-reading), and a one-page markdown prep-sheet export for the night before. Styled with a dedicated dark case-file/field-note token system (`--ink`/`--paper`/`--brass`), deliberately not the generic navy-SaaS look repeatedly requested and repeatedly declined (see `CLAUDE.md`). |

**Current real state (2026-08-12):** all of the above is built, unit-tested
(`interview_smoke_test.py` + `answer_bank_smoke_test.py`, 100+ checks
combined, offline/zero-keys via fake `call_fn` doubles) and live-verified
against the real Gemini API and by actually clicking through the hosted
Streamlit dashboard against a real job description (ICICI Bank, Digital
Product Manager). Several real bugs were found only by that live use, not
the offline suite — a missing 429/503 retry, an `st.rerun()` silently
discarding writes because it unwinds through the DB connection's
commit-on-normal-exit context manager, a process switch that never actually
switched, `StreamlitDuplicateElementKey` once the same question could
legitimately appear in two tabs, and a fit score quietly computed over
lexical noise ('gathering', 'solution', one requirement literally named
'key') because the bulk JD scorer was being read as if it were a
single-posting analyst. All fixed and re-verified live — see `CLAUDE.md`
for the mechanism of each, worth reading before touching `interview_ui.py`,
`interview_prep.py`, or `interview_llm.py` again.

No CLI wrapper — everything is called programmatically or through the
dashboard tab today (`interview_prep.process_new_jd(...)`,
`interview_answers.generate_answer_batch(...)`). Run the dashboard the same
way as the rest of the app (`streamlit run streamlit_app.py`) and open the
"🗂️ Interview Prep" tab. Live at https://job-1357.streamlit.app — this
subsystem's own database only exists on whichever instance you use it from
(local vs. hosted), by design (see `CLAUDE.md`, 2026-08-12).

## Free-tier setup (one-time, ~20 minutes)

### 1. Adzuna key (free)
1. Register at https://developer.adzuna.com/ → create an app.
2. Note the **Application ID** and **Application Key**.

### 2. Gemini key (free)
1. Go to https://aistudio.google.com/apikey → "Create API key".
2. Free tier comfortably covers 50 tailoring calls/day on `gemini-2.0-flash`.
   (Alternative: Groq — free key at https://console.groq.com/keys, then set
   `tailor.provider: groq` in `config.yaml`.)

### 3. Telegram bot (free, optional but recommended)
1. In Telegram, message **@BotFather** → `/newbot` → pick a name → copy the
   **bot token**.
2. Send any message to your new bot (this is required once).
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and
   copy `"chat":{"id": <number>}` — that number is your **chat id**.

### 4. SerpApi key (optional, free tier — Naukri/LinkedIn/Indeed coverage)
1. Register at https://serpapi.com → dashboard → copy the API key.
2. Free tier is 250 searches/month — see `search.serpapi_titles` and
   `serpapi.*` in `config.yaml` for how usage is kept within that budget.
3. Set `SERPAPI_KEY` and flip `serpapi.enabled: true`.

### 5. GitHub repository + secrets
1. Push this repo to GitHub (private is fine).
2. Repo → Settings → Secrets and variables → Actions → add:
   - `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`
   - `GEMINI_API_KEY` (and/or `GROQ_API_KEY` / `ANTHROPIC_API_KEY`)
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - `SERPAPI_KEY` (optional)
   Unset secrets are skipped gracefully — the pipeline still runs, quietly
   missing whatever that secret powers.
3. **Adding the secret is not the same as wiring it.** A secret existing in
   `gh secret list` only reaches the script if it's also listed in the
   workflow's `env:` block under the "Run pipeline" step
   (`.github/workflows/daily_job_scan.yml`) — a real bug on 2026-07-30 was
   exactly this: `SERPAPI_KEY` was added to the repo and to the commit-back
   step, but missing from that `env:` block, so the run reported "not set"
   despite the secret existing. Check both places when adding any new secret.
4. **Verify, don't assume.** "I added it in the UI" and "the workflow can
   actually read it" are different claims — a secret can look saved and still
   never reach the job (wrong name, wrong repo, saved to an environment the
   workflow doesn't use, or missing from `env:` per the point above). After
   adding secrets, trigger a run (`gh workflow run daily_job_scan.yml`) and
   grep its log for each one:
   `gh run view <id> --log | grep -i "not set\|call failed\|skipping"`.
   A secret that "should be set" but shows up blank in that grep isn't set,
   full stop — this exact gap (Gemini + Telegram silently missing for
   an unknown stretch while Adzuna alone got fixed) is why this line exists.

### 6. Resume placement
Put your resume at the repo root as **`base_cv.pdf`**. Replace it any time —
the next run re-parses it.

## Editing filters & targets

Everything lives in `config.yaml`:
- `search.titles` — queries sent to Adzuna/Workday. As of 2026-07-29 these are
  skill-cluster-derived (each title maps to a capability actually evidenced in
  `resume_master.json`, not a guess — see CLAUDE.md for the full mapping), not
  generic PM/BA guesses. Adding a title here does nothing unless it (or a
  substring) is also in `filters.title_keywords` below — that's what actually
  lets a listing through.
- `search.serpapi_titles` — a narrower subset used only by
  `sources/serpapi_jobs.py`, sized to fit the free tier's 250 searches/month.
- `filters.title_keywords` — allowlist; a listing needs one in its title.
- `filters.cities` — allowlist (empty = all cities); "remote" always passes.
  Widened 2026-08-10 to add Hyderabad/Delhi/Gurugram/Gurgaon/Noida/Chennai
  alongside Pune/Mumbai/Bangalore — real run logs showed ~520 jobs/day
  already clearing every other filter, so geography was the actual volume
  bottleneck, not relevance.
- `profile.comfort_max_years` / `profile.stretch_years` /
  `profile.over_senior_penalty` — the seniority-judging thresholds, see
  "Seniority judging" above.
- `filters.min_salary_annual` — enforced ONLY when a listing reports salary.
- `filters.min_score_to_tailor` — jobs below this score (default 50, using the
  structured score — see "Fit scoring" above) stay in the CSV/digest but don't
  get a tailored resume.
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
