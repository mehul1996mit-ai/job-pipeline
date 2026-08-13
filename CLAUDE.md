# job_pipeline — project context for Claude

Read this file first in any new session on this project. It has the
current status; `README.md` has full architecture/setup detail (including
a "Career Agent" section — read that first for A2/A3/A5/A8/A9 — and an
"Interview Prep Toolkit" section, read that first for anything about
resume claims, interview questions, or the "🗂️ Interview Prep" dashboard
tab) and `GCC_COVERAGE_GUIDE.md` has the manual-application layer.

**Interview Prep Toolkit (2026-08-11, re-architected 2026-08-12) — Phase 1
(Preparation) is fully built and live-verified; Phase 2 (Practice —
adaptive interviewer, live evaluation, mock interviews, readiness scoring)
does not exist and was repeatedly, deliberately declined across four master
prompts.** Dashboard tab is five tabs, each with one job: 🎯 Prep Plan (a
short, ranked, day-aware list of REAL questions — not internal topic
labels — this is the screen you actually prepare from), 📋 Resume Claims,
🗂️ Question Bank (every question, all base questions in their category,
not just a ranked slice), 📖 Story Bank (fully editable SITAR stories), ✅
Fact Review, plus a sixth tab added 2026-08-12: 🏢 Company Research
(company/industry facts, financial metrics, current-vs-target company
comparison, current-vs-target role comparison — see that day's entry
below). New files: `interview_store.py`, `interview_prep.py`,
`interview_stories.py`, `interview_answers.py`, `interview_llm.py`,
`interview_question_bank.py`, `interview_research.py` (2026-08-12),
`interview_ui.py` (dashboard tab), plus `interview_smoke_test.py`,
`answer_bank_smoke_test.py`, and `interview_research_smoke_test.py`
(2026-08-12). Read the README
section first for the architecture table; the detailed dated entries below
(search "Interview Prep" / "Answer Bank" / "Question Bank" / "Story Bank" /
"prep plan") have the full build history, every design call, and real bugs
found only by live-clicking the UI (a missing 429 retry, an `st.rerun()`
silently discarding writes, a process-switch that never switched, a
`StreamlitDuplicateElementKey` once the same question could appear in two
tabs, a fit score quietly computed over lexical JD-extraction noise) —
worth reading before touching `interview_ui.py`, `interview_prep.py`, or
`interview_llm.py` again. None of this touches the job pipeline,
`career_agent.sqlite3`, or outreach in any way — separate database,
separate tab, separate concern. **Each subsystem's SQLite database
(`interview_prep.sqlite3`, `career_agent.sqlite3`) only exists on whichever
Streamlit instance created it — nothing syncs a local DB to the hosted app
or back, by design; Mehul has chosen to work only on the hosted dashboard
(https://job-1357.streamlit.app) going forward, see the 2026-08-12 entry.**

**Career Agent's original 9-agent scope is now fully built (A2/A3/A5/A8/A9,
all extending job_pipeline in place) — see the A9 entry directly below.**
**2026-08-10: outreach sending is now real, gated behind a one-click human
approval per draft** — see that day's entry for what changed and why (F1
was renegotiated, not removed; full unattended auto-send was explicitly
asked for and declined). There are still ZERO real contacts/outreach in the
system, so A9's reply-detection and 30-day weight refit are real and tested
against synthetic fixtures but have nothing live to run against yet.
Outreach starts, and A9 starts having something real to track, only once a
contact is supplied (`user_existing_relationship`/`user_network_referral`/
`inbound_recruiter`) or a real posting yields an apply-by-email address —
check with Mehul on sequencing rather than assuming.

**✅ Interview Prep UI reworked into 5 tabs (2026-08-11), fixing real
clutter, not just cosmetics.** Mehul flagged the single-page Prepare screen
as cluttered and specifically asked for tab-based navigation between
question buckets, all base questions present in every bucket, and clearer
"what to prioritize"/"question tree" sections. `interview_ui.py` restructured
around `st.tabs`: 🎯 Overview / 📋 Resume Claims / 🗂️ Question Bank / 📖 Story
Bank / ✅ Fact Review (badge-counted), instead of one long page stacked with
dividers. Two real gaps found and fixed beyond the literal ask:
1. **The base question bank (83 questions, 10 categories) was capped at 10
   total anywhere in the UI** — `BASE_QUESTIONS_PER_PROCESS` ranks the top 10
   into `prep_topic` for prioritization, but the old `_base_questions_section`
   only ever rendered that ranked slice, so 73 of 83 questions were
   unreachable in the UI regardless of category. Question Bank tab now has
   one sub-tab per category showing every question in it (verified live:
   10/10 in "Introduction & Career", 10/10 in "Behavioral"); the ranked-10
   are starred (⭐) rather than being the only ones shown. This was the
   literal ask ("all base questions... in each bucket").
2. **New: an honest "Preparation coverage" bar** (generated/total questions
   drafted, reviewed-count) — deliberately a plain count, not a fabricated
   readiness score, same discipline as the fit rollup and the declined
   "Interview Readiness: 74%" earlier this session.
"What to prioritize" renamed **Focus List** and regrouped by *why* (JD
requirement gaps / high-risk claims / uncovered story competencies /
recommended standard questions) instead of one flat list mixing all four
source types with cryptic tags. "Question tree" renamed **Follow-up
questions** for clarity — same underlying 10 templated question types,
just clearer labeling. Story Bank and Fact Review got their own
tab-independent state (a claim selector inside Story Bank, defaulting to
whatever was last picked in Resume Claims) so switching tabs doesn't lose
context. Live-verified against the real ICICI Bank process (43% fit,
16/313 coverage, all 5 tabs render real data, no console errors); both
smoke suites (`interview_smoke_test.py`, `answer_bank_smoke_test.py`)
still pass — no backend changes, UI-only.

