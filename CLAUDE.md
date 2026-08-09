# job_pipeline — project context for Claude

Read this file first in any new session on this project. It has the
current status; `README.md` has full architecture/setup detail and
`GCC_COVERAGE_GUIDE.md` has the manual-application layer.

## STATUS (last updated 2026-08-09)

**🔁 READ THIS BEFORE DIAGNOSING ANY "the pipeline stopped running" REPORT
(2026-08-09).** It has now been reported twice — 2026-08-05 and 2026-08-09 —
and **both times the pipeline had run successfully every single day.** What
had actually happened both times: this LOCAL clone was 4 commits behind
`origin/main`, so `data/job_queue_*.csv` topped out at an old date and the
local dashboard (which reads local files) looked dead. A stale clone and a
dead pipeline were indistinguishable from the dashboard. Actual run history
on 2026-08-09 was 18/18 consecutive successful days.

**So: check `gh run list --workflow=daily_job_scan.yml` FIRST, before
touching any code.** If runs are green, the pipeline is fine and the problem
is local sync — do not "fix" the workflow.

Three things were changed so this cannot silently recur:
1. **`dashboard_watchdog.ps1` now fast-forwards the clone** (`git fetch` +
   `git merge --ff-only origin/main`) on the same 5-minute Task Scheduler
   job that already restarts Streamlit, and writes its outcome to
   `dashboard_sync.log`. `--ff-only` is load-bearing: it can only move the
   clone forward, so it can never overwrite local commits or unsaved
   `match_feedback`/`applied` edits the dashboard wrote into a queue CSV. A
   refused merge is reported, never auto-resolved — losing hand-entered
   ratings to an automatic merge would be worse than showing stale data.
2. **`streamlit_app.py` shows a data-freshness banner above the tabs on
   every page** (`freshness_banner()`): newest queue date, its age, and the
   sync heartbeat. Green when current; a loud red block when the data is 2+
   days old OR the watchdog's own heartbeat is >20 min stale — that second
   condition matters because a dead syncer otherwise renders identically to
   a healthy one. The red block explicitly says a stale clone does NOT mean
   the pipeline failed, and links Actions + the hosted dashboard to check.
3. Two real bugs were caught in that banner only by testing it rather than
   assuming: PowerShell's `-Format o` emits 7-digit fractional seconds and
   `Set-Content -Encoding utf8` (PS 5.1) prepends a **BOM** — each one made
   `datetime.fromisoformat()` raise, silently killing the "N min ago"
   heartbeat and making a dead watchdog look healthy. Fixed by writing
   `yyyy-MM-ddTHH:mm:sszzz` and reading `utf-8-sig`. **If you touch that
   log's format, re-test the parse — this exact failure is invisible.**

**Cron delay fix from 2026-08-05 measurably worked** (verify before changing
it again). `daily_job_scan.yml` moved from `"0 3 * * *"` to `"17 3 * * *"`
and the watchdog from `"0 5 * * *"` to `"39 5 * * *"`, on the theory that
GitHub queues every repo requesting the same top-of-hour minute. Observed
start times since: 08-06 11:39 UTC, 08-07 07:57, 08-08 05:31, 08-09 05:55 —
i.e. the delay fell from a consistent 8–10h down to ~2.5h. Not eliminated,
and GitHub gives no SLA on `schedule:` at all, so **the honest ceiling here
is "runs daily, lands mid-morning IST," not "runs at 08:47 IST sharp."** The
only way to a hard guarantee is triggering from outside GitHub Actions (an
external cron service calling the GitHub API with a PAT) — deliberately NOT
built: it needs a long-lived credential Mehul would have to create and hold,
to solve a problem that has not actually occurred in 18 days.

**Local Windows scheduled tasks are NOT a reliable backstop for anything
critical** — a one-off verification task registered for 2026-08-06 failed
with `0x800710E0` (operator/administrator refused) and produced nothing.
They only run while logged in, and can be refused silently. Fine for
convenience work like the dashboard sync above (if the machine is off you
are not reading the dashboard anyway); not fine as the thing standing
between you and a missed run.

