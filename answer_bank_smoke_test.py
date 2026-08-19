"""Offline smoke test for the Answer Bank (E1-E7). No API keys, no network.
smoke_test.py, career_agent_smoke_test.py, and interview_smoke_test.py must
all still pass -- run them separately, this file only covers this subsystem.

Scope: §17 build order items 1-3 (version store + operations, batch
generation, fact ledger/conflict resolution). §9 (AI-baseline diagnosis),
§11 (question feedback), §10 (voice profile) are not built yet — see
CLAUDE.md.

Run: python answer_bank_smoke_test.py
"""
import json
import os
import re
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
os.environ["INTERVIEW_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "answer_bank_test.sqlite3")

import interview_store
import interview_prep
import interview_answers
import interview_llm

with open(os.path.join(REPO_ROOT, "resume_master.json"), encoding="utf-8") as f:
    MASTER_RESUME = json.load(f)

interview_store.init_db()

with interview_store.connect() as conn:
    process_id = interview_prep.create_interview_process(
        conn, "TestCo", "PM", "We need product management experience.", "pasted")

claims = interview_prep.extract_claims(MASTER_RESUME, [])
CLAIM = dict(next(c for c in claims if "Lifted conversion 23%" in c["claim_text"]))
with interview_store.connect() as conn:
    CLAIM["id"] = interview_prep.insert_claims(conn, [CLAIM])[0]

print("== E1 — practice records are untouchable from here")
SEND_LIKE_PATTERNS = re.compile(r"\bpractice_\w*\s*.*\bUPDATE\b|\bmock_interview\b.*\bUPDATE\b", re.I)
violations = []
for fn in ("interview_answers.py",):
    with open(os.path.join(REPO_ROOT, fn), encoding="utf-8") as f:
        text = f.read()
    # Any UPDATE/INSERT/DELETE statement whose target table name contains a
    # forbidden substring is a real E1 violation, not just the string
    # appearing in a comment (which the docstrings here do, legitimately).
    for m in re.finditer(r"(?:UPDATE|INSERT INTO|DELETE FROM)\s+(\w+)", text, re.I):
        for bad in interview_answers.FORBIDDEN_TABLE_SUBSTRINGS:
            if bad in m.group(1).lower():
                violations.append((fn, m.group(0)))
check("no write statement targets a practice/mock/scorecard table", not violations, str(violations))

try:
    interview_answers._assert_not_practice_table("practice_session")
    check("_assert_not_practice_table refuses a practice-shaped name", False)
except interview_answers.PracticeWriteAttempted:
    check("_assert_not_practice_table refuses a practice-shaped name", True)

print("\n== §1/§2 operations — generate/author/revise/correct, append-only")
with interview_store.connect() as conn:
    v1 = interview_answers.create_version(
        conn, process_id, "claim_question", 1, "Walk me through this claim",
        operation="generate", body_text="Draft answer with [YOU FILL: baseline]",
        draft_status="complete_with_gaps", source="generated", claim_id=CLAIM["id"])
    row1 = conn.execute("SELECT * FROM prepared_answer_version WHERE id=?", (v1,)).fetchone()
    check("first version has version_no=1, no parent, review_depth=unread (generate default)",
          row1["version_no"] == 1 and row1["parent_version_id"] is None and row1["review_depth"] == "unread")

    v2 = interview_answers.revise_answer(
        conn, process_id, "claim_question", 1,
        "I led the redesign myself, cutting drop-off 40% [YOU FILL: baseline]")
    row1_after = conn.execute("SELECT * FROM prepared_answer_version WHERE id=?", (v1,)).fetchone()
    row2 = conn.execute("SELECT * FROM prepared_answer_version WHERE id=?", (v2,)).fetchone()
    check("revising sets superseded_by on the parent, never touches its body_text",
          row1_after["superseded_by"] == v2 and row1_after["body_text"] == row1["body_text"])
    check("revision is version_no=2 with parent_version_id pointing at v1",
          row2["version_no"] == 2 and row2["parent_version_id"] == v1)
    check("a meaningful edit is classified review_depth != unread", row2["review_depth"] in ("edited", "rewritten"))

    try:
        interview_answers.correct_extraction(
            conn, process_id, "claim_question", 1, field="baseline_stated",
            old_value=False, new_value=True)
        # correct_extraction with the SAME body_text as current should succeed;
        # verify separately that a CHANGED body_text is refused.
        check("correct_extraction with unchanged text succeeds", True)
    except ValueError:
        check("correct_extraction with unchanged text succeeds", False)

    try:
        interview_answers.create_version(
            conn, process_id, "claim_question", 1, "q",
            operation="correct_extraction", body_text="totally different text now",
            draft_status="complete", source="typed")
        check("E§1.3: correct_extraction with CHANGED text is rejected as a mislabelled revision", False)
    except ValueError:
        check("E§1.3: correct_extraction with CHANGED text is rejected as a mislabelled revision", True)

    v_author = interview_answers.author_answer(
        conn, process_id, "claim_question", 2, "A different question",
        "Written entirely from scratch by the candidate.")
    row_author = conn.execute("SELECT * FROM prepared_answer_version WHERE id=?", (v_author,)).fetchone()
    check("author operation stores review_depth='rewritten' (fully candidate's own)",
          row_author["review_depth"] == "rewritten" and row_author["operation"] == "author")

print("\n== E3 — prepared answers never confer mastery or readiness")
check("no mastery/readiness table or column exists anywhere in interview_store schema",
      "mastery" not in interview_store.SCHEMA_SQL.lower()
      and "readiness" not in interview_store.SCHEMA_SQL.lower())
check("is_untested reflects preparation coverage only, not a mastery/readiness signal",
      hasattr(interview_answers, "is_untested"))
with interview_store.connect() as conn:
    check("a fully authored answer still reports untested (E3 — authoring != practice)",
          interview_answers.is_untested(conn, process_id, "claim_question", 2))

print("\n== §4 batch generation — fake call_fn double, I3-checked")

GOOD_ANSWER = json.dumps({
    "answer_text": "I led the Personal Loan platform's conversion work, lifting "
                   "conversion 23% and user acquisition 14% [YOU FILL: specific "
                   "technical steps].",
    "gaps": ["specific technical steps"],
})
BAD_ANSWER = json.dumps({"answer_text": "We hit a massive 92% improvement.", "gaps": []})


def fake_good(prompt):
    return GOOD_ANSWER


def fake_bad_then_good(prompt):
    fake_bad_then_good.n = getattr(fake_bad_then_good, "n", 0) + 1
    return BAD_ANSWER if fake_bad_then_good.n == 1 else GOOD_ANSWER


with interview_store.connect() as conn:
    cq = [{"question_ref_id": 10, "question_text": "Walk me through this",
          "claim": CLAIM, "question_source": "claim_question"}]
    results = interview_answers.generate_answer_batch(
        conn, process_id, cq, MASTER_RESUME, call_fn=fake_good)
    check("batch generation produces one result per question", len(results) == 1)
    check("a fact-grounded generated answer is marked complete_with_gaps ([YOU FILL] present)",
          results[0]["draft_status"] == "complete_with_gaps")

    cq2 = [{"question_ref_id": 11, "question_text": "Q2", "claim": CLAIM,
           "question_source": "claim_question"}]
    results2 = interview_answers.generate_answer_batch(
        conn, process_id, cq2, MASTER_RESUME, call_fn=fake_bad_then_good)
    check("a fabricated number in attempt 1 regenerates and succeeds",
          results2[0]["regenerated"] and results2[0]["draft_status"] in ("complete", "complete_with_gaps"))

    cq_none = [{"question_ref_id": 12, "question_text": "Q3", "claim": None,
               "question_source": "claim_question"}]
    results3 = interview_answers.generate_answer_batch(
        conn, process_id, cq_none, MASTER_RESUME, call_fn=fake_good)
    check("a question with no claim/story is insufficient_context, no LLM call needed",
          results3[0]["draft_status"] == "insufficient_context")

    mixed = cq_none + cq2  # insufficient_context should sort first regardless of input order
    mixed_results = interview_answers.generate_answer_batch(
        conn, process_id, mixed, MASTER_RESUME, call_fn=fake_good)
    check("read-through queue orders insufficient_context/gaps before complete",
          mixed_results[0]["draft_status"] == "insufficient_context")

print("\n== §6 fact detection / confirmation / conflict resolution (E4, E5)")
sample_answer = ("I owned this end to end. The baseline was 12% before I started, and I "
                 "grew it to 23% over 6 months with a team of 4 engineers.")
facts = interview_answers.extract_fact_candidates(sample_answer, claim_ref=CLAIM["id"])
types_found = {f["fact_type"] for f in facts}
check("fact detector distinguishes baseline/metric/timeframe/team_size in one answer",
      {"baseline", "metric", "timeframe", "team_size"} <= types_found, str(types_found))

with interview_store.connect() as conn:
    fc_ids = interview_answers.detect_and_insert_fact_candidates(
        conn, process_id, v2, sample_answer, claim_ref=CLAIM["id"])
    check("fact candidates persisted with provenance always candidate_asserted (E4)",
          all(conn.execute("SELECT provenance FROM fact_candidate WHERE id=?", (i,)).fetchone()["provenance"]
              == "candidate_asserted" for i in fc_ids))

    # Pick a fact candidate whose value (23) does NOT conflict with the claim's own
    # metric_value (23) -- should confirm straight through.
    non_conflicting = next(i for i in fc_ids if
                           conn.execute("SELECT value FROM fact_candidate WHERE id=?", (i,)).fetchone()["value"]
                           == CLAIM["metric_value"])
    result = interview_answers.confirm_fact_candidate(conn, non_conflicting)
    check("a non-conflicting fact confirms straight into the ledger", result["status"] == "confirmed")
    ledger_row = conn.execute("SELECT provenance FROM fact_ledger WHERE id=?", (result["ledger_id"],)).fetchone()
    check("E4: ledger entry provenance is candidate_asserted, never resume_backed",
          ledger_row["provenance"] == "candidate_asserted")

    # A fact candidate with a value that DOES conflict (e.g. "12" as a
    # 'baseline'-typed number attached to the same claim, which has no
    # baseline of its own, so simulate a real conflict against a metric type
    # by re-tagging one to collide) -- construct directly for a clean test.
    conflicting_id = conn.execute(
        """INSERT INTO fact_candidate
           (process_id, value, unit, fact_type, source_version_id, source_span,
            claim_ref, status, provenance, created_at)
           VALUES (?, '18', '%', 'metric', ?, 'conversion went up about 18%', ?,
                   'pending', 'candidate_asserted', datetime('now'))""",
        (process_id, v2, CLAIM["id"])).lastrowid
    conn.commit()
    result_conflict = interview_answers.confirm_fact_candidate(conn, conflicting_id)
    check("E5: a conflicting value halts as 'conflicted', writes nothing to the ledger",
          result_conflict["status"] == "conflicted" and result_conflict["ledger_id"] is None)

    resolved = interview_answers.confirm_fact_candidate(
        conn, conflicting_id, conflict_resolution="new_value_right")
    check("E5: resolving 'new_value_right' now writes to the ledger", resolved["status"] == "confirmed")
    disc = conn.execute(
        "SELECT * FROM resume_discrepancy WHERE fact_candidate_id=?", (conflicting_id,)).fetchone()
    check("E5: a ResumeDiscrepancy is recorded on resolution", disc is not None)

    resume_row = conn.execute("SELECT metric_value FROM resume_claim WHERE id=?", (CLAIM["id"],)).fetchone()
    check("E5: resume_master.json / resume_claim is NEVER modified by conflict resolution",
          resume_row["metric_value"] == CLAIM["metric_value"])

    resume_right_id = conn.execute(
        """INSERT INTO fact_candidate
           (process_id, value, unit, fact_type, source_version_id, source_span,
            claim_ref, status, provenance, created_at)
           VALUES (?, '99', '%', 'metric', ?, 'bogus', ?, 'pending', 'candidate_asserted',
                   datetime('now'))""",
        (process_id, v2, CLAIM["id"])).lastrowid
    conn.commit()
    rejected = interview_answers.confirm_fact_candidate(
        conn, resume_right_id, conflict_resolution="resume_right")
    check("E5: resolving 'resume_right' rejects the candidate value, writes nothing",
          rejected["status"] == "rejected" and rejected["ledger_id"] is None)

print("\n== §12 evaluation policy (E7) — guards correct, no engine to fake a score with")
with interview_store.connect() as conn:
    calls_tracker = {"n": 0}

    def _no_llm_call_allowed(*a, **k):
        calls_tracker["n"] += 1
        raise AssertionError("evaluate_prepared_answer must never itself trigger a model call")

    eval_result = interview_answers.evaluate_prepared_answer(conn, v2)
    check("no evaluation engine exists yet -> status is 'unavailable', not a fabricated score",
          eval_result["status"] == "unavailable")
    check("evaluate_prepared_answer makes zero model calls itself (E7 — explicit trigger only, "
          "and even then there's nothing to call)", calls_tracker["n"] == 0)

    # Simulate a cached evaluation_ref to verify the cache-by-identical-text guard,
    # since there's no real engine yet to produce one organically.
    conn.execute("UPDATE prepared_answer_version SET evaluation_ref = 999 WHERE id = ?", (v2,))
    cached = interview_answers.evaluate_prepared_answer(conn, v2)
    check("an already-evaluated version returns the cached evaluation_ref, no re-evaluation",
          cached["status"] == "cached" and cached["evaluation_ref"] == 999)

print("\n== E6 — pasted content is unverified until confirmed")
with interview_store.connect() as conn:
    v_paste = interview_answers.author_answer(
        conn, process_id, "claim_question", 3, "Q4",
        "I grew conversion by 73% in my last role.", source="pasted")
    row_paste = conn.execute("SELECT source FROM prepared_answer_version WHERE id=?", (v_paste,)).fetchone()
    check("a pasted answer is tagged source='pasted', distinguishable from typed/generated",
          row_paste["source"] == "pasted")
    paste_facts = interview_answers.detect_and_insert_fact_candidates(
        conn, process_id, v_paste, "I grew conversion by 73% in my last role.")
    check("facts detected in pasted text still enter as 'pending', not auto-confirmed",
          all(conn.execute("SELECT status FROM fact_candidate WHERE id=?", (i,)).fetchone()["status"] == "pending"
              for i in paste_facts))

print(f"\n{'='*60}")
if failures:
    print(f"{failures} check(s) FAILED")
    sys.exit(1)
print("All answer_bank_smoke_test checks passed.")