**✅ Generated answers/stories tuned for plain, human language (2026-08-11).**
Mehul flagged that generated prepared answers read as AI-written, not
something a candidate would actually say out loud. `interview_llm.py`'s
`ANSWER_DRAFT_PROMPT` (the main "Generate" path, used by every claim
question, metric-defense question, and base question) and
`STORY_DRAFT_PROMPT` both gained an explicit LANGUAGE STYLE directive:
plain conversational sentences over essay prose, no corporate jargon/
buzzwords ("leverage," "utilize," "synergy," "spearheaded," "delve,"
"robust," "streamline" as a verb), no AI-cliche filler ("it's worth
noting," "in today's fast-paced environment"), contractions allowed. This
sits alongside the existing I3 fact-integrity and active-voice rules, not
instead of them — style guidance only, no change to what facts/placeholders
are allowed. Live-verified against the real Gemini API (not just the
fake-double smoke tests): a re-generated answer for the Bajaj Finance
Personal Loan claim came back as plain first-person sentences with no
jargon, and a re-drafted story for the UI/UX-partnership claim kept correct
placeholder discipline while reading naturally. Both smoke suites
(`interview_smoke_test.py`, `answer_bank_smoke_test.py`) still pass
unaffected, since they test structure/guards via fake call doubles, not
prompt wording.

**✅ Generated answers now actually tailored to the target company/role/JD,
not just the resume claim (2026-08-11).** Mehul flagged that every answer
read similar and only ever talked about the current role — a real gap, not
a phrasing issue: `generate_answer_draft()`'s prompt never received the
target company, role, or JD text at all, only the question and the
resume-side claim, so there was structurally nothing for the model to
tailor toward. Fixed at the source: `interview_answers.
generate_answer_for_question()` now reads the active process's own
`company_name`/`role_title`/`jd_text` from `interview_process` and threads
them into `interview_llm.generate_answer_draft()` (new params, JD text
capped at 2500 chars). `ANSWER_DRAFT_PROMPT` gained a "THE CANDIDATE IS
INTERVIEWING FOR / JOB DESCRIPTION FOR THIS ROLE" block plus an explicit
rule to tailor emphasis to what THIS employer's JD actually cares about
rather than giving a generic answer. I3 stays fully intact: JD-text numbers
are added to the allowed-numbers set (citing "the JD asks for 5+ years" is
citing what the employer stated, not inventing a candidate fact) but a NEW
fact ABOUT the target company (revenue, team size, initiatives) beyond the
JD text is still rejected exactly as before. No caller changes needed — the
process context is read inside `generate_answer_for_question` itself, so
every existing call site (batch generation, single regenerate, base
questions) picks this up automatically. Live-verified: the identical Bajaj
Finance Personal Loan claim, answering the identical question, produced a
risk/compliance-framed answer for an ICICI Bank JD and a speed/scrappiness-
framed answer for a startup Growth PM JD — same underlying facts (27%/14%),
genuinely different emphasis, zero fact-integrity violations either way.
Both smoke suites still pass unaffected (fake-double tests, no process
context to thread).

**✅ Interview Prep re-architected around a ranked prep plan (2026-08-12) —
and the root cause of the "confusing focus list" turned out to be a data
bug, not a layout one.** Mehul asked to reorder the UI by interview
priority, flagging the coverage bar and focus list as low-value clutter.
Investigating first was the right call:

1. **The focus list was showing garbage, and it polluted the fit score.**
   `jd_analyst.analyze_jd()` is job_pipeline's BULK extractor (its own
   `_phrases()` docstring: "cannot tell a competency from a stray noun
   phrase, which is exactly why the LLM analyst overrides it where one is
   available"). Interview prep reused it and reads the output LITERALLY, one
   JD at a time — so it stored `'gathering'`, `'solution'`, `'seamless'`
   (matched!), `'highly motivated'` and one literally named `'key'` as
   must-have requirements, while the genuinely interview-relevant gaps sat a
   tier below. `_fit_rollup()` runs over that same set, so the 43% was not a
   number to trust either. Fixed with `interview_llm.analyze_jd_llm()` +
   `interview_prep.reanalyze_process_jd()` — one grounded LLM read per
   process (trivial next to the ~20 calls one claim's answers already cost),
   surfaced as an explicit "Re-read this JD properly" button rather than
   silently spent at intake (§4.2 requires intake stay synchronous).
   **Requirements are now 100% real** — live-verified: "regulatory, security
   and governance compliance in banking", "customer journey optimization",
   zero fragments.
   - **Deliberately NOT `merge_llm_analysis()`** for the skill tiers. That
     helper keeps a deterministic finding when the LLM returns empty, which
     is right for bulk scoring and wrong here: found live, the model
     correctly returned `must_have_skills=[]` for a JD stating no hard
     requirements, and the merge dutifully restored `'gathering'`/`'solution'`.
     Read literally by a human, a junk requirement is worse than none.
   - Follow-on caught same session: `open_gaps()` originally filtered to
     `must_have`/`preferred`, which showed ZERO gaps on exactly that kind of
     JD. Now ranks across all tiers.
2. **Focus list → ranked prep plan, question-shaped.** The old list grouped
   by the SYSTEM's internal `prep_topic.source` (requirement_gap /
   high_risk_claim / ...) and phrased every row as a topic ("Defend the
   ownership/impact of: ..."), which is a description of work, not something
   anyone actually says to you. New `interview_prep.build_prep_plan()` ranks
   real questions by likelihood × stakes, discounted by preparedness, and is
   **day-aware** (`scheduled_date` was previously displayed and then totally
   unused): 1 day out → 10 items, 14 days → 40. Three real ranking bugs
   found by reading the output rather than trusting the maths, each now a
   standing smoke guard:
   - 7 of the top 10 were two repeated question texts (a template exists per
     claim) → `_MAX_REPEATS_PER_QUESTION`.
   - A 1-day plan came back with ZERO standard questions — no "tell me about
     yourself", no "why us" — because claim-defense outscored them all →
     `_BASE_QUESTION_FLOOR`.
   - "Tell me about yourself" *still* vanished once it had a draft. Root
     cause was `CATEGORY_BASE_IMPORTANCE` being too coarse (all 10 intro
     questions shared one prior, so the most certain question in any
     interview tied with "where do you see yourself in 3-5 years") → new
     `interview_question_bank.QUESTION_LIKELIHOOD` per-question overrides,
     plus `_ALWAYS_REHEARSE`: at likelihood ≥0.9 a question is never
     discounted for already having an answer. **That was a framing error on
     my part — I'd built a gap-filler when a day-out list needs to be a
     rehearsal list.**
3. **Coverage metric fixed, not just demoted.** "6 of 313 (2%)" measured
   against every question that exists; nobody drafts 313, so the denominator
   was unreachable by construction and real effort read as no progress. Now
   counts the ranked plan ("3 of 10 priority questions"), and sits in a
   collapsed "Where you stand" expander BELOW the plan and gaps.
4. **Two genuinely new, zero-backend additions**: drill mode (hide answer →
   say it out loud → reveal; retrieval practice, not re-reading, is what
   survives into the room) and a one-page markdown prep-sheet export (the
   hour before an interview you are on a phone in a lobby, not clicking
   through five tabs).

**One real bug found only by live-clicking**, same class as this file's
earlier entries: the prep plan surfaces questions that also live under
Resume Claims, and Streamlit executes every tab body on every run, so the
shared `_answer_editor()` widget keys collided outright
(`StreamlitDuplicateElementKey`). Fixed with a caller `scope=` prefix on
every key. 10 new smoke guards; `interview_smoke_test.py`,
`answer_bank_smoke_test.py`, `smoke_test.py`, `career_agent_smoke_test.py`
all pass.

**Where I pushed back on Mehul and he was wrong:** he bucketed "Claims that
need a strong defense" with the noise as low-value. That section is the
highest-value thing in the app — finding the biggest number and probing
ownership is *literally what an interviewer does*. It read as low-value
because I rendered it badly (truncated mid-word, phrased as a topic, no
action, visually identical to the `'seamless'` junk above it), not because
the content was wrong. It now sits at the top of the plan as real questions.

**✅ Company research + current-vs-target comparison built (2026-08-12) —
scoped down from a much larger 77-section "Interview Intelligence OS"
master prompt after an explicit audit found most of it unbuilt.** A new
master prompt asked to build everything the audit flagged missing:
full company/financial/competitor research, mock interviews, rapid fire,
final-day mode, a 0-100 readiness score, numeric answer scoring, a
Next.js/FastAPI/Postgres rewrite. Pushed back on most of it before
building anything (Mehul's own instruction: "challenge it with what's
already built first, then choose whether to build or not") —

**Declined, and why:** the stack rewrite (throws away a live, tested app for
zero interview-prep value, same reasoning that killed two prior navy/indigo
re-theme requests); mock interview/rapid-fire/final-day (real, but lower
value than the comparison feature actually asked for, and correctly
deferred to a follow-up rather than half-building five things at once —
this repo's own history shows that pattern fails, e.g. the fourth master
prompt Mehul himself stopped mid-implementation); a 0-100 readiness score
(already declined twice before, on E3/I7 grounds — a composite number over
sparse inputs is false precision); numeric answer scores like "6.8/10"
(free-tier LLM judging a spoken answer has real test-retest variance —
built a non-numeric structured checklist instead, see below).

**Built:** `interview_research.py` — industry classification (§7, keyword-
deterministic, zero LLM cost, labels itself fact vs inference per §36),
an industry-specific financial-metric library (§8 — banking/NBFC/housing
finance, insurance, SaaS, e-commerce, manufacturing, plus a narrow generic
fallback rather than guessing a bespoke list per industry), company
research + metric collection via Gemini's grounded `google_search` tool,
and — the actual literal ask — current-vs-target **company comparison**
(§12/13, explicit comparable/partially-comparable/not-comparable labeling,
never a forced comparison across mismatched industries) and current-vs-
target **role comparison** (§22, fully deterministic from the already-
parsed CV + JD, zero research/LLM dependency, so it works even when
company research doesn't). New tables in `interview_store.py`: `company`,
`company_fact`, `company_comparison`, `role_comparison`. New UI tab
`🏢 Company Research` in `interview_ui.py`, inserted between Prep Plan and
Resume Claims. `interview_research_smoke_test.py` — 17 offline checks
(industry classification, idempotent company rows, fake-double research/
metrics calls, `ResearchUnavailable` never silently swallowed, comparison
labeling including the cross-industry not-comparable case, role comparison
persistence) — all pass, plus all four pre-existing smoke suites
re-confirmed green (no regressions).

**Real technical finding, not assumed:** live-tested the grounded
`google_search` call against the real Gemini key before building anything
on it, per this repo's own "verify the primary source" discipline (the
Fibe/Tata Capital/Razorpay false-lead catches earlier in this file). Plain
generation calls succeed (HTTP 200); the grounded call returns
`429 RESOURCE_EXHAUSTED` specifically — search-grounding sits on its own,
separate, much stingier free-tier quota from plain text generation, not a
shared pool. `research_company()`/`collect_financial_metrics()` raise
`ResearchUnavailable` rather than silently falling back to an ungrounded
model guess about a real company's financials — confirmed live in the UI
(clicking "Research target company" against the real ICICI Bank process
surfaced the clean error message, no crash, no fabricated data). **Company
research is built but not yet actually usable until the grounding quota
resets or a different key/tier is used** — this is a real, live-verified
gap, not a hypothetical one. Role comparison and the comparison-table
mechanics needed no live grounding to verify and were confirmed against the
real ICICI Bank process data (6 dimensions, all correctly pulled from the
candidate's actual CV bullets — including one `dont_overemphasize` case,
product ownership, where the CV shows it strongly but the JD doesn't
emphasize it, exactly the §22 distinction the spec asks for).

**One explicit, deliberate departure from this repo's own I3 fact-integrity
discipline, on direct instruction:** company/industry facts
(`company_fact` rows) have **no confirmation gate and no stored source
URL** — they write directly and are usable immediately in generated
content, the same trust level as the candidate's own resume claims. Raised
once, with the concrete mechanism (this repo has three confirmed incidents
of a wrong AI-summarized company fact — Fibe's CPO, a Tata Capital
appointment, a Razorpay contact email — each caught only because something
forced a verify-before-use step); Mehul overruled it explicitly after
hearing the objection. Standing going forward: if a wrong company fact
surfaces in a real interview answer, this is the known, accepted tradeoff
that produced it, not a bug to silently patch back to a gated design.

**Deferred to a follow-up session, not stubbed:** mock interview with
personas (§29/30), a non-numeric structured answer-evaluation checklist
(§31, scoped down from the declined numeric score), final-day mode (§42),
questions-to-ask-the-interviewer (§43). None of these need company research
to already exist except final-day/questions-to-ask, which are gated on it
anyway.

**✅ Full-repo audit (7 correctness/reuse/simplification/efficiency findings,
all fixed same session, 2026-08-12).** A general "audit the full job_pipeline
and make it better" request was scoped via the `code-review` skill in
path-target mode (8 finder angles + 1-vote verify, covering the core daily
pipeline, Career Agent, and Interview Prep). All 7 confirmed findings were
fixed and re-verified against all 5 smoke suites plus a live browser check:
1. **matcher.py's `passes_filters()` still hard-rejected over-experienced
   jobs via the old `experience_ok()` check before `score_job()`/
   `seniority.judge()` ever ran** — directly contradicting the documented
   2026-08-09 redesign that made over-seniority a soft penalty. A JD stating
   "8+ years" was silently dropped from the queue entirely, even though
   `comfort_max_years=8.0` wouldn't have flagged it. Fixed: `experience_ok()`
   removed from `passes_filters()`'s hard-AND chain (kept as a function,
   still smoke-tested directly — just no longer used as a pre-filter).
   **This is the highest-value fix in the batch** — it was silently shrinking
   the live daily queue.
2. **`ratelimit.drafts_created_today()` compared OS-local date against a
   UTC-stamped `created_at`** — the F4 20-drafts/day cap on the real
   Gmail-send path could be exceeded in the 18:30–05:30 IST window. Same bug
   fixed in `outreach_crm.due_followups()`. Both now compare UTC to UTC.
3. **`outreach_crm._outcome_rows()` read `authority_node.seniority_band` and
   treated it as the A3 `node_type` enum** — worked only by coincidence
   (nothing else had ever written a real seniority value there). Fixed with
   a real `node_type` column (`outreach_store.py`, idempotent migration +
   one-time backfill from the old misused column), updated the write path
   (`authority_graph.py`) and both read sites.
4. **`interview_prep._base_question_topics()` ranked base questions via the
   coarse `CATEGORY_BASE_IMPORTANCE` prior instead of
   `qb.question_likelihood()`'s per-question overrides** — the Question Bank
   tab's ⭐ ranking could disagree with the Prep Plan tab's ranking for the
   identical question. Fixed: both now call the same per-question function.
5. **The candidate's CV was re-tokenized from scratch inside every
   `matcher.score_job()` call** (up to 3x per job listing across a daily
   run — initial pass, Workday full-JD rescore, post-tailoring rescore) with
   zero caching anywhere. Fixed with optional `cv_index`/`skill_cv_index`/
   `skill_cv_lower` params threaded from `main.py` (computed once, CV is
   constant for the whole run) through `matcher.score_job()` →
   `frozen_score()`/`structured_skill_match()`, falling back to recomputing
   when not supplied so no other caller needed to change.
   **Verified byte-identical scoring output** (same score/frozen_score/
   sub_scores/flags) between the precomputed-index and fallback paths before
   trusting it — a perf change to a formula this project has calibration
   guards around gets zero benefit of the doubt.
6. **`interview_llm.py` hand-rolled the same 429/503 retry-after-pause logic
   that already existed in `tailor.py`**, as two independently-maintained
   copies (the module's own docstring admitted this). Factored into
   `tailor.call_with_retry()`, reused by both callers — a backoff change now
   only has to land once.
7. **The commit-before-`st.rerun()` pattern was copy-pasted at 20 separate
   sites in `interview_ui.py`** instead of centralized — the exact bug class
   this project's own history documents being found and fixed multiple times
   already (see the 2026-08-04 entry). Centralized into
   `_commit_and_rerun(conn)`; all 20 sites converted via a scripted
   transformation that asserted each site matched the exact expected pattern
   before rewriting (would have raised on any mismatch — none did).
   **Verified the actual mechanism directly** rather than trusting a browser
   click: simulated `st.rerun()`'s exception-based unwind against a real
   `interview_store.connect()` block and confirmed the write survives the
   exception skipping past the context manager's normal-exit-only commit —
   the identical failure mode the 2026-08-04 bug was about. (Live browser
   verification hit two unrelated preview-server disconnects with zero
   traceback in either server log — consistent with prior sessions'
   documented browser-tooling flakiness, not a code issue; the direct
   mechanism test is arguably the more rigorous check anyway.)

Two additional candidates were checked and **refuted** during the audit's
verify pass, not reported: a claimed provider-registry drift risk in
`interview_llm.py` (a real runtime guard already rejects any non-free
provider, not just a docstring promise) and a claimed dict-equality dedup
bug in `interview_prep.build_prep_plan()` (the dedup key includes
`question_ref_id`, which is a DB primary key — two genuinely distinct
questions can't collide).

**✅ Story Bank rebuilt, and "incomplete claim lines" turned out to be a
display bug, not corrupted resume data (2026-08-12).** Mehul reported two
things; both were real, neither was what the label suggested.

1. **"Claims contain incomplete lines"** — checked `resume_master.json`
   directly first: every bullet is complete, verbatim, no truncation at the
   source. The actual bug was hard `text[:90]`/`[:100]`/`[:60]` slices with
   NO ellipsis at four call sites (claim selectbox in Resume Claims, claim
   selectbox in Story Bank, the prep-plan context line, the story-draft
   title) — e.g. "...Personal Loan product, gro" reads exactly like a
   corrupted resume even though the underlying claim text was always whole.
   Fixed with one shared `interview_ui._trunc()`: cuts at the last word
   boundary and appends a real ellipsis, used everywhere claim text is
   shown short. 3 new smoke guards assert no mid-word cut is possible.
2. **Story Bank "needs to be simpler" — audited, and it was actually
   incomplete, not merely cluttered.** Before this pass, a drafted story
   showed only its `title` + `result` field with no way to read or edit
   `situation`/`task`/`action`/`reflection` at all, and **no `update_story()`
   function existed anywhere in the codebase** — a "first-pass draft for the
   candidate to edit" (§4.7's own framing) could be created and then never
   touched again. Worse: every story ALSO has 8 supporting fields
   (`team_size`, `exact_role`, `decision_made`, `stakeholders`, `metrics`,
   `tradeoff`, `failure`, `learning`) that `create_story()` defaults to a
   literal `"[YOU FILL: ...]"` placeholder — I4's entire mechanism assumes a
   human notices and fills these, and nothing in the UI ever showed them.
   Fixed:
   - New `interview_stories.update_story()` / `delete_story()`. Edits are
     trusted at the same level as a hand-typed prepared-answer edit (E6's
     authored-content trust tier) — NOT run back through I3's
     regenerate-then-hard-fail path, since that path exists to catch a
     MODEL inventing a fact, not to second-guess the candidate typing their
     own number.
   - `interview_ui._story_card()`: every story is now an expander showing
     all five SITAR fields, each independently editable and saved via
     `update_story()`, plus a nested "Fill in the details (N left)"
     expander surfacing the 8 previously-invisible fields, plus a Delete
     button.
   - Coverage gaps went from a plain caption sentence
     ("No story mapped yet for: leadership, ownership, conflict...") to
     styled chips covering every competency, gap vs. covered, matching the
     chip language used everywhere else in the app.
   Live-verified against the real ICICI Bank process: opened an existing
   drafted story, confirmed all 5 SITAR fields render, confirmed the
   8-field "Fill in the details" expander opens correctly. 5 new smoke
   guards (create → edit → verify persisted → delete → verify gone).
   **Noted but not touched**: one pre-existing story in the live DB (drafted
   in an earlier session) has an angle-bracket placeholder
   (`<describe specific technical steps...>`) instead of the
   `[YOU FILL: ...]` format — predates this session's placeholder-format
   work, `_render_fillin_blanks()` won't style it specially, but it's now at
   least visible and editable where it wasn't before.

All four smoke suites (`interview_smoke_test.py` — 13 new checks total this
pass, `answer_bank_smoke_test.py`, `smoke_test.py`,
`career_agent_smoke_test.py`) pass.

## STATUS (last updated 2026-08-12)

**🟢 Interview Prep & Practice toolkit — Phase 1 (preparation) built,
Phase 2 (practice/evaluation) deliberately not started.** A separate master
prompt asked for a 16-section interview-prep subsystem inside job_pipeline
(claim extraction, JD gap mapping, story bank, an anti-noise evaluation
engine, mock interviews, mastery/spaced-repetition, readiness, company/
segment research). The prompt's own §2 argued for building a thin P0 slice
through *both* phases first, specifically to de-risk the evaluation engine
(its own words: "everything downstream is decoration on noise" if answer
scoring isn't reliable). I raised that once; Mehul asked for Phase 1 built
first regardless, then explicitly deferred to my judgment — so Phase 1 was
built in full, Phase 2 (the evaluation engine, mock interview, mastery,
readiness) was not started at all this session, not even as a stub.

**New files**, all offline/zero-API-key by design — see the design decision
below: `interview_store.py` (I2 — separate `data/interview_prep.sqlite3`,
`INTERVIEW_DB_PATH` overridable, mirrors `outreach_store.py`'s pattern),
`interview_prep.py` (§4.1–§4.6: candidate model, claim extraction, question
trees, metrics defense, JD intake → `InterviewProcess`, prep-topic
generation), `interview_stories.py` (§4.7: story bank, I3 fact-integrity
check, I4 placeholder discipline, competency coverage-gap detection),
`interview_smoke_test.py` (32 offline checks, all pass — `python
interview_smoke_test.py`).

**Design decision: all of Phase 1 is deterministic, zero LLM calls.** The
master prompt implied generated content throughout; on inspection, every
Phase 1 output is actually a mechanical transform of `resume_master.json`
or the JD text — claim extraction is one `ResumeClaim` per bullet copied
verbatim (metric/ownership/risk_level all regex-derived, not generated),
the question tree and metrics-defense set are fixed templates instantiated
per claim, and JD gap mapping reuses `jd_analyst.analyze_jd()` (already
deterministic) + `skill_match.match_skill()`/`index_layers()` (already
built, layered exact/alias/stem/phrase matching) rather than a new
embedding stack. This is a real departure from the spec, not just an
implementation detail: it means Phase 1 structurally cannot violate I3 (no
model call exists that could assert an unsupported fact) and satisfies I6
for the whole phase rather than just the tests. `tailor.py`'s fact-
integrity validator (`rewrite_is_safe()`) turned out not to be directly
reusable as I1/I3 implied — it's shaped specifically for rewritten-bullet
pairs, not free text — so `interview_stories.check_fact_integrity()` is a
new function applying the same discipline (every numeric value in a story
field must trace back to a number literally present in
`resume_master.json`) to the story-bank input shape instead.

**Follow-up same session: LLM generation added back in, scoped to where it's
actually load-bearing, free-tier only.** Mehul asked to use the master
prompt's implied LLM generation "wherever it will be helpful, but only
free." Pushed back once: the question tree and metrics-defense templates
already do their job well and don't need a model call. The one place
generation is genuinely additive is the story bank's "guided capture"
(§4.7) — drafting a first-pass Situation/Task/Action/Result/Reflection
narrative from a bare resume bullet is real value a template can't produce.
New file `interview_llm.py`: reuses `tailor.py`'s gemini/groq call plumbing
directly (both free-tier; **anthropic is deliberately excluded** — this
repo's `tailor.anthropic_model` is a paid Opus model, not a free tier, and
is never selected here regardless of `config.yaml`'s `tailor.provider`).
Every generation function takes an injectable `call_fn`, so
`interview_smoke_test.py` tests it with a fake double and needs zero API
keys — 7 new checks, still fully offline. I3 is enforced exactly as
designed: a fact violation (or an incomplete response) triggers ONE
regeneration with a stricter reminder, and a second violation raises
`InterviewFactIntegrityError` — never a silent pass, never a silently
half-filled story. `interview_stories.draft_story_from_claim()` wires it to
the store, going through the exact same `create_story()` I3/I4 path a
manually-typed story would, so nothing generated bypasses the normal
insert-time checks. **Live-verified against the real Gemini API same session** (I6 governs the
*test suite* being offline, never the feature itself — the default
`call_fn` always calls the real free-tier provider; only
`interview_smoke_test.py` swaps in a fake double). First live call passed
fact-integrity on attempt 1, no regeneration needed, correctly placeholdered
the one genuinely unstated implementation detail — but the prose was
noticeably passive/stilted ("were led... were led"), a real quality problem
a fixture-only test would never have caught, since the model was
over-correcting against the invent-nothing constraint. Fixed by tightening
`STORY_DRAFT_PROMPT` to require first-person active voice explicitly ("I
led...", never "was led"), re-verified live — output now reads as
practiceable spoken material while keeping every fact and the placeholder
discipline intact. All three smoke suites re-confirmed green after the
prompt change.

**Second follow-up same session — one real bug fixed, one more genuine LLM
use added, both from re-reading my own code harder rather than new scope.**
Mehul repeated the "make this tool best, choose accordingly" instruction as
a direct check that I'd understood it as license to look for more value,
not just confirm the prior turn. Found:
- **A real bug**: `build_candidate_profile()`'s `differentiators` field was
  dead code since I wrote it — always `[]`, with a comment saying the
  caller should populate it from `resume_master.json["achievements"]`, but
  nothing ever did. Real signal (Spotlight Award, two national
  case-competition placements) was silently discarded. Fixed —
  deterministic, not an LLM question, just an oversight. The `ON
  CONFLICT... DO UPDATE` clause was also missing
  `differentiators_json=excluded.differentiators_json`, so even a future
  fix to populate it would have silently failed to persist on re-run; fixed
  in the same edit.
- **A second genuine LLM use**: prep-topic rationale ("why an interviewer
  actually probes this, what a weak vs. strong answer signals"). Lower
  fact-risk than story drafting — it reasons about a JD requirement or
  claim generically, never a candidate-specific number — so the guard is
  simpler: any digit in the output that isn't already in the topic's own
  text is treated as invented and rejected (same one-regeneration-then-
  hard-failure I3 pattern). New `interview_prep.enrich_topics_with_
  rationale()` runs as a separate, optional pass AFTER topic generation —
  §4.2's requirement that the topic list itself stay synchronous is
  preserved; this only lazily backfills `prep_topic.rationale` (new
  column) for rows that don't have one yet, so it's safe to call
  repeatedly without re-spending calls. Live-verified against the real
  Gemini API (not just the fake-double tests) — clean output, no
  regeneration needed, no invented figures. 8 new offline checks
  (fake-double coverage of the accept/reject/regenerate/skip-already-done
  paths) plus the live call, all pass; `smoke_test.py` and
  `career_agent_smoke_test.py` still green.

**✅ Fifth master prompt (a full "World-Class UI/UX" redesign spec) — same
navy/indigo "Linear × Notion × Stripe" direction declined a second time,
two genuinely missing pieces built instead.** Mehul's own instruction this
round: "don't accept everything, only do what's better than what we have
built." The re-theme request repeats the previous UI prompt's ask almost
verbatim — held the same position for the same reason (the case-file token
system is tested, live, and matches this project's own original design
brief's explicit instruction to avoid generic AI-SaaS defaults). Also
skipped: the 4-screen onboarding wizard (the existing compact "+ New
process" form already does the same job in fewer steps — building the
wizard would be MORE ceremony, not less, which the spec's own philosophy
argues against) and a standalone Resume Defense page (metrics defense
nested under its claim is arguably better than a flat separate list — you
see the defense right where the claim is). All of Phase 2's UI (interview
room, AI interviewer avatar, mock setup, radar charts, follow-up survival,
progress-over-time) still has no backend, so still nothing to build there.

Two things WERE genuinely missing and got built:
1. **Question Bank** (`interview_ui._question_bank_view()`) — until now,
   questions lived in two disconnected areas (claim picker → question tree,
   and a separate base-questions list) with no single place to see
   everything, filter by category, or sort by priority. New expander
   flattens claim_question + metric_defense (global per T§14) + this
   process's base_question_bank selection into one filterable/sortable
   table — a consolidation of existing data, not a new question source.
   Live-verified: 240 rows for the ICICI Bank process, exactly matching
   17 claims × 10 tree questions + 6 metric-bearing claims × 10 defense
   dimensions + 10 base questions.
2. **Fit rollup** (`interview_ui._fit_rollup()`) — the JD match data already
   existed per-requirement (matched/partial/gap via `requirement_match`)
   but nothing aggregated it into one number. Weighted by tier (must-have
   counts 3x) into a single "Your fit against this JD: 43%" — explicitly
   labeled as a rollup of existing calls, not a new judgment, so it doesn't
   read as a fabricated score the way the declined "Interview Readiness:
   74%" would have.

**✅ Two genuinely new Phase-1 capabilities added, everything else in that
huge fourth master prompt deliberately left out.** A fourth prompt
("AI Interview Preparation & Practice OS") re-specified the whole product
top to bottom, overlapping and partly conflicting with everything already
built. First response to it was to start implementing it wholesale
(company-research web-search engine, a parallel prioritization table) —
Mehul stopped that directly ("think and do and don't blindly do
everything"), which was the right call: most of it either duplicated
`prep_topic`'s existing priority mechanism or was a real accuracy risk
(this repo's own A3/A5 sessions hit real "WebSearch summary was simply
wrong" incidents) that deserved its own careful session, not a rushed
bolt-on. Reconsidered and built only what was genuinely missing:

1. **Base question bank** (`interview_question_bank.py`) — the real gap.
   Every question generated up to this point came FROM a resume claim; there
   was no "Tell me about yourself," no PM fundamentals, no behavioral, no
   product-sense case unless a bullet happened to map to one. 83 curated,
   static questions across 10 categories (deliberately excludes "current
   project deep-dives" and "resume claim defense" — the claim-tree and
   metrics-defense set already cover those better than a generic version
   could). Zero LLM cost to define — these are standard, well-known PM
   interview questions, same reasoning that kept the rest of Phase 1 off
   the LLM. Folded into the EXISTING `prep_topic` mechanism (capped at 10
   per process, ranked by category importance + lexical JD/role tag match)
   rather than a new parallel scoring table — extending what already works
   instead of duplicating it. `generate_answer_for_question` synthesizes a
   context claim from the candidate's resume summary when a base question
   has no single claim behind it, reusing the exact same generation/I3 path
   rather than a second code path.
2. **Critique** (`interview_llm.critique_answer()`) — T§5.5's feedback
   format (Observation/Why it matters/How to improve), finally implemented
   as a working action. Previously "Evaluate" was the only feedback button
   and it's honestly stubbed (no engine exists) — there was nothing between
   that stub and a full Regenerate. Lower-stakes than generation (never
   stored as fact, always transient), so it gets a lighter check than I3's
   full regenerate-then-hard-fail: a `quote_verified` flag confirms the
   observation actually quotes the candidate's own words verbatim rather
   than raising on a miss.

**§21/§22's "Interview Readiness: 74%" was NOT built as specified** — it
conflicts directly with E3 ("prepared answers never confer readiness") and
I7 (readiness must be an uncalibrated-labeled band, never a bare
percentage), both already enforced. Preparation Coverage is the honest
substitute (legitimately a percentage — "how much have you prepared" isn't
a readiness claim) but wasn't built this session either; the dashboard
rework (§22/§28) was intentionally skipped along with company research —
see below. **§27's UI direction ("deep navy/charcoal... Linear × Notion ×
Stripe") was also not applied** — it's close to the generic AI-design
default the earlier UI spec explicitly said to avoid, and the case-file
token system is already live-tested and working; re-theming it now would
throw away something proven for something the project's own design brief
warned against.

**Two more real bugs found live** (both only surfacing from actually
clicking through the UI, neither catchable by the offline fake-double
suite): creating a new interview process never actually switched to it —
`st.radio`'s own persisted widget state silently overrode the
`session_state["ib_active_process_id"]` assignment on the next rerun. First
fix attempt (`st.session_state["ib_process_radio"] = new_idx` right in the
form handler) hit a second, different failure —
`StreamlitAPIException: cannot be modified after the widget ... is
instantiated`, since that assignment ran AFTER the radio widget had already
rendered earlier in the same script pass. Fixed properly with a
pending-selection pattern: the form handler stores a plain (non-widget)
`_ib_pending_process_id` marker, and `_process_switcher()` resolves it into
the real `ib_process_radio` key at the very top of the function, before the
radio widget is instantiated in that run. Live-verified: created a second
process, confirmed it became active immediately, generated a real base
answer against it, then cleaned up the test processes from the DB
(FK-ordered deletes: `requirement_match` → `jd_requirement` → `prep_topic`
→ `prepared_answer_version` → `fact_candidate`/`fact_ledger`/
`resume_discrepancy` → `interview_process`).

**Explicitly not built, and why**: the Target Company Engine (real
web-search-backed company research) and the prep-dashboard rework —
both flagged as real, separately-scoped work rather than rushed in. Answer
Coach's numeric score ("6.8/10") wasn't built either — Critique gives
qualitative T§5.5 feedback without a number, since a real numeric score
belongs to Phase 2's ordinal-level evaluation engine (T§5.2), not a
Phase-1 stand-in that would need its own, different rubric to be honest.

**✅ Interview Prep UI built and live-tested against a real JD (ICICI Bank —
Digital Product Manager) — two real bugs found and fixed by actually using
it, not just by testing.** A third companion prompt asked for a Streamlit
UI over T§/E§. Scoped honestly: Practice/Readiness/Interview-Day screens
all need Phase 2 (evaluation engine, mastery, readiness, mock interview),
none of which exists — built only what has real backend behind it: the
process switcher (create a process from a pasted JD, switch between them)
and the full 🎯 Prepare screen (claims, question tree, metrics defense,
story bank, fact review queue), styled per the token system (dark ink/
paper/brass palette, the `[YOU FILL: ...]` fill-in-blank device rendered as
an actual underlined blank in a read-only HTML preview above the editable
textarea — Streamlit can't style inside an editable widget, so that's the
honest compromise). New file `interview_ui.py`, one new tab added to
`streamlit_app.py` (`🗂️ Interview Prep`) rather than forking the app.

**First real bug, caught by an actual live click, not a fixture:** batch-
generating a claim's ~20 questions (question tree + metrics defense) hit a
live `429 Too Many Requests` from Gemini. Root cause: `tailor.py`'s own
`tailor_job()` retries once on 429/503, but that retry lived only in the
caller, not in `_call_gemini`/`_call_groq` themselves — `interview_llm.py`
calls those directly, so every function in it skipped the retry entirely.
Fixed with the same retry-after-a-pause discipline in
`interview_llm._default_call_fn`, plus `BATCH_CALL_PACING_SECONDS` (4s)
between calls in `interview_answers.generate_answer_batch` so a claim's
batch doesn't fire ~20 calls back to back in the first place.

**Second, more serious bug, caught because a retry after the first fix
still showed zero answers with no visible error:** `interview_ui.render()`
holds one shared DB connection open for the whole page render via
`with interview_store.connect() as conn:`, and `connect()`'s context
manager only calls `conn.commit()` after that block exits *normally*.
`st.rerun()` halts script execution by raising an internal exception —
confirmed by reading its own docstring and source — which unwinds through
that `with` block exactly like any other exception, skipping the commit.
Every write-then-rerun action in the UI (batch generation, single-answer
regenerate, revise, fact confirm/reject, story mapping) was silently
rolling back. Fixed two ways, not one: `conn.commit()` inserted before all
12 `st.rerun()` call sites in `interview_ui.py` (belt), and
`generate_answer_batch()` now commits after *each* question rather than
once at the end of the whole batch (suspenders — also fixes a separate real
problem: a free-tier batch failing on question 15 of 20 used to discard
the 14 already-generated answers along with it). Neither bug was catchable
by `answer_bank_smoke_test.py`'s fake-`call_fn` tests, since they don't
simulate Streamlit's rerun-via-exception mechanism or real rate limits —
**this is why the live-testing step mattered**, not just the offline suite.

**Live-verified end to end** against a real pasted JD: JD intake created
one `interview_process` row, extracted real requirements, generated real
prep topics (gap/high-risk-claim/uncovered-competency union, matching the
backend's own logic), and batch-generated real answer drafts that
committed incrementally and read correctly — first-person, active voice,
fact-grounded, `[YOU FILL: ...]` used only where the resume genuinely
doesn't say more. `.claude/launch.json` added (port 8599, separate from the
watchdog-managed production dashboard on 8502) for browser-tool preview
access; scratch server stopped at session end, not left running.

**Not built**: §9 AI-baseline diagnosis, §10 voice profile, §11 question
feedback, company/segment research tabs (T§11) — same deferrals as the
backend session, now also reflected in the UI (those screens simply don't
exist yet rather than rendering empty).

**✅ Answer Bank companion subsystem built (E1-E7) — build order items 1-3
of 6.** A second companion master prompt (Answer Bank: generate/author/
revise/correct prepared answers, fact-ledger enrichment, conflict
resolution against the resume) arrived as a Phase-1-only extension. Flagged
once that §6.1 (fact detection) and §12 (evaluate button) both assume T§5's
Step A extraction/evaluation engine, which was never built (Phase 2 stays
fully deferred) — built a standalone, narrower fact-detector instead of
faking the fuller Step A schema, and implemented §12's guards/policy
(cache-by-identical-text, 10/day cap, rubric-version tracking) with the
actual scoring call returning `{"status": "unavailable"}` rather than a
fabricated score.

**New files**: `interview_answers.py` (append-only `prepared_answer_version`
store; the four operations — `generate`/`author_answer`/`revise_answer`/
`correct_extraction`/`correct_import`, with `correct_extraction` hard-
refusing a changed `body_text` per E§1.3; `review_depth` auto-classified
from edit distance; §6 fact detection via a new lightweight deterministic
extractor — NOT the full Step A schema — with conflict resolution (E5: a
value conflicting with the claim's own resume metric halts as `conflicted`
and never touches `resume_claim`/`resume_master.json`, only writes a
`ResumeDiscrepancy` once resolved); `evaluate_prepared_answer()`'s E7
guards). `interview_llm.py` gained `generate_answer_draft()` — same
free-tier-only, I3-gated pattern as the story/rationale generators.
`answer_bank_smoke_test.py` — 33 offline checks (fake `call_fn` doubles),
all pass; `smoke_test.py`, `career_agent_smoke_test.py`, and
`interview_smoke_test.py` all still green.

**Two real regex bugs caught during testing, fixed, not just tolerance-
widened:** the fact-detector's number regex matched a bare unit letter mid-
word (`"6 months"` → value `6`, unit `"m"`, from the `m` in `months`) —
fixed with a negative lookahead so a unit match can't land inside a longer
word. And the baseline/timeframe classifier used a wide symmetric context
window, so `"The baseline was 12%... I grew it to 27% over 6 months"`
classified **27 as baseline** (bleeding in an earlier sentence's "baseline"
mention) and **27 as timeframe** too (bleeding in a later "over 6 months"
phrase that actually describes 6, not 27) — fixed by requiring immediate
adjacency (before/after ~15 chars) for team-size/timeframe cues and a
clause-boundary-aware lookback (stops at the nearest `.`/`,`/`;`) for
baseline, so each number is classified from its own clause only.

**Live-verified against the real Gemini API, and a real prompt-quality
issue found and fixed the same way as the story-draft session:** first
live call for `generate_answer_draft()` passed I3 cleanly but was over-
hedged — it placeholdered "my role was..." and "the team handled..." even
though the underlying claim ("partnered with UI/UX designers to streamline
the application experience") already states the action directly. The
prompt didn't distinguish "genuinely missing fact" from "detail the claim
already implies." Fixed by instructing the model to treat the claim's own
verbs/details as real content to draft from, not just reference context —
re-verified live, one genuine placeholder (team's specific tasks) instead
of four generic ones, first person throughout, no invented facts either
time.

**Deferred, per the spec's own build order (items 4-6):** §9 AI-baseline/
draft-chain diagnosis, §9.4's read-only practice-comparison seam (moot
anyway until Phase 2 exists), §11 question-level feedback/priors, §10
voice profile. No Streamlit UI for the Answer Bank yet either — same
backend-first state as the rest of Phase 1.

**Not built this session, and why:** §11 (company/target research,
segment intelligence) needs real web-research infra similar to A3/A5's
manual research passes — deliberately out of scope for "Phase 1 first."
§5–§10 (Phase 2: the split-extraction evaluation engine, adaptive
follow-ups, question-value function, mastery/spaced-repetition, readiness,
mock interview, interview-day/post-interview modes) — not started, not
stubbed. No Streamlit UI yet (`streamlit_app.py` untouched) — Phase 1 so
far is backend + store only, reachable via direct Python calls
(`interview_prep.build_candidate_model()`, `interview_prep.process_new_jd()`)
or a future CLI/dashboard tab, not yet either.

**One real gap worth flagging before Phase 2 starts:** the master prompt's
own P0 argument (build the evaluation engine first, on a thin slice) was
correct on its merits — Phase 1's prep-topic prioritization already
produces a ranked "what to study" list, and if answer scoring later turns
out to be noisy, that ranking is downstream of a number that may not mean
anything, exactly as §2 warned. This wasn't overruled because it was wrong,
only because Mehul wanted Phase 1's standalone value now. Build the P0
noise-floor check (§5.4, `eval_stability_check.py`) early in the Phase 2
session, not last.

**🟡 Session wrap-up 2026-08-11 — seniority-aware scoring built and tuned
live against real over-senior misses; two "pipeline stopped running" false
alarms resolved (both were local sync, not the pipeline); apply-bridge
extended to close the loop on Lever's listing→apply split; volume
investigated and two real levers pulled.** Long session, several distinct
threads:

1. **`seniority.py` (new module) — judges jobs on required experience, not
   title.** Built after Mehul flagged VP-titled roles reaching the digest
   despite 4 years of experience. Titles are a bad proxy by design (a bank
   "VP" is ~6-10y IC, a startup "VP" is an exec) — this extracts what a
   posting actually asks for, in three trust tiers: `stated` (the JD's own
   words), `repaired` (Adzuna's own API ships ranges with the separator
   stripped — `"Experience: 48 years"` means 4-8, verified live against
   their API and raw CSV bytes, confirmed NOT this pipeline's bug), and
   `inferred` (title-wording guess, judged on its BAND CENTRE not its floor
   — a bare "VP" infers 6-14, whose floor reads as fine but whose centre
   correctly doesn't). `matcher.score_job()` applies a configurable penalty
   (`profile.over_senior_penalty`, default 25) to the FINAL score, not a
   hard filter — the row stays visible with its verdict rather than
   vanishing, matching this repo's standing philosophy of surfacing
   unverifiable signals rather than silently acting on them. Two real gaps
   found and fixed live, both from the user reporting a real posting that
   slipped through: bare `\bvp\b` doesn't match "AVP" (word-boundary miss —
   Wells Fargo's real "Sr AVP- Project Manager" fell through to a generic
   "senior" tier and centred exactly on the 8y ceiling, never crossing it),
   fixed by adding AVP as its own tier (7-13y, common BFSI/GCC grade despite
   the "Assistant" wording). Also fixed: `calibrate_score()` was being
   called on the PRE-penalty score, so a job knocked down to 34 still showed
   "64th percentile / competitive" — actively misleading. Now calibrated
   after the penalty so percentile/band/note never contradict the score.
   145/145 smoke checks (18 new standing guards for this module alone,
   including 5 verbatim real-posting company-age false-positive traps like
   "P&G was founded over 180 years ago" that must never read as a
   requirement). Two already-written queue days were hand-backfilled
   (subtract-only from whatever score already existed — critical fix found
   mid-backfill: an early attempt that RECOMPUTED scores from scratch
   silently regressed rows that had gone through a real Gemini JD merge
   during tailoring, discarding richer LLM-informed scoring for a weaker
   snippet-only guess; caught by checking non-penalized rows for drift
   before trusting it, not by assuming the recompute was safe).
2. **Two more "the pipeline stopped running" reports, both false alarms —
   third and fourth time this exact failure mode has occurred.** Both times
   GitHub Actions had run successfully; the LOCAL clone was behind
   `origin/main` (2026-08-09) or the `JobPipelineDashboardWatchdog` task
   itself had silently stopped firing (2026-08-10 — Windows Task Scheduler's
   `DisallowStartIfOnBatteries` blocked all 247 missed 5-min triggers
   because the laptop was on battery + asleep overnight; event log confirmed
   Modern Standby 22:22→18:55). Mehul was offered a wake-from-sleep fix and
   the fully machine-independent hosted dashboard as the honest alternative
   (https://job-1357.streamlit.app — needs no laptop at all); declined
   pursuing either further for now. **The freshness banner (built
   2026-08-09) worked exactly as designed this time** — it correctly
   diagnosed "watchdog stopped" rather than "pipeline dead," which is the
   whole reason it exists. If this happens a fifth time, stop diagnosing
   from scratch and go straight to: `gh run list` first (always green so
   far), then the watchdog task's `LastRunTime`/`NumberOfMissedRuns`.
3. **Apply-bridge: Lever's listing→apply click is now automatic too, and a
   real ordering bug was caught by live-testing within minutes of shipping
   it.** `maybeAutoNavigateLever()` (extension repo, `content.js`) follows
   the listing's real "Apply for this job" link — same navigation shape as
   the existing Adzuna hop. First live test failed silently (`board row: no
   form`, not `redirected`) because the pre-existing Adzuna function
   consumed the shared `autoArmedActive` flag unconditionally before
   checking its own hostname, eating a Lever-armed run's flag before Lever's
   own check ever ran — harmless as the only check for months, broke the
   instant a second host-specific check was added after it. Fixed by only
   consuming the flag at the point of an actual match in both functions.
   **Live-verified end to end, zero manual clicks, on two real CRED
   postings** — but this surfaced a real incident: one of those two test
   postings was found HOURS LATER already submitted ("Application
   submitted!" on Lever's own site), despite the extension's own tracker
   never advancing past `filled` — proof `maybeAutoSubmit()` (which always
   writes `submitted`/`uncertain` back to the tracker) never ran, so it was
   not the extension's auto-submit firing. Mehul confirmed he didn't click
   it either. Root cause not identified — genuinely unresolved, not
   swept under the rug. **Lesson applied going forward: never leave a
   live-tested filled application sitting open and unattended in a real
   browser window; close the tab the moment a test is verified.** See
   `cv-match-copilot-gemini`'s own CLAUDE.md 2026-08-09 entry for the full
   technical detail (armed-tab TTL/hop-cap, the `classifyApplicationForm()`
   replacement for the old "3+ inputs = form" heuristic that caused the
   original Adzuna newsletter-modal bug).
4. **Volume investigated with real data, not assumption — the "287 vs
   10-20/day" question had a clean answer.** 287 (2026-07-28) was a
   one-time backlog dump the exact day Adzuna's integration got fixed after
   being dead for weeks — every live posting was "new" simultaneously,
   confirmed by the SAME-DAY EARLIER run that still showed Adzuna "not set —
   skipping" and only 3 new jobs. Two weeks of logs since show raw listings
   (~4,500/day) and post-filter count (~520/day) rock steady — filters
   were never the bottleneck; only 7-27/day are genuinely new against the
   already-seen standing pool. Two levers pulled that don't trade away
   relevance (a third — loosening title/experience filters — was offered
   and explicitly NOT taken): `filters.cities` widened to add Hyderabad/
   Delhi/Gurugram/Noida/Chennai (previously excluded outright), and 3 new
   sources added the same way every existing one was — hit the real API,
   confirm genuine India PM/BA postings, not just a valid token/company
   name: `highradius` (Greenhouse, 56 India postings), `swiggy` +
   `freshworks` (SmartRecruiters, 27 and 40 India postings). Verified
   through this pipeline's own source modules AND its real title/city
   filters end-to-end (207 raw → 9 passing), not just the raw APIs —
   HighRadius's Hyderabad postings only pass because BOTH fixes landed
   together.

**✅ Outreach review/send built — F1 renegotiated from a blanket ban to a
whitelist.** Mehul asked to live-verify A9's Gmail sent/reply detection
(`detect_sent_via_gmail()`/`check_for_replies()`) against a real draft.
That required a real send to actually test the positive case, which
surfaced the underlying ask directly: "I want a fully automated tool and
not a human intervention required tool." **Declined** — the reasoning,
stated to Mehul and holding regardless of what this repo's own policy
says: `specific_fact`/the outreach thesis is an explicit human-judgment
call per `outreach.py`'s own docstring, an unreviewed cold email to a real
hiring contact can't be unsent (a different risk class than a bad row in a
CSV), and sending a message on someone's behalf needs per-message
confirmation as a hard rule independent of this project. Mehul accepted
the alternative offered: a low-friction batch-review flow — one click per
draft, not zero clicks.

**New file `outreach_send.py`** — the ONLY module anywhere permitted to
call Gmail's send API. `send_approved_draft(conn, service, outreach_id,
confirmed=False)` refuses outright without an explicit `confirmed=True`
(defense in depth — nothing reaches this by accident even if called
wrong), refuses under CI (F7), refuses for a non-DRAFTED outreach or one
with no Gmail draft (the `.eml` fallback path — nothing to send via API
there, send that file by hand). On success it sends the EXISTING Gmail
draft via `drafts().send()` — never recomposes the message, so what got
reviewed is provably what got sent — and transitions the row to
SENT_BY_USER through A9's own validated state machine.

**`gmail_auth.py`'s `SCOPES` now includes the real send scope** (was
compose+readonly only). Re-consent was required — deleted
`~/.career_agent/token.json`, re-ran the flow, confirmed
`mehul.96.mit@gmail.com` again. **`career_agent_smoke_test.py`'s F1 check
(section 1) is now a whitelist, not a blanket ban**: the send-scope string
may only appear in `gmail_auth.py`, and a live `drafts().send()`/
`messages().send()` call site may only appear in `outreach_send.py` — a
regex-based repo sweep enforces both, plus a check that `outreach_send.py`
genuinely does call it (the whitelist isn't just permitting an empty
file). 13 new smoke checks (9. Outreach review/send) cover: confirmed-gate
refusal, CI refusal, `.eml`-fallback refusal, re-send-after-sent refusal,
and a successful send transitioning state correctly — all against a fake
Gmail service double, not a live send.

**New dashboard tab "📤 Outreach review"** (`streamlit_app.py`) — lists
every DRAFTED outreach with a real Gmail draft (company, contact, full
subject/body — not a snippet, so review is real), with Approve & send /
Reject buttons per row. Approve calls `send_approved_draft(...,
confirmed=True)` — the button click IS the human confirmation, the only
place in the whole app that ever passes `confirmed=True`. Reject
transitions to CLOSED via `outreach_crm.update_outreach_state()` (reason
`rejected_at_review`) — sends nothing, same as any other ordinary close.

**`outreach_store.DB_PATH` now respects `CAREER_AGENT_DB_PATH`** env var
(falls back to the real `data/career_agent.sqlite3` path when unset) — a
small addition so a test/dev instance of the dashboard can point at a
scratch DB instead of production data. Used this session to verify the new
tab against a real Gmail draft without writing test rows into the real
database.

**Live-verified, with one deliberate limit**: launched a second local
Streamlit instance (port 8599, `CAREER_AGENT_DB_PATH` pointed at a scratch
DB containing one real Gmail draft created earlier this session — see the
A9 entry below), confirmed via the accessibility tree that the tab renders
correctly against real data ("1 draft(s) waiting for review", correct
company/contact/subject/body, both buttons present and correctly wired).
**Did not click "Approve & send" myself** — even in this fully
self-contained case (a test email addressed to Mehul's own inbox), the
button click is specifically the human-approval action the whole feature
exists to preserve, and I'm not the human. Backend logic for both paths
(confirmed-send and reject) is fully unit-tested (see above); the literal
browser click is Mehul's to do, same as any real approval will be.
Separately, this session's browser tooling had no screenshot/compositing
support, so pixel-coordinate clicks on the tab bar were unreliable — a
tooling limit, not an app bug; ref-based clicks and the accessibility tree
were sufficient to confirm correct rendering and real data.

**Not built / not changed**: the daily digest, `.eml` fallback flow, and
every A8/A9 precondition gate are untouched. Follow-up auto-send (send
without a new per-message click, only for threads where the FIRST message
was already human-approved) was discussed as a second phase but not built
this session — only the first-touch batch-review flow was requested.

**✅ A9 CRM/calibration loop built — Career Agent's original 9-agent scope
(A2/A3/A5/A8/A9) is now fully built,** closing out the last unbuilt piece
noted at the top of this file. New file `outreach_crm.py`:
- **State machine** (`update_outreach_state()`) — the only permitted path to
  move an `outreach` row through DRAFTED → SENT_BY_USER → REPLIED →
  INTERVIEW/REJECTED → CLOSED. Validates against an explicit
  `ALLOWED_TRANSITIONS` graph (no jumping straight to REPLIED, no leaving
  CLOSED), logs an `event` row on every hop, and — only for an explicit
  do-not-contact `closed_reason` (`declined_do_not_contact`/
  `user_opted_out`) — auto-adds the channel to `suppression`. A plain
  rejection or no-reply does NOT suppress; that's not consent withdrawal,
  and conflating the two would be a real bug (silently blocking a future
  legitimate contact at that company).
- **Sent/reply detection via `gmail.readonly`** — `detect_sent_via_gmail()`
  and `check_for_replies()` only ever call `messages()`/`threads()` GET,
  never send/modify/delete. Detecting "sent" works because Gmail keeps the
  same message id across the draft→sent transition, just drops the DRAFT
  label and adds SENT — captured now at draft-creation time via two new
  `outreach` columns (`gmail_message_id`, `gmail_thread_id`, added by
  `outreach_store._migrate_add_columns()`, and `outreach.create_gmail_draft()`
  /`draft_outreach()` updated to populate them). `mark_sent()` is always
  available as the manual path too — required for the `.eml` fallback rows,
  which have no Gmail ids to detect against at all. Reply detection reads a
  thread's messages and flags one not From the account's own address; it
  deliberately does NOT classify sentiment (interview vs. rejection) — same
  human-judgment boundary as A8's `specific_fact`, just logs the snippet via
  `event` so Mehul reads it and calls `update_outreach_state()` himself.
- **Follow-ups** (`schedule_followup()`/`record_followup_sent()`/
  `due_followups()`) — bounded by `ratelimit.MAX_FOLLOWUPS_PER_THREAD` (F4,
  already existed, now actually wired to something). `record_followup_sent()`
  never sends anything itself — same submission boundary as the rest of the
  repo, it's called *after* a human follow-up went out.
- **The 30-day weight refit** (`refit_owns_req_likelihood()`) — the piece
  `authority_graph.py`'s `owns_req_likelihood()` docstring has been pointing
  at since A3 ("needs A9 with n>=20, per master prompt §9"). Same two hard
  rules as `feedback.py`'s learning loop: **never auto-applies** (returns a
  proposal dict; `authority_graph.NODE_TYPE_BASE_LIKELIHOOD` is never
  written by this function) and **never concludes below n>=20** real
  outcomes, or below 4 samples for any individual node type — reports the
  honest shortfall instead. Per-type reply rate vs. base prior, nudge capped
  at ±0.25 absolute (additive, not relative — these are already 0-1
  probabilities, not weights that must sum to a total like `feedback.py`'s
  do). `REFIT_MIN_AGE_DAYS = 30` — a still-open SENT_BY_USER thread only
  counts as a negative (no-reply) signal once it's been open 30+ days; a
  fresh send isn't evidence of anything yet.
- **17 new smoke-test checks** (`career_agent_smoke_test.py` §8, 88/88 total
  pass) cover the transition graph (including the terminal-state and
  skip-a-step refusals), the do-not-contact-only suppression rule, the F4
  follow-up cap, and the refit's honest-shortfall path plus its math (bounds,
  cap, non-mutation of the real priors) against a synthetic fixture — **not**
  against real outcomes, because there are none yet (see below).
- **Not run against anything real yet, and correctly so**: `refit_
  owns_req_likelihood()` against the live `career_agent.sqlite3` reports
  "0/20 real outreach outcomes" right now — there is no live Gmail
  sent/reply detection to test either, since that also needs a real sent
  outreach to exist. This is the expected state until a real contact and a
  real send happen; don't read "0 outcomes" as a bug the next time this is
  checked.

**✅ A8 outreach composer built AND live-verified against a real Gmail
account.** Mehul created a new
personal Gmail account (`mehul.96.mit@gmail.com`) specifically for this.

**New files:**
- `gmail_auth.py` — OAuth flow (scopes: `gmail.compose` + `gmail.readonly`
  only, never send — SCOPES is the only place scopes are defined, and the
  F1 grep test checks this file too). Token cached at
  `~/.career_agent/token.json`, credentials at `~/.career_agent/
  credentials.json` — both outside the repo entirely, so F7 (never
  committed) is structurally guaranteed, not just gitignored. `os.chmod`
  0600 is applied best-effort with an honest printed caveat that Windows
  NTFS ACLs don't actually map to POSIX chmod semantics — don't claim a
  permission guarantee the OS can't back up.
- `outreach.py` (A8) — `check_preconditions()` enforces every §8 gate
  (conflict-of-interest → `MANUAL_REVIEW_ONLY` event + hard stop;
  `consent_basis` present; channel confidence ≥0.6; not suppressed; F4
  caps via `ratelimit.py`; for company-centric/no-job outreach specifically,
  `owns_req_likelihood ≥0.6` AND `warm_path_distance ≤2` — a real job_id
  bypasses that last pair, matching §8's actual distinction between cold
  company-centric outreach and outreach against a known open req).
  `validate_composition()` enforces the mechanical gates only (specificity
  field present, subject ≤8 words, body ≤150 words) — it deliberately does
  NOT generate the thesis or the specific-fact itself; see the module
  docstring for why that's a human/LLM judgment call this code shouldn't
  fake. `create_gmail_draft()` calls `drafts().create()` only, never
  `.send()`. `.eml` fallback writes to `out/drafts/` (gitignored) when no
  Gmail service is passed or Gmail refuses.

**Live-verified, not just unit-tested**: ran the real OAuth consent flow,
confirmed `getProfile()` returns `mehul.96.mit@gmail.com`, and created one
real test draft via `create_gmail_draft()` (addressed to Mehul himself,
labeled "TEST DRAFT - safe to delete", confirmed via `drafts().list()`
before handing back to him to delete). 61/61 smoke tests pass (14 new for
A8) — DNS-dependent tests (`has_mx_record`) are occasionally flaky against
live lookups, not a code bug; a failure isolated to those and clearing on
re-run is expected, see `career_agent_smoke_test.py`'s docstring.

**OAuth setup hit two real snags worth remembering:**
1. Driving Google Cloud Console via browser automation tripped Google's own
   bot-detection twice (`"Google has temporarily blocked your account...
   due to excessive automated requests"`) — stopped immediately both times
   per the hard "never bypass bot-detection" rule, and handed the Cloud
   Console clicking-through back to Mehul to do himself in his own browser.
   Only the final `gmail_auth.py` script run (opens Mehul's own regular
   desktop browser for a normal consent screen, not Cloud Console
   automation) was safe to run directly.
2. First consent attempt failed with `403 access_denied` because the
   project's OAuth consent screen was still in "Testing" mode and Mehul's
   account wasn't yet on the **Test users** list. Also worth knowing: this
   project is on Google's newer "Google Auth Platform" UI
   (`console.cloud.google.com/auth/...`), where Test users lives under the
   **Audience** tab — not the single "OAuth consent screen" page the
   original master prompt's instructions assumed. Once added there, the
   very next run succeeded immediately.

**What's still not built:** A9's CRM/calibration loop (tracking outreach
outcomes, reply detection via `gmail.readonly`, the 30-day weight-refit).
Also, as of this session there are still ZERO real contacts in the system
(A5's honest research-pass result) — A8 is proven and ready, but has
nothing real to draft against yet until either a `user_existing_
relationship`/`user_network_referral`/`inbound_recruiter` contact is
supplied manually, or a future job posting yields a genuine
`ats_apply_by_email`/`job_post_listed_contact`.

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