**✅ A5 contact resolution built.** New file `contact_resolution.py` — the
validation pipeline that has to pass BEFORE `outreach_store.
insert_contact_channel()` (the F2 gate, built in the 2026-08-04 P0 session)
ever gets called: RFC-5322-shaped syntax check, MX-record lookup (DNS only —
never an SMTP handshake/RCPT TO probe, per F6), domain-match against the
company's own domain or a documented subsidiary (hard reject if neither —
added `company.domain` column + `dnspython` dependency, both new this
session), suppression-list check, case-insensitive dedupe, and a documented-
prior confidence score (base rate per `consent_basis` type, +/- domain
match, +corroboration, -staleness after 180 days). Every rejection logs a
`NO_CONSENTED_CONTACT` event rather than silently dropping. 47/47 smoke
tests pass (13 new for A5).

**Real research pass against the 7 already-verified A3 nodes (5 companies:
PhonePe, Razorpay, Cashfree Payments, Pine Labs, Setu, IIFL Finance) found
ZERO consented contacts — and that's the correct, expected result, not a
gap.** Checked two automatable routes: (1) job_pipeline's own already-
discovered JD data for these companies — no apply-by-email addresses exist
in any Greenhouse/Lever posting (expected; ATS platforms don't publish
those), (2) each company's own careers/contact page for a published
general inbox. Two leads surfaced by search (`jobs@razorpay.com`,
`careers@cashfree.com`) — **both failed direct verification**: Razorpay's
actual contact page lists real named PR contacts (Hepsibah Rozario, Anu
Saraswat, etc.) and a PR-only inbox, no `jobs@` address at all; Cashfree's
own careers page explicitly says "email us your resume" without ever
giving an address. Same "verify the primary source, not the search
summary" discipline as the Fibe catch (2026-08-05) and the Tata Capital
non-add — a third confirmation this matters, not a one-off.

**Bottom line**: A5's mechanism is real, tested, and ready to fire the
moment a legitimate consent_basis route produces a candidate — this session
just didn't find one, which matches the master prompt's own framing exactly
("the absence of a contact is a valid, expected outcome"). The remaining
5 of 7 consent_basis types (`user_existing_relationship`,
`user_network_referral`, `inbound_recruiter`, `portal_opt_in_channel`, and
effectively `job_post_listed_contact`/`ats_apply_by_email` beyond what
job_pipeline already discovers) are inherently Mehul-supplied, not
automatable — that's F2's design, not a gap to close.

**Second same-session follow-up (2026-08-05) — SEBI-filing route tried as requested,
partially works, 1 more node found (7 total, 5 companies).** Mehul asked to
pursue RBI/SEBI/MCA filing coverage for the 19 companies (mostly large
regulated NBFCs/insurers) that came up empty in the first pass (see entry
below). Findings, precisely:
- **RBI's NBFC register**: no executive names at all, registration status
  only. Not useful for this purpose, don't retry.
- **MCA filings**: Director-level only (board, not function heads). Same gap
  as company "leadership" pages already hit. Not useful for this purpose.
- **SEBI LODR "Senior Management" disclosure**: real regulatory requirement,
  but hits two separate walls in practice — (a) the quick-access web pages
  for it usually only surface Board+CFO/Company Secretary, not the fuller
  functional list (confirmed on Muthoot Finance), and (b) the actual annual
  report PDF that has the full Schedule V table is often a **scanned/
  image-based PDF with no extractable text** (confirmed on Cholamandalam,
  7.6MB, no OCR available in this environment). This is a real tooling
  limit, not a discovery-effort gap.
