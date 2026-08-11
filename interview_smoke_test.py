"""Offline smoke test for the interview-prep subsystem (Phase 1 only —
Phase 2's evaluation engine, practice modes, mastery, and readiness are not
built yet). No API keys, no network — same discipline as smoke_test.py.

Run:  python interview_smoke_test.py
"""
import json
import os
import re
import sqlite3
import sys
import tempfile

PASS, FAIL = "  [PASS]", "  [FAIL]"
failures = 0


def check(name, condition, detail=""):
    global failures
    print(f"{PASS if condition else FAIL} {name} {detail}")
    if not condition:
        failures += 1


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
os.environ["INTERVIEW_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "interview_test.sqlite3")

import interview_store
import interview_prep
import interview_stories
import interview_answers

with open(os.path.join(REPO_ROOT, "resume_master.json"), encoding="utf-8") as f:
    MASTER_RESUME = json.load(f)

import cv_parser
import cv_structure

# Build a structured CV the same way main.py would, without needing the PDF.
_CV_TEXT_PARTS = [MASTER_RESUME.get("summary", "")]
for _c in MASTER_RESUME.get("experience", []):
    for _r in _c.get("roles", []):
        _CV_TEXT_PARTS.extend(_r.get("bullets", []))
for _g in MASTER_RESUME.get("skills", []):
    _CV_TEXT_PARTS.append(_g.get("items", ""))
STRUCTURED_CV = cv_structure.parse_cv_structured("\n".join(_CV_TEXT_PARTS))

interview_store.init_db()

print("== I1 — no Gmail send-scope string or send() call site in any interview_*.py file")
# The forbidden scope string is built at runtime, never written literally in
# this file, so this test doesn't itself trip career_agent_smoke_test.py's
# own repo-wide F1 sweep for it.
SEND_SCOPE_STR = "gmail" + "." + "send"
SEND_CALL_RE = re.compile(r"(drafts\(\)|messages\(\))\.send\(")
violations = []
for fn in os.listdir(REPO_ROOT):
    if fn.startswith("interview_") and fn.endswith(".py") and fn != "interview_smoke_test.py":
        with open(os.path.join(REPO_ROOT, fn), encoding="utf-8") as f:
            text = f.read()
        if SEND_SCOPE_STR in text or SEND_CALL_RE.search(text):
            violations.append(fn)
check("no Gmail send scope / send() call in interview_*.py", not violations, str(violations))

print("\n== I2 — separate database, INTERVIEW_DB_PATH honoured")
check("interview_store.DB_PATH matches INTERVIEW_DB_PATH env var",
      interview_store.DB_PATH == os.environ["INTERVIEW_DB_PATH"])
career_agent_db = os.path.join(REPO_ROOT, "data", "career_agent.sqlite3")
if os.path.exists(career_agent_db):
    conn = sqlite3.connect(career_agent_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    interview_tables = {"resume_claim", "claim_question", "metric_defense",
                        "interview_process", "story"}
    check("no interview tables leaked into career_agent.sqlite3",
          not (tables & interview_tables))
else:
    check("career_agent.sqlite3 not present locally — skip cross-DB leak check", True)

print("\n== §4.3 claim extraction — deterministic, zero LLM")
claims = interview_prep.extract_claims(
    MASTER_RESUME, STRUCTURED_CV["skills"]["declared"])
check("at least one claim extracted per bullet", len(claims) > 10, f"({len(claims)} claims)")

metric_claim = next(c for c in claims if "27%" in c["claim_text"])
check("metric extracted from '...growing user engagement 27%...'",
      metric_claim["metric_value"] == "27" and metric_claim["metric_unit"] == "%")
check("ownership_signal is 'absent' on a subject-less resume bullet",
      metric_claim["ownership_signal"] == "absent")
check("risk_level elevated for metric + absent ownership",
      metric_claim["risk_level"] >= 4, f"(got {metric_claim['risk_level']})")

collective_claim = next(c for c in claims if "liaison" in c["claim_text"].lower())
check("collective ownership_signal detected on a 'liaison' bullet",
      collective_claim["ownership_signal"] == "we")

no_metric_claim = next(c for c in claims if c["metric_value"] is None)
check("risk_level low when no metric present",
      no_metric_claim["risk_level"] <= 3, f"(got {no_metric_claim['risk_level']})")

check("every claim.verifiability is resume_backed (extracted verbatim from resume_master.json)",
      all(c["verifiability"] == "resume_backed" for c in claims))

print("\n== §4.4 question tree — 10 fixed types, no drift across calls")
tree1 = interview_prep.generate_question_tree(metric_claim["claim_text"], "Bajaj Finance")
tree2 = interview_prep.generate_question_tree(metric_claim["claim_text"], "Bajaj Finance")
check("exactly 10 question types generated", len(tree1) == 10, f"({len(tree1)})")
check("identical claim produces an identical tree (deterministic, not re-generated)",
      tree1 == tree2)

print("\n== §4.5 metrics defense — 10-dimension interrogation, metric claims only")
defense = interview_prep.generate_metric_defense(
    metric_claim["claim_text"], metric_claim["metric_value"], metric_claim["metric_unit"])
check("10 defense dimensions generated for a metric claim", len(defense) == 10, f"({len(defense)})")
no_metric_defense = interview_prep.generate_metric_defense(
    no_metric_claim["claim_text"], no_metric_claim["metric_value"], no_metric_claim["metric_unit"])
check("defense set still templates cleanly with a [YOU FILL] placeholder when no metric exists",
      "[YOU FILL: value]" in no_metric_defense[0]["question_text"])

print("\n== §4.1 candidate model + persistence")
build_result = interview_prep.build_candidate_model(MASTER_RESUME, STRUCTURED_CV)
check("candidate profile built with claims persisted",
      build_result["claim_count"] == len(claims) and build_result["profile_id"])
with interview_store.connect() as conn:
    n_claims = conn.execute("SELECT COUNT(*) AS n FROM resume_claim").fetchone()["n"]
    n_questions = conn.execute("SELECT COUNT(*) AS n FROM claim_question").fetchone()["n"]
    n_defense = conn.execute("SELECT COUNT(*) AS n FROM metric_defense").fetchone()["n"]
check("resume_claim rows persisted", n_claims == len(claims), f"({n_claims})")
check("claim_question rows = 10 per claim", n_questions == len(claims) * 10,
      f"({n_questions} vs expected {len(claims) * 10})")
metric_claims_count = sum(1 for c in claims if c["metric_value"])
check("metric_defense rows = 10 per metric-bearing claim only",
      n_defense == metric_claims_count * 10,
      f"({n_defense} vs expected {metric_claims_count * 10})")

# Re-running build must not duplicate claims (idempotency guard).
interview_prep.build_candidate_model(MASTER_RESUME, STRUCTURED_CV)
with interview_store.connect() as conn:
    n_claims_2 = conn.execute("SELECT COUNT(*) AS n FROM resume_claim").fetchone()["n"]
check("re-running build_candidate_model does not duplicate claims", n_claims_2 == n_claims)

print("\n== §4.2 JD intake — one InterviewProcess, sync requirement matching")
JD_TEXT = """
We need a Product Manager with must-have experience in digital lending and
stakeholder management. Preferred: experience with A/B testing. The role
requires 5+ years and an MBA. Must have strong Google Analytics skills.
Nice to have: experience with blockchain.
"""
jd_result = interview_prep.process_new_jd(
    "TestCo", "Senior PM", JD_TEXT, "pasted", MASTER_RESUME)
with interview_store.connect() as conn:
    n_process = conn.execute("SELECT COUNT(*) AS n FROM interview_process").fetchone()["n"]
    n_reqs = conn.execute(
        "SELECT COUNT(*) AS n FROM jd_requirement WHERE process_id = ?",
        (jd_result["process_id"],)).fetchone()["n"]
    n_matches = conn.execute(
        "SELECT COUNT(*) AS n FROM requirement_match WHERE process_id = ?",
        (jd_result["process_id"],)).fetchone()["n"]
    statuses = {r["match_status"] for r in conn.execute(
        "SELECT match_status FROM requirement_match WHERE process_id = ?",
        (jd_result["process_id"],)).fetchall()}
check("exactly one interview_process row created", n_process == 1, f"({n_process})")
check("every requirement got exactly one match row", n_reqs == n_matches, f"({n_reqs} vs {n_matches})")
check("a genuinely unsupported requirement (blockchain) is classified as a gap",
      "gap" in statuses)
check("a genuinely supported requirement (analytics/stakeholder mgmt) is matched or partial",
      "matched" in statuses or "partial" in statuses)

print("\n== §4.6 prep topics — union of gaps/high-risk-claims/uncovered-competencies")
with interview_store.connect() as conn:
    topics = conn.execute(
        "SELECT topic_text, source, priority FROM prep_topic WHERE process_id = ? ORDER BY priority DESC",
        (jd_result["process_id"],)).fetchall()
sources_seen = {t["source"] for t in topics}
check("prep topics generated", len(topics) > 0, f"({len(topics)})")
check("topics sourced from requirement gaps", "requirement_gap" in sources_seen)
check("topics sourced from high-risk claims", "high_risk_claim" in sources_seen)
check("topics sourced from uncovered competencies", "uncovered_competency" in sources_seen)
priorities = [t["priority"] for t in topics]
check("topics are sorted by priority descending", priorities == sorted(priorities, reverse=True))

print("\n== §4.7 story bank — I3 fact integrity + I4 placeholders")
with interview_store.connect() as conn:
    good = interview_stories.create_story(
        conn, "Personal Loan platform redesign", MASTER_RESUME,
        claim_id=None,
        situation="The PL app had a high drop-off rate",
        task="Redesign the application flow",
        action="Partnered with UI/UX to streamline the experience",
        result="Cut user drop-off rates by 40%",
        reflection=None,
    )
    bad = interview_stories.create_story(
        conn, "Fabricated story", MASTER_RESUME,
        situation="We had a 99% drop-off rate",   # 99 is not in resume_master.json
        task="Fix it", action="Did stuff", result="Fixed it",
    )
    check("story with resume-grounded metric (40%) is accepted", good["ok"], str(good))
    check("story with a fabricated metric (99%) is rejected", not bad["ok"], str(bad))
    check("rejected story is not persisted", bad["story_id"] is None)

    row = conn.execute("SELECT * FROM story WHERE id = ?", (good["story_id"],)).fetchone()
    check("I4 placeholder used for unsupplied team_size",
          row["team_size"] == "[YOU FILL: team size]")
    check("I4 placeholder used for unsupplied stakeholders",
          row["stakeholders"] == "[YOU FILL: stakeholders]")

    interview_stories.map_story_to_competency(conn, good["story_id"], "customer_focus")
    gaps_before = interview_stories.coverage_gaps(conn, ["leadership", "customer_focus"])
    check("coverage_gaps reports 'leadership' still uncovered", "leadership" in gaps_before)
    check("coverage_gaps does not report 'customer_focus' (now mapped)",
          "customer_focus" not in gaps_before)

    try:
        interview_stories.map_story_to_competency(conn, good["story_id"], "not_a_real_competency")
        check("mapping to an unknown competency raises", False)
    except ValueError:
        check("mapping to an unknown competency raises", True)

print("\n== §4.7 LLM story drafting (free-tier only) — I3/I6 with a fake call_fn double")
import interview_llm

GOOD_DRAFT = json.dumps({
    "situation": "The PL app had a high drop-off rate in the application flow",
    "task": "Redesign the flow to reduce drop-off",
    "action": "Partnered with UI/UX designers to streamline the experience",
    "result": "Cut user drop-off rates by 40%",
    "reflection": "This showed the value of close design partnership",
})
BAD_DRAFT = json.dumps({
    "situation": "The app had a 99% drop-off rate",   # 99 not in resume_master.json
    "task": "Fix it", "action": "Did stuff", "result": "Fixed it",
    "reflection": "Learned a lot",
})
INCOMPLETE_DRAFT = json.dumps({"situation": "context only"})

check("interview_llm never resolves to the paid anthropic provider",
      "anthropic" not in interview_llm.FREE_PROVIDERS)

calls = {"n": 0}


def fake_call_fn_good(prompt):
    calls["n"] += 1
    return GOOD_DRAFT


calls["n"] = 0
result = interview_llm.generate_story_draft(metric_claim, MASTER_RESUME, call_fn=fake_call_fn_good)
check("a fact-grounded LLM draft is accepted on the first call",
      result["ok"] and not result["regenerated"] and calls["n"] == 1)

call_sequence = {"n": 0}


def fake_call_fn_bad_then_good(prompt):
    call_sequence["n"] += 1
    return BAD_DRAFT if call_sequence["n"] == 1 else GOOD_DRAFT


call_sequence["n"] = 0
result2 = interview_llm.generate_story_draft(
    metric_claim, MASTER_RESUME, call_fn=fake_call_fn_bad_then_good)
check("I3: a fabricated metric on attempt 1 triggers exactly one regeneration, which then succeeds",
      result2["ok"] and result2["regenerated"] and call_sequence["n"] == 2)


def fake_call_fn_always_bad(prompt):
    return BAD_DRAFT


try:
    interview_llm.generate_story_draft(metric_claim, MASTER_RESUME, call_fn=fake_call_fn_always_bad)
    check("I3: a violation surviving regeneration raises (hard failure, never a silent pass)", False)
except interview_llm.InterviewFactIntegrityError:
    check("I3: a violation surviving regeneration raises (hard failure, never a silent pass)", True)


def fake_call_fn_incomplete(prompt):
    return INCOMPLETE_DRAFT


try:
    interview_llm.generate_story_draft(metric_claim, MASTER_RESUME, call_fn=fake_call_fn_incomplete)
    check("an incomplete SITAR response is treated as invalid, not silently partial", False)
except interview_llm.InterviewFactIntegrityError:
    check("an incomplete SITAR response is treated as invalid, not silently partial", True)

with interview_store.connect() as conn:
    claim_with_id = dict(metric_claim, id=None)
    drafted = interview_stories.draft_story_from_claim(
        conn, claim_with_id, MASTER_RESUME, "LLM-drafted engagement story",
        call_fn=fake_call_fn_good)
    check("draft_story_from_claim persists via the same create_story() I3/I4 path",
          drafted["ok"] and drafted["story_id"] is not None)
    row = conn.execute("SELECT * FROM story WHERE id = ?", (drafted["story_id"],)).fetchone()
    check("persisted LLM-drafted story keeps the I4 placeholder for fields it never touched",
          row["team_size"] == "[YOU FILL: team size]")

print("\n== §4.1 differentiators populated from resume_master.json achievements")
with interview_store.connect() as conn:
    row = conn.execute(
        "SELECT differentiators_json FROM candidate_profile ORDER BY id DESC LIMIT 1").fetchone()
differentiators = json.loads(row["differentiators_json"])
check("differentiators populated from achievements (previously always empty)",
      len(differentiators) == len(MASTER_RESUME.get("achievements", [])) and len(differentiators) > 0,
      f"({len(differentiators)})")
check("each differentiator carries an evidence_ref, not a bare assertion",
      all(d.get("evidence_ref") for d in differentiators))

print("\n== §4.6 prep-topic rationale (LLM, free-tier) — I3 with a fake call_fn double")


def fake_rationale_good(prompt):
    return "An interviewer probes this because a weak answer sounds like a keyword match, not real ownership."


def fake_rationale_with_new_number(prompt):
    return "This matters because 73% of interviewers ask a follow-up here."   # 73 not in the topic


def fake_rationale_bad_then_good(prompt):
    fake_rationale_bad_then_good.n = getattr(fake_rationale_bad_then_good, "n", 0) + 1
    return fake_rationale_with_new_number(prompt) if fake_rationale_bad_then_good.n == 1 else fake_rationale_good(prompt)


sample_topic = topics[0]["topic_text"]
r1 = interview_llm.generate_topic_rationale(sample_topic, call_fn=fake_rationale_good)
check("a clean rationale is accepted on the first call", r1["ok"] and not r1["regenerated"])

try:
    interview_llm.generate_topic_rationale(sample_topic, call_fn=fake_rationale_with_new_number)
    check("I3: a rationale inventing a new number always fails eventually", False)
except interview_llm.InterviewFactIntegrityError:
    check("I3: a rationale inventing a new number always fails eventually", True)

r3 = interview_llm.generate_topic_rationale(sample_topic, call_fn=fake_rationale_bad_then_good)
check("I3: a bad rationale on attempt 1 regenerates once and then succeeds",
      r3["ok"] and r3["regenerated"])

with interview_store.connect() as conn:
    updated_ids = interview_prep.enrich_topics_with_rationale(
        conn, jd_result["process_id"], call_fn=fake_rationale_good)
    check("enrich_topics_with_rationale fills in every topic for the process",
          len(updated_ids) == len(topics), f"({len(updated_ids)} vs {len(topics)})")
    still_null = conn.execute(
        "SELECT COUNT(*) AS n FROM prep_topic WHERE process_id = ? AND rationale IS NULL",
        (jd_result["process_id"],)).fetchone()["n"]
    check("no prep_topic row left without a rationale after enrichment", still_null == 0)

    # Calling again must not re-spend calls on rows that already have one.
    updated_again = interview_prep.enrich_topics_with_rationale(
        conn, jd_result["process_id"], call_fn=fake_rationale_good)
    check("re-running enrichment skips already-rationale'd topics (no wasted calls)",
          len(updated_again) == 0)

print("\n== base question bank — folded into prep_topic, not a parallel system")
import interview_question_bank as qb

check("base question bank covers all 10 categories, no exact-count padding",
      len(qb.CATEGORY_LABELS) == 10 and 70 <= len(qb.BASE_QUESTIONS) <= 110,
      f"({len(qb.BASE_QUESTIONS)} questions)")
check("base bank does NOT duplicate claim-tree/metrics-defense categories",
      "current_project" not in qb.CATEGORY_LABELS and "resume_claim_defense" not in qb.CATEGORY_LABELS)

fintech_jd = "We need a PM with lending and fintech experience."
generic_jd = "We need a PM to own our checkout experience."
fintech_matches = [q for q in qb.relevant_questions(fintech_jd, "PM") if q["tag_matched"]]
generic_matches = [q for q in qb.relevant_questions(generic_jd, "PM") if q["tag_matched"]]
check("lending/fintech JD tag-matches fintech-tagged questions", len(fintech_matches) > 0)
check("a generic JD does not spuriously tag-match fintech questions", len(generic_matches) == 0)

with interview_store.connect() as conn:
    base_topics = conn.execute(
        "SELECT source_ref_id, priority FROM prep_topic WHERE process_id=? AND source='base_question_bank'",
        (jd_result["process_id"],)).fetchall()
check("base question topics are capped, not dumped wholesale",
      0 < len(base_topics) <= interview_prep.BASE_QUESTIONS_PER_PROCESS,
      f"({len(base_topics)})")
priorities = [t["priority"] for t in base_topics]
check("base question topics are pre-ranked by score", priorities == sorted(priorities, reverse=True))

def fake_base_question_answer(prompt):
    return json.dumps({
        "answer_text": "I'm a Product Manager with 4+ years in digital lending, "
                       "currently owning the Home Loan digital acquisition stack.",
        "gaps": [],
    })


with interview_store.connect() as conn:
    result_base = interview_answers.generate_answer_for_question(
        conn, jd_result["process_id"], "base_question", base_topics[0]["source_ref_id"],
        "Tell me about yourself.", None, MASTER_RESUME, call_fn=fake_base_question_answer)
check("a base question with claim=None still drafts from candidate summary, "
      "not insufficient_context", result_base["draft_status"] != "insufficient_context")

print("\n== Critique (T§5.5 format, free-tier, quote-verified)")
GOOD_CRITIQUE = json.dumps({
    "observation": 'You said "I led the platform redesign myself" — clear ownership.',
    "why_it_matters": "This signals you can drive a project independently.",
    "how_to_improve": "Add the specific metric that resulted from this.",
})
UNANCHORED_CRITIQUE = json.dumps({
    "observation": "You demonstrated strong leadership throughout.",
    "why_it_matters": "Shows seniority.",
    "how_to_improve": "Be more specific.",
})


def fake_critique_good(prompt):
    return GOOD_CRITIQUE


def fake_critique_unanchored(prompt):
    return UNANCHORED_CRITIQUE


crit1 = interview_llm.critique_answer(
    "Tell me about a leadership moment.", "I led the platform redesign myself.",
    call_fn=fake_critique_good)
check("critique with a real verbatim quote is marked quote_verified",
      crit1["quote_verified"] and "OBSERVATION" not in crit1["observation"])
check("critique returns all three T§5.5 fields", all(crit1[k] for k in
      ("observation", "why_it_matters", "how_to_improve")))

crit2 = interview_llm.critique_answer(
    "Tell me about a leadership moment.", "I led the platform redesign myself.",
    call_fn=fake_critique_unanchored)
check("a critique with no verbatim quote is flagged, not silently trusted",
      not crit2["quote_verified"])

print("\n== Prep plan ranking (standing guards for bugs found live)")
import interview_question_bank as _qb

_plan_pid = interview_prep.process_new_jd(
    "PlanCo", "Digital Product Manager",
    "We need a product manager for digital lending. Requirement gathering, "
    "stakeholder management, and mobile banking experience are important.",
    "pasted", MASTER_RESUME)["process_id"]

with interview_store.connect() as _c:
    _plan = interview_prep.build_prep_plan(_c, _plan_pid, days_to_interview=1)
    _plan14 = interview_prep.build_prep_plan(_c, _plan_pid, days_to_interview=14)

check("a 1-day plan is short enough to actually drill",
      len(_plan) == interview_prep.plan_size_for(1), f"({len(_plan)})")
check("a plan further out is larger than a 1-day plan",
      len(_plan14) > len(_plan), f"({len(_plan14)} vs {len(_plan)})")

# Found live: a pure score sort filled 7 of the top 10 with two repeated
# question texts (the same template exists once per claim).
_texts = [i["question_text"].strip().lower() for i in _plan14]
check("no single question text floods the plan",
      all(_texts.count(t) <= interview_prep._MAX_REPEATS_PER_QUESTION for t in _texts))

# Found live: a 1-day plan came back with ZERO standard questions -- no
# "tell me about yourself", no "why us" -- because claim-defense outscored
# them all. No interview opens with a metric-attribution probe.
check("standard questions can never be scored out of the plan entirely",
      any(i["question_source"] == "base_question" for i in _plan))

# Found live: near-certain questions vanished once they had a draft, because
# the preparedness discount treated an unread generated answer as progress.
_certain = [i for i in _plan14 if i["likelihood"] >= interview_prep._ALWAYS_REHEARSE]
check("near-certain questions appear in the plan at all", bool(_certain))
check("a near-certain question is never discounted for already having a draft",
      all(abs(i["score"] - i["likelihood"] * i["stakes"]) < 1e-6 for i in _certain))

check("every plan row is a real question, not an internal topic string",
      all(i["question_text"] and not i["question_text"].startswith("Prepare for:")
          for i in _plan14))
check("Tell me about yourself outranks a low-frequency intro question",
      _qb.question_likelihood({"id": 1, "category": "intro_career"}) >
      _qb.question_likelihood({"id": 10, "category": "intro_career"}))

# I3-adjacent: a JD requirement the posting never stated would silently move
# the fit score, exactly what tailor.py's own jd_analysis note warns about.
check("an LLM requirement not grounded in the JD text is rejected",
      not interview_llm._grounded_in_jd("kubernetes", "we need product managers"))
check("a genuinely grounded requirement is kept",
      interview_llm._grounded_in_jd("mobile banking", "experience in mobile banking apps"))

print(f"\n{'='*60}")
if failures:
    print(f"{failures} check(s) FAILED")
    sys.exit(1)
print("All interview_smoke_test checks passed.")