- **What actually worked**: SEBI Regulation 30 "material event" disclosures
  — the specific one-off filings companies make for individual KMP/Senior
  Management *appointments* (not the annual consolidated list) are
  sometimes indexed directly by search and, critically, the company then
  often publishes a matching bio page on their own site. This is how **IIFL
  Finance (Vinay Agrawal, Business Head - Loan Against Property)** was
  confirmed — found via a Reg 30 appointment search, then verified against
  IIFL's own dedicated bio page (`iifl.com/finance/about-us/leaders/
  management/vinay-agrawal`), a real `company_leadership_page` source.
  **Tata Capital had the same pattern of lead (2 new CBOs, Reg 30, May
  2026) but could NOT be confirmed** — the specific BSE filing PDF 404'd and
  Tata Capital's own management-team page returned HTTP 406 — so it was
  correctly left out despite a plausible-sounding search summary. Angel
  One's only current CPO-related news was a **resignation** (Ankit Rastogi,
  leaving Aug 2026) — correctly not added.
- Fixed another live classifier gap: `authority_graph.py`'s
  `OWNER_TITLE_MARKERS` didn't include "business head" (a common Indian BFSI
  P&L-owner title pattern with no "chief"/"head of"/"director" in it), and
  `FUNCTION_KEYWORDS["partnerships"]` didn't include "business head"/"chief
  business officer" — both fixed, `career_agent_smoke_test.py` still 34/34.

**Bottom line for future sessions**: the SEBI Reg-30-appointment-then-
verify-on-company-bio-page pattern is real and worth repeating per company,
one appointment search at a time — it's just slow (one company at a time,
most still come up empty) and cannot be sped up by trying to read full
annual-report PDFs (OCR gap) or the RBI/MCA registers (wrong data entirely).
7 nodes / 5 companies is genuinely where this methodology caps out without
new tooling (OCR, or a paid corporate-data API) or manual LinkedIn lookups
Mehul does himself and hands over via `add_manual_node()`.

**✅ First same-session follow-up — node-discovery research pass run against
25 companies, 6 real nodes written.** The entry below says "0 authority nodes
yet ... is the correct, honest output" — that was true right after A3's code
landed, before this research pass; it's stale now. Ran a manual (not
automated — see the module docstring, this is real per-company web research
using WebSearch/WebFetch, not a scraper) pass against A2's top 25 companies
by relevance score (swapped out Bajaj Finance — conflict-of-interest, no
outreach possible there regardless — and the meaningless alphabetical tail
of 0-score ties, for better-known companies across categories).

**Result: 6 verified nodes across 4 companies**, all traced to an allowed
source (see `policy/authority_node_sources.yaml`) with a real URL —
PhonePe (Neeraj Jain, Head of Product), Razorpay (Khilan Haria CPO + Apuarv
Sethi CMO), Cashfree Payments (Vijay Ravisekar, VP Product Management), Pine
Labs + Setu (Vijeth Pandit, CPO of both — one verified byline naming both
companies). 19 of 25 companies came up empty against a real primary source —
expected and reported honestly, not silently skipped.

**Real finding worth remembering for the next research pass:** consumer
fintech startups (CRED, Razorpay, PhonePe, Pine Labs, Cashfree) publish
named product/function executives in press releases and blog bylines;
traditional regulated NBFCs/insurers/brokers (Tata Capital, Cholamandalam,
Muthoot Finance, IIFL Finance, Angel One, Go Digit, Policybazaar, Acko) do
not — their public sites only name CEO/MD/Board/CFO for governance
disclosure, never product-function heads. This pass's methodology (leadership
pages, press releases, blog bylines) essentially cannot find nodes for that
second group. RBI/SEBI/MCA regulatory filings — not yet attempted, needs
real filing-database access — are the more promising next avenue
specifically for the large-NBFC segment of the allowlist.

**One integrity catch during the pass, worth noting as a standing risk:** a
WebSearch AI-summary claimed Fibe's Chief Product & Analytics Officer was
"Balakrishnan Narayanan," but the actual regulatory filing it cited
(`earlysalary.in/.../manager-details/`) named entirely different people when
fetched directly. **WebSearch's summary was simply wrong** — always fetch
the actual cited primary source before writing an authority_node; never
write from a search summary alone, even when it sounds confident and cites
a URL.

Two classifier gaps found and fixed live during the pass (both in
`authority_graph.py`'s `FUNCTION_KEYWORDS`): "Chief Marketing Officer" and
"VP - Product Management" weren't matching (`FUNCTION_KEYWORDS["growth_marketing"]`
needed "chief marketing officer"/"cmo"; `["product"]` needed "product
management" — the "manager" vs "management" wording gap). Fixed; watch for
more title-wording gaps as more companies get researched — this classifier
is still narrow.

**🟡 Session wrap-up 2026-08-05 — Career Agent A3 hiring-authority graph
built (P0 + A2 were 2026-08-04).** Continues the same in-place extension of
job_pipeline (see 2026-08-04 entry below for why, still the live decision).

**Built and tested this session** (`career_agent_smoke_test.py`, 30/30 pass):
- `outreach_store.py` — added `company.size_band`/`headcount_estimate`
  columns (idempotent migration via `_migrate_add_columns()`, safe against
  the DB file A2 already created), and `insert_authority_node()` — the sole
  write path for `authority_node`, gated on `source` being one of the 6
  values in the new `policy/authority_node_sources.yaml` (company leadership
  page, press release, regulatory filing, conference speaker list, published
  byline/podcast, or `user_manual_entry`). No LinkedIn scraping path exists
  anywhere in this codebase — that's enforced by this being a closed
  allowlist, not a comment.
- `policy/network.yaml` — Mehul's own warm-path map (person → distance/via),
  currently empty (`people: []`). **Read-only input, never written or
  inferred by code** — `authority_graph.warm_path_distance()` only looks
  values up here; there is no social-graph traversal anywhere. Unlisted
  people default to distance 2 ("same sub-sector, plausible cold-but-
  relevant"), not 3 ("no path") — every company reaching this graph already
  came through A2's BFSI/fintech-targeted list, so sub-sector adjacency
  holds by construction unless network.yaml says otherwise.
- `authority_graph.py` (A3) — classifies a title into a function
  (product/business_analysis/partnerships/program_project/growth_marketing)
  and a node type (function_owner > hiring_manager > ta_lead_function >
  generic_ta, the master prompt's priority-by-req-ownership reframe, not
  title alphabet), scores `owns_req_likelihood` (documented priors, not yet
  fitted — needs A9 with n≥20 real outcomes per §9), and writes through
  `outreach_store.insert_authority_node()`. `add_manual_node()` is the one
  source Mehul can use directly today.
  **What this module does NOT do: discover people.** The other 5 allowed
  sources (leadership pages, press releases, filings, speaker lists,
  bylines) need real per-company research nothing here automates yet — `0
  authority nodes yet` is the correct, honest output of `python
  authority_graph.py` right now, not a bug.

**Not built yet (next session):** A5 contact resolution (the F2 resolver —
`insert_contact_channel()` exists but nothing calls it with real discovered
contacts), A8 Gmail outreach composer (OAuth + `.eml` fallback for the
`mibdu.org` Workspace-admin-block case), the node-discovery research step
for A3 (turning "0 nodes" into real function-owner names via the 5 public
sources), and the 4 pending A2 signals (cluster-fit, sub-sector,
size/geography, growth). `company.size_band`/`headcount_estimate` are also
still NULL for every company — nothing populates them yet, so A3's
size-band modifier in `owns_req_likelihood()` is inert until that lands.

**🟡 Session wrap-up 2026-08-04 — Career Agent P0 guardrails + A2 company
targeting built, extending job_pipeline in place (not a new repo).** A
separate master prompt asked for a 9-agent "Career Agent" system (company
targeting → hiring-authority graph → contact resolution → Gmail-draft
outreach → CRM/calibration) as a brand-new `career_agent/` repo. Checked
first and found job_pipeline already implements, live-tested, most of what
that spec calls A1/A4/A6/A7/A9 (CV parsing, job discovery, 3-layer scoring,
tailoring with a deviation gate, and the feedback/calibration loop) — so
building a parallel repo would have re-implemented ~2500 lines of debugged
code and created a 4th place scoring logic could drift (job_pipeline +
2 extension forks already have that problem per the master prompt's own
§13 mirror-discipline note). Decision, confirmed with Mehul: extend
job_pipeline in place; scope this session to P0 + one real agent (A2),
checkpoint, continue A3/A5/A8 next session.

**Built and tested this session** (`career_agent_smoke_test.py`, 18/18 pass,
`python career_agent_smoke_test.py`):
- `policy/company_allowlist.yaml` — the 150-company guaranteed-include floor
  (147 after documented dedup), `Bajaj Finance` flagged
  `conflict_of_interest_companies` (Mehul's current employer — must never be
  auto-drafted once A8 exists, only ever manual-review).
- `policy/contact_allowlist.yaml` — the 7 valid `consent_basis` values (F2's
  source of truth).
- `outreach_store.py` — new SQLite store (`data/career_agent.sqlite3`, WAL,
  gitignored/rebuildable) for `company`/`authority_node`/`contact_channel`/
  `outreach`/`event`/`suppression`. This file is the ONLY place permitted to
  INSERT into those tables — `insert_contact_channel()` is the F2 gate
  (raises on missing/invalid `consent_basis` before the DB's own NOT NULL
  constraint would), enforced at both the Python and schema level.
- `ratelimit.py` — F4 caps (20 drafts/day, 21-day per-company cooldown, 2
  follow-ups) clamped in code so `config.yaml` can lower them but never raise
  them past the ceiling.
- `company_targeting.py` (A2) — force-includes every allowlist company
  (`source_floor='user_allowlist'`, exempt from the DORMANT cap), then scores
  every company. **Only the `hiring_activity` signal is real** — it reads
  job_pipeline's own `data/seen_jobs.json` directly (no re-discovery) and
  counts reqs per company in the trailing 90 days. The other 4 signals from
  the master prompt (cluster-business-fit, sub-sector match, size band,
  geography, growth signal — 80% of the spec'd weight) have **no data source
  built yet** and deliberately contribute 0 rather than a fabricated midpoint
  — `relevance_explain_json.pending_signals` lists them so this is visible,
  not silently wrong. Run: `python company_targeting.py`.
- Standing regression test for F1 (`messages/send`/`gmail.send` grep) is in
  place now even though A8/Gmail doesn't exist yet — it'll catch a violation
  the moment outreach.py is written next session.

**Not built yet (next session):** A3 hiring-authority graph (needs
`network.yaml` for warm-path distance — user-maintained, never scraped), A5
contact resolution (the F2 resolver itself — `insert_contact_channel()`
exists but nothing calls it with real discovered contacts yet), A8 Gmail
outreach composer (needs OAuth setup + the `.eml` fallback for the
`mibdu.org` Workspace-admin-block scenario), and the 4 pending A2 signals
above. `career_agent.sqlite3` is gitignored and fully rebuildable by
re-running `company_targeting.py` — don't hand-edit it.

**✅ Session wrap-up 2026-08-02 — merged pipeline/browser status tab built
and live-verified; extension review-before-Submit UX built; a fully silent
missed daily run caught and given a watchdog.** Three things this session:

1. **Merged status tab (dashboard "🧭 Status")** — the item queued at the end
   of 2026-08-01's session. Shows a per-job CHECKLIST (Found → Scored →
   Résumé tailored → Opened in browser → Live JD tailor → Form filled →
   Submitted, with a badge for manual stages like interview/offer beyond
   that), not just a current-stage word — fixed same session after Mehul
   pointed out the first cut buried completed steps. Two real technical
   findings, both verified live rather than assumed:
   - The extension's `background.js` gained a new `opened` tracker stage
     (fires when the dashboard's `?jtApply=1` marker lands on a page, before
     tailoring even starts — the one browser-side moment nothing tracked
     before) and a READ-ONLY `jt.queryTracker` handler behind
     `externally_connectable` (scoped to `localhost:8502` + the hosted
     dashboard). No write path exists from the dashboard into the
     extension's storage.
   - **First implementation attempt (`st.components.v1.html`) silently could
     not work** — Streamlit renders that into a sandboxed `srcdoc` iframe
     with an OPAQUE origin; `allow-same-origin` does NOT fix this for
     `srcdoc` (verified live: `contentWindow.location.origin === "null"`),
     so `externally_connectable` can never match it no matter how correct
     the extension side is. Fixed by switching to Streamlit's static file
     server (`.streamlit/config.toml`'s `enableStaticServing`,
     `static/jt_status.html` + a Python-regenerated `jt_status_data.json`
     sidecar) — a real same-origin page, which the extension CAN message.
     **If a future dashboard feature needs `chrome.runtime` from inside
     Streamlit, this is the pattern — `components.v1.html` cannot do it.**
2. **Extension review-before-Submit UX (`content.js`)** — the other item
   queued at the end of 2026-08-01. The instant a Fill pass completes, the
   fill bar now shows a compact "N filled confidently, M need a look" line,
   a click-to-jump chip per flagged field, and auto-scrolls to the first
   flagged field so you land on it immediately. Reads the EXISTING
   three-state classes (`jt-confident-filled`/`jt-ai-filled`/`jt-needs-you`)
   rather than re-deriving confidence, so it can't disagree with the actual
   field outlines. Already committed and pushed via that repo's autopush
   hook (`.claude/settings.json` → `scripts/autopush.sh`). **NOT live-tested
   in a real browser this session** — both available paths were blocked
   (`file://` URLs refused by the browser automation tool; opening the
   extension's popup as a plain tab breaks its `activeTab`/`currentWindow`
   targeting, so "Run on this page" can't be driven that way either).
   Syntax-checked and the extension's 19-file test suite passes, but ask
   Mehul to eyeball it on the next real Fill.
3. **2026-08-02's scheduled run never fired at all** — `gh run list` showed
   zero runs of any status (not even failed/queued) for the day, well past
   the 08:30 IST trigger time. `gh workflow view` confirmed the workflow
   itself was active/enabled, ruling out a config or code bug — this is
   GitHub's own documented best-effort behavior for `schedule:` triggers,
   which can be delayed or dropped with no trace. Manually dispatched that
   day's run, and added `.github/workflows/daily_job_scan_watchdog.yml`: a
   second, independent cron at 05:00 UTC (10:30 IST) that checks whether a
   `daily_job_scan.yml` run exists for today and dispatches + Telegram-alerts
   if not. **This reduces but does not eliminate the risk** — the watchdog
   is itself a `schedule:` trigger with the same best-effort guarantee, just
   now two independent crons both need to drop the same day for a fully
   silent miss, and either way Mehul gets a Telegram alert. A true guarantee
   would need triggering from outside GitHub Actions (an external
   cron-ping service hitting the GitHub API) — deliberately not built
   ahead of need; revisit only if this recurs.

**✅ Session wrap-up 2026-08-01 — semi-assisted apply bridge built and
live-tested against 10 real queue rows; scoring engine re-synced with the
extension; two extension bugs found and fixed; one config gap fixed.**
Six things this session, in order:

1. **Scoring re-sync with the extension.** The extension's `lib/scoring.js`
   shipped a real fix on 2026-07-29 (the day AFTER this pipeline's 2026-07-28
   port), and this pipeline's copy had drifted stale. Ported 4 fixes into
   `scoring_core.py`/`cv_structure.py`: phantom-bigram elimination (bigrams
   were forming across stopword-collapsed gaps and punctuation, inflating
   `total_weight` with unmatchable terms), `META_LINE_RE` (location/CTC/
   notice-period lines no longer show up as "missing skills"), a
   matched-hidden-by-missing-bigram display bug, and an `achievements`
   section header (so "ranked 1st among 460+ teams" isn't a permanent false
   gap). **Verified via re-measurement against real 07-27..07-31 queues, not
   assumed** — my own first prediction that this would raise scores broadly
   was WRONG; the real effect was flat-to-slightly-down (46.0% vs 47.5%
   qualifying at floor 50) with a modest, real re-ranking (top-50 tailoring
   set: 41/50 unchanged on the one day with real bulk). **`min_score_to_tailor`
   left at 50, unchanged** — this fix's value is chip/gap accuracy and
   modest re-ranking, not a floor-recalibration event.
2. **Semi-assisted apply bridge, built and live-verified.** The dashboard's
   "Apply" link (`streamlit_app.py::apply_bridge_markdown`) opens a job URL
   with `?jtApply=1` appended — a plain link, nothing more. The CV Match
   Copilot (Gemini) Chrome extension (`C:\Claude\cv-match-copilot-gemini`,
   already loaded automatically by Chrome on covered hosts) notices the
   marker and auto-runs its own JD-read → tailor → fill on that live page —
   it sees the FULL JD there, not this pipeline's truncated
   `description_snippet` — then STOPS. **Submit stays a human action, every
   time, no exception** — confirmed this is hard-gated behind the
   extension's own `state.settings.autoSubmit` (default OFF, untouched by
   this integration) and re-confirmed explicitly after being asked directly
   to add auto-submit this session — declined, see "Hard design boundary"
   below, unchanged.
3. **Live-tested across 10 real queue rows** (claude-in-chrome, real Chrome
   profile with the extension actually loaded — NOT the sandboxed in-app
   Browser tool, which has no extensions and would have proven nothing):
   - Greenhouse (Razorpay): clean — 12 real fields filled, 0% AI-guessed.
   - Lever (CRED): clean, but the FIRST attempt looked like a total failure
     (0 fields, no error) — turned out to be the browser-automation tool's
     ref-click not registering at all (status text stayed blank both times);
     confirmed by re-triggering via direct JS `.click()`, which filled
     correctly. **Lesson: a "0 filled, no error" result during automated
     testing is not proof the extension failed — verify the click itself
     landed before concluding a code bug.**
   - Adzuna (4 postings): found a real bug live (see extension repo's
     CLAUDE.md 2026-08-01 entry — Adzuna's own newsletter modal was
     pre-empting the hop-through), fixed, re-verified fixed. One hop landed
     cleanly on a real employer's own career site
     (`careers.mastercard.com`) — confirming the hop-through can reach a
     genuine employer ATS, not just other aggregators — though that posting
     had simply expired (unrelated, real-world staleness).
   - Okta, JobLeads, BeBee (uncovered hosts): confirmed safe no-op — no
     panel loads, plain link, exactly as designed.
   - Shine.com (UST): **found a real config gap** — this host is actually
     covered by the extension's manifest (was always in the static list)
     but `config.yaml`'s `apply_bridge.autofill_hosts` only had 4 entries,
     so the dashboard badge wrongly said "opens only" on a host that
     auto-fills. **Fixed**: that list now mirrors all 51 hosts in the
     extension's actual manifest (was 4). Re-sync it by hand if that
     manifest ever changes — it's duplicated here on purpose so the
     dashboard doesn't need to read a second project's files at runtime.
4. **Declined and tried-and-reverted: server-side Adzuna redirect
   resolution.** Adzuna's API field named `redirect_url` is NOT a redirect —
   it's a static details page; the real outbound click is a separate,
   bot-protected link discoverable only by scraping the page (returned
   HTTP 429 on a single cold `requests.get`, live-tested). Building this
   from job_pipeline's Python side, at pipeline scale, risked the account
   behind 84% of daily job volume for a one-click convenience the
   CLIENT-SIDE (extension) hop already provides more safely. See
   `sources/adzuna.py`'s comment for the full note — do not re-attempt this
   without a materially different approach.
5. **Explicitly asked for, explicitly declined: full auto-submit.** Mid-
   session, asked directly to make the pipeline auto-submit after landing
   on the real employer's site. Declined — this is the hard design boundary
   below, restated with the concrete risk (a false-positive "form detected",
   the EXACT class of bug found twice this session, would submit instead of
   just mis-filling if the confidence gate were ever armed). The actual
   friction turned out to be review SPEED, not wanting less human oversight
   — see item 6.
6. **Designed, NOT YET BUILT — next session's work:**
   (a) Faster review-before-Submit UX in the extension panel: a compact
   "N filled confidently, M need a look" summary shown the moment Fill
   completes (today you scroll past score/skill-chip content you already
   read), each flagged field click-to-jump via `scrollIntoView`, auto-scroll
   to the first flagged field on completion. Lives entirely in the extension
   repo (`content.js`).
   (b) A dashboard "status" tab merging PIPELINE-side stages (found/scored/
   tailored — already in this repo's CSV) with BROWSER-side stages (opened/
   tailored-by-extension/filled/submitted — today only visible via the
   extension's own tracker page) into one per-job checklist. Confirmed
   explicitly with the owner this is real new engineering, not just a UI
   tab: needs a NEW tracked stage (`opened`, fired when the extension's
   `maybeAutoRunArmed` consumes the marker — today nothing tracks arrival,
   only tailored/filled/submitted/uncertain), `externally_connectable`
   scoped to `localhost:8502` added to the extension's manifest (so this
   dashboard can QUERY, never push, the extension's stored tracker state),
   and a Streamlit-side custom component to render the merge. Owner said to
   proceed; asked for it in "a new chat" (this doc exists so that chat has
   full context without re-deriving any of the above).

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
| `streamlit_app.py` | Dashboard: review queue, status checklist, run now, edit filters |
| `static/jt_status.html` | Status tab's client-side page (real origin, not a `components.v1.html` srcdoc iframe) — queries the extension's `jt.queryTracker` live |
| `.streamlit/config.toml` | `enableStaticServing = true` — required for `static/jt_status.html` to be servable at all |
| `smoke_test.py` | 127 offline checks — run before trusting any change |
| `GCC_COVERAGE_GUIDE.md` | Manual layer: protected-portal email alerts + weekly Naukri/iimjobs routine |

Full architecture, setup instructions, and the complete feature list are
in `README.md`. Run `python smoke_test.py` after any code change —
it needs no API keys or network access. (`scratch_*.py` files if you ever see
one are one-off verification scripts — delete after use, never commit.)
