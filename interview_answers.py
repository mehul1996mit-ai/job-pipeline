"""Answer Bank — companion subsystem to interview_prep.py (E1-E7).

Phase 1 only (E1): nothing here writes to, scores, or reads back from any
practice/mock record. No such tables exist yet (Phase 2 was never built),
so E1 is enforced by answer_bank_smoke_test.py's repo sweep, same style as
career_agent_smoke_test.py's F1 check, rather than by a foreign key with
nothing to reference.

Scope built this session (§17 build order items 1-3): the append-only
PreparedAnswerVersion store and its four operations, batch generation
(§4) reusing interview_llm.py's free-tier plumbing, and fact-candidate
detection/confirmation/conflict-resolution (§6). Deferred: AI-baseline
diagnosis (§9.1-9.2), question feedback/priors (§11), voice profile (§10)
-- see CLAUDE.md for why.

E7 (§12 evaluation policy): the trigger/guard logic (cache-by-body-text,
daily cap, never-on-keystroke) is implemented so it's correct and testable,
but there is no T§5 evaluation engine yet to actually call -- evaluate()
returns a clear "not available" result rather than faking a score.
"""
from __future__ import annotations

import difflib
import json
import re
import time
from datetime import datetime, timezone

from interview_store import connect
from interview_stories import _all_resume_numbers   # reused, not duplicated

VALID_OPERATIONS = {"generate", "author", "revise", "correct_extraction", "correct_import"}
VALID_QUESTION_SOURCES = {"claim_question", "metric_defense", "base_question", "custom_question"}
REWRITE_EDIT_DISTANCE_RATIO = 0.5   # >= this fraction changed => 'rewritten' not 'edited'
BATCH_CALL_PACING_SECONDS = 4       # spacing between real LLM calls in a batch — a claim's
                                     # 10 questions can mean up to 20 calls (I3 regeneration),
                                     # fired with no pacing this is what tripped a live 429
MAX_EVALUATIONS_PER_QUESTION_PER_DAY = 10   # §12 guard


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ======================================================== E1 — practice guard

class PracticeWriteAttempted(RuntimeError):
    """Raised if any code path in this module is ever pointed at a practice/
    mock table. Nothing here does that today (no such tables exist), but
    this exists so a future accidental import can't quietly violate E1."""


FORBIDDEN_TABLE_SUBSTRINGS = ("practice_", "mock_interview", "scorecard")


def _assert_not_practice_table(table_name: str) -> None:
    if any(s in table_name.lower() for s in FORBIDDEN_TABLE_SUBSTRINGS):
        raise PracticeWriteAttempted(
            f"refused: {table_name!r} looks like a practice/mock table (E1)")


# ============================================================ edit distance

def _edit_distance_ratio(a: str, b: str) -> float:
    """0.0 = identical, 1.0 = completely different. Character-level, via
    difflib's opcode lengths -- good enough to distinguish a light edit from
    a rewrite without pulling in a Levenshtein dependency."""
    a, b = a or "", b or ""
    if not a and not b:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b)
    changed = sum(size for tag, i1, i2, j1, j2 in
                 (sm.get_opcodes()) for size in
                 ([max(i2 - i1, j2 - j1)] if tag != "equal" else []))
    return changed / max(len(a), len(b), 1)


# ================================================== §2 version model / E1-E3

def _next_version_no(conn, process_id, question_source, question_ref_id) -> int:
    row = conn.execute(
        """SELECT MAX(version_no) AS n FROM prepared_answer_version
           WHERE process_id=? AND question_source=? AND question_ref_id=?""",
        (process_id, question_source, question_ref_id)).fetchone()
    return (row["n"] or 0) + 1


def _current_version(conn, process_id, question_source, question_ref_id):
    return conn.execute(
        """SELECT * FROM prepared_answer_version
           WHERE process_id=? AND question_source=? AND question_ref_id=?
             AND superseded_by IS NULL
           ORDER BY version_no DESC LIMIT 1""",
        (process_id, question_source, question_ref_id)).fetchone()


def create_version(conn, process_id: int, question_source: str, question_ref_id: int,
                    question_text: str, operation: str, body_text: str,
                    draft_status: str, source: str, claim_id: int | None = None,
                    review_depth: str | None = None) -> int:
    """The single insert path for prepared_answer_version. E2: the prior
    current version (if any) is never modified except for its
    `superseded_by` pointer -- body_text, draft_status, etc. on it are
    untouched forever."""
    if operation not in VALID_OPERATIONS:
        raise ValueError(f"operation must be one of {VALID_OPERATIONS}")
    if question_source not in VALID_QUESTION_SOURCES:
        raise ValueError(f"question_source must be one of {VALID_QUESTION_SOURCES}")

    parent = _current_version(conn, process_id, question_source, question_ref_id)
    parent_id = parent["id"] if parent else None

    if operation in ("correct_extraction",) and parent and body_text != parent["body_text"]:
        # E§1.3 / §16: an extraction correction with changed text is a
        # mislabelled revision, not a correction -- refuse outright rather
        # than silently reclassify it for the caller.
        raise ValueError(
            "correct_extraction must not change body_text — this is a revision, "
            "call create_version(operation='revise', ...) instead")

    edit_distance = None
    if parent:
        edit_distance = round(_edit_distance_ratio(parent["body_text"], body_text) * 100)

    if review_depth is None:
        if operation == "generate":
            review_depth = "unread"
        elif operation == "author":
            review_depth = "rewritten"
        elif operation == "revise":
            ratio = _edit_distance_ratio(parent["body_text"], body_text) if parent else 1.0
            review_depth = "rewritten" if ratio >= REWRITE_EDIT_DISTANCE_RATIO else "edited"
        else:   # correct_extraction / correct_import — inherit, text-level or no-op
            review_depth = parent["review_depth"] if parent else "unread"

    version_no = _next_version_no(conn, process_id, question_source, question_ref_id)
    cur = conn.execute(
        """INSERT INTO prepared_answer_version
           (process_id, question_source, question_ref_id, question_text, claim_id,
            version_no, operation, parent_version_id, body_text, draft_status,
            review_depth, source, seed_practice_answer_ref, evaluation_ref,
            superseded_by, edit_distance_from_parent, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, NULL, NULL, NULL, ?, ?)""",
        (process_id, question_source, question_ref_id, question_text, claim_id,
         version_no, operation, parent_id, body_text, draft_status, review_depth,
         source, edit_distance, _now()))
    new_id = cur.lastrowid
    if parent_id:
        conn.execute(
            "UPDATE prepared_answer_version SET superseded_by = ? WHERE id = ?",
            (new_id, parent_id))
    return new_id


def mark_skimmed(conn, version_id: int) -> None:
    """A future UI calls this after tracking real view duration (§9.5). Only
    moves unread -> skimmed; never downgrades an already-edited/rewritten
    answer, and never touches a superseded (non-current) version."""
    row = conn.execute(
        "SELECT review_depth, superseded_by FROM prepared_answer_version WHERE id = ?",
        (version_id,)).fetchone()
    if row and row["review_depth"] == "unread" and row["superseded_by"] is None:
        conn.execute(
            "UPDATE prepared_answer_version SET review_depth = 'skimmed' WHERE id = ?",
            (version_id,))


def is_untested(conn, process_id: int, question_source: str, question_ref_id: int) -> bool:
    """§3 corollary — true whenever a current prepared answer exists.
    seed_practice_answer_ref / practice-attempt tracking doesn't exist yet
    (no Phase 2), so every question with a current version reads as
    untested for now; this is the correct, honest state until practice
    exists to clear it, not a bug."""
    cur = _current_version(conn, process_id, question_source, question_ref_id)
    return cur is not None


# ============================================================ §4 generation

def classify_draft_status(answer_text: str, had_claim_or_story: bool) -> str:
    if not had_claim_or_story or not (answer_text or "").strip():
        return "insufficient_context"
    if "[YOU FILL:" in answer_text:
        return "complete_with_gaps"
    return "complete"


def generate_answer_for_question(conn, process_id: int, question_source: str,
                                  question_ref_id: int, question_text: str,
                                  claim: dict | None, master_resume: dict,
                                  story_text: str | None = None,
                                  config: dict | None = None, call_fn=None) -> dict:
    """§4: batch-generation unit for a single question. Uses only confirmed
    facts (resume_master.json + this process's fact_ledger) and, if given,
    a story from the bank. Delegates drafting + I3 enforcement to
    interview_llm.generate_answer_draft() (local import — keeps the LLM/API
    surface out of callers that only read/confirm facts).

    Also reads this process's own target company/role/JD text and threads
    them into the draft -- without this, every answer only ever knew the
    QUESTION and the CANDIDATE'S PAST claim, never who they're actually
    interviewing for, which is why answers for different questions/processes
    read near-identically (found live, flagged by Mehul: every answer just
    talked about the current role, nothing tailored to the target JD)."""
    from interview_llm import generate_answer_draft

    process_row = conn.execute(
        "SELECT company_name, role_title, jd_text FROM interview_process WHERE id = ?",
        (process_id,)).fetchone()

    if claim is None and not story_text and question_source in ("base_question", "custom_question"):
        # Base questions (T§4's "PM fundamentals"/"behavioral"/etc — not
        # derived from any single claim) still have real context to draft
        # from: the candidate's overall summary. Synthesizing a claim dict
        # here reuses the exact same generation/I3 path rather than adding a
        # second code path just because there's no single claim behind it.
        # A user-authored custom_question is the same situation -- it also
        # has no claim/story mapped to it by definition, but the summary is
        # still real, usable context rather than a bare placeholder.
        claim = {"claim_text": master_resume.get("summary", ""), "source_company": None}

    if claim is None and not story_text:
        version_id = create_version(
            conn, process_id, question_source, question_ref_id, question_text,
            operation="generate",
            body_text="[YOU FILL: this question has no matching claim or story yet — "
                      "answer it directly, or map a story to it first]",
            draft_status="insufficient_context", source="generated")
        return {"version_id": version_id, "draft_status": "insufficient_context", "regenerated": False}

    ledger_numbers = {row["value"] for row in conn.execute(
        "SELECT value FROM fact_ledger WHERE process_id = ?", (process_id,)).fetchall()}

    draft = generate_answer_draft(
        question_text=question_text,
        claim_text=(claim or {}).get("claim_text", ""),
        company=(claim or {}).get("source_company", ""),
        story_text=story_text or "",
        master_resume=master_resume,
        extra_allowed_numbers=ledger_numbers,
        target_company=(process_row["company_name"] if process_row else None),
        target_role=(process_row["role_title"] if process_row else None),
        jd_text=(process_row["jd_text"] if process_row else None),
        config=config, call_fn=call_fn)

    draft_status = classify_draft_status(draft["answer_text"], had_claim_or_story=True)
    version_id = create_version(
        conn, process_id, question_source, question_ref_id, question_text,
        operation="generate", body_text=draft["answer_text"], draft_status=draft_status,
        source="generated", claim_id=(claim or {}).get("id"))
    return {"version_id": version_id, "draft_status": draft_status,
            "regenerated": draft["regenerated"]}


def generate_answer_batch(conn, process_id: int, claim_questions: list[dict],
                           master_resume: dict, story_lookup: dict | None = None,
                           config: dict | None = None, call_fn=None) -> list[dict]:
    """§4: batch generation across a set of (claim, question) pairs. Each
    entry in claim_questions is expected to have: question_ref_id,
    question_text, claim (dict or None). Returns results ordered
    gaps/insufficient-context first, matching the spec's read-through queue
    ordering (§4, §15) so callers don't have to re-sort."""
    story_lookup = story_lookup or {}
    results = []
    for i, cq in enumerate(claim_questions):
        if i > 0 and call_fn is None:
            # Only pace real API calls -- a fake call_fn in tests has no rate
            # limit to respect, and this would just slow the test suite down.
            time.sleep(BATCH_CALL_PACING_SECONDS)
        claim = cq.get("claim")
        story_text = None
        if claim and claim.get("id") in story_lookup:
            story_text = story_lookup[claim["id"]]
        result = generate_answer_for_question(
            conn, process_id, cq.get("question_source", "claim_question"),
            cq["question_ref_id"], cq["question_text"], claim, master_resume,
            story_text=story_text, config=config, call_fn=call_fn)
        # Commit per-question, not once at the end of the whole batch. A
        # free-tier batch of ~20 calls WILL sometimes fail partway (rate
        # limit, transient error) -- without this, one late failure discards
        # every answer already generated, and the caller sees nothing at all
        # for what was actually a mostly-successful run. Explicit commit here
        # is required regardless of the caller's own connection-lifetime
        # semantics (e.g. Streamlit holding one connection open for a whole
        # page render and only committing at the very end via st.rerun()).
        conn.commit()
        result["question_ref_id"] = cq["question_ref_id"]
        result["question_text"] = cq["question_text"]
        results.append(result)

    order = {"insufficient_context": 0, "complete_with_gaps": 1, "complete": 2}
    results.sort(key=lambda r: order.get(r["draft_status"], 3))
    return results


# ================================================================ operations

def author_answer(conn, process_id: int, question_source: str, question_ref_id: int,
                   question_text: str, body_text: str, claim_id: int | None = None,
                   source: str = "typed") -> int:
    """§1.1 — write from blank, discarding any generated draft's lineage
    (parent_version_id is set automatically by create_version if a current
    version exists, so the chain is preserved even though this is a
    from-scratch rewrite, not a revision of its content)."""
    draft_status = "complete_with_gaps" if "[YOU FILL:" in body_text else "complete"
    return create_version(
        conn, process_id, question_source, question_ref_id, question_text,
        operation="author", body_text=body_text, draft_status=draft_status,
        source=source, claim_id=claim_id)


def revise_answer(conn, process_id: int, question_source: str, question_ref_id: int,
                   new_body_text: str, source: str = "typed") -> int:
    """§1.2 — edit the current version. Requires a current version to exist
    (there is nothing to revise otherwise — use author_answer or generate)."""
    current = _current_version(conn, process_id, question_source, question_ref_id)
    if not current:
        raise ValueError("no current version to revise — use author_answer() or generate first")
    draft_status = "complete_with_gaps" if "[YOU FILL:" in new_body_text else "complete"
    return create_version(
        conn, process_id, question_source, question_ref_id, current["question_text"],
        operation="revise", body_text=new_body_text, draft_status=draft_status,
        source=source, claim_id=current["claim_id"])


def correct_extraction(conn, process_id: int, question_source: str, question_ref_id: int,
                        field: str, old_value, new_value) -> int:
    """§1.3 extraction correction — text is UNCHANGED (create_version enforces
    this, raising if body_text differs from the parent's), records the
    correction as a labelled training signal (§7.5) but triggers NO model
    call — deterministic re-scoring only would happen here once T§5's
    extraction-derived dimensions exist; nothing to re-score yet."""
    current = _current_version(conn, process_id, question_source, question_ref_id)
    if not current:
        raise ValueError("no current version to correct")
    version_id = create_version(
        conn, process_id, question_source, question_ref_id, current["question_text"],
        operation="correct_extraction", body_text=current["body_text"],
        draft_status=current["draft_status"], source=current["source"],
        claim_id=current["claim_id"])
    conn.execute(
        """INSERT INTO extraction_correction
           (prepared_answer_version_id, field, old_value, new_value, created_at)
           VALUES (?,?,?,?,?)""",
        (version_id, field, str(old_value), str(new_value), _now()))
    return version_id


def correct_import(conn, process_id: int, question_source: str, question_ref_id: int,
                    corrected_body_text: str) -> int:
    """§1.3 import correction — fixes mangled text (paste/seed artifacts).
    Text-level only, no re-scoring; unlike correct_extraction the text IS
    allowed to change (that's the whole point), so it goes through
    create_version with its own operation tag rather than reusing revise."""
    current = _current_version(conn, process_id, question_source, question_ref_id)
    if not current:
        raise ValueError("no current version to correct")
    return create_version(
        conn, process_id, question_source, question_ref_id, current["question_text"],
        operation="correct_import", body_text=corrected_body_text,
        draft_status=current["draft_status"], source=current["source"],
        claim_id=current["claim_id"])


# ==================================================== §6 fact detection/ledger

_METRIC_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|percent|x|k|K|m|M|cr|lakh|\+)?(?!\w)", re.I)
_TEAM_SIZE_BEFORE = re.compile(r"team of\s*$", re.I)
_TEAM_SIZE_AFTER = re.compile(r"^[- ]?(?:person|people|engineers?|designers?|analysts?|members?)\b", re.I)
_BASELINE_WORD = re.compile(r"\bbaseline\b|\bstarting (?:point|from)\b", re.I)
_TIMEFRAME_BEFORE = re.compile(r"\b(?:over|within|in)\s*$", re.I)
_TIMEFRAME_AFTER = re.compile(r"^\s*(?:days?|weeks?|months?|years?|quarters?)\b", re.I)


def _clause_before(text: str, pos: int, back: int = 40) -> str:
    """Text back to the nearest clause boundary (.,;) within `back` chars, or
    the full window if none -- prevents an earlier clause's context word
    (e.g. "baseline" in a prior sentence) from bleeding onto a later,
    unrelated number in the same answer."""
    seg = text[max(0, pos - back):pos]
    bounds = list(re.finditer(r"[.,;]", seg))
    return seg[bounds[-1].end():] if bounds else seg


def extract_fact_candidates(body_text: str, claim_ref: int | None = None) -> list[dict]:
    """Lightweight, standalone fact detector for the Answer Bank -- NOT the
    full T§5.1 Step A extraction schema (that belongs to the evaluation
    engine, which doesn't exist yet). This only needs to find candidate
    facts to diff against the ledger, not judge answer quality, so a
    narrower deterministic scan is the honest scope rather than faking the
    fuller schema ahead of the engine it belongs to."""
    text = body_text or ""
    candidates = []
    for m in _METRIC_RE.finditer(text):
        value, unit = m.group(1), (m.group(2) or "").strip()
        span_start = max(0, m.start() - 40)
        span_end = min(len(text), m.end() + 20)
        span = text[span_start:span_end].strip()
        before15 = text[max(0, m.start() - 15):m.start()]
        after15 = text[m.end():m.end() + 15]
        # Immediate adjacency, not a wide symmetric window: "27% over 6
        # months" must classify 6 as the timeframe value and 27 as the
        # metric, not both as timeframe just because "over...months" is
        # nearby. Same reasoning for baseline -- _clause_before stops a
        # PRIOR sentence's "baseline" word from attaching to a later,
        # unrelated number.
        if _TEAM_SIZE_BEFORE.search(before15) or _TEAM_SIZE_AFTER.search(after15):
            fact_type = "team_size"
        elif _BASELINE_WORD.search(_clause_before(text, m.start())):
            fact_type = "baseline"
        elif _TIMEFRAME_BEFORE.search(before15) or _TIMEFRAME_AFTER.search(after15):
            fact_type = "timeframe"
        else:
            fact_type = "metric"
        candidates.append({
            "value": value, "unit": unit, "fact_type": fact_type,
            "source_span": span, "claim_ref": claim_ref,
        })
    return candidates


def detect_and_insert_fact_candidates(conn, process_id: int, version_id: int,
                                       body_text: str, claim_ref: int | None = None) -> list[int]:
    """§6.1 — run on each submitted prepared answer. Inserts one
    FactCandidate per detected value, status='pending', provenance always
    'candidate_asserted' (E4 — hardcoded, not a parameter, so nothing here
    can ever write 'resume_backed')."""
    ids = []
    for c in extract_fact_candidates(body_text, claim_ref):
        cur = conn.execute(
            """INSERT INTO fact_candidate
               (process_id, value, unit, fact_type, source_version_id, source_span,
                claim_ref, status, provenance, created_at)
               VALUES (?,?,?,?,?,?,?, 'pending', 'candidate_asserted', ?)""",
            (process_id, c["value"], c["unit"], c["fact_type"], version_id,
             c["source_span"], c["claim_ref"], _now()))
        ids.append(cur.lastrowid)
    return ids


def _resume_claim_numbers(conn, claim_id: int) -> set[str]:
    row = conn.execute(
        "SELECT metric_value FROM resume_claim WHERE id = ?", (claim_id,)).fetchone()
    return {row["metric_value"]} if row and row["metric_value"] else set()


def confirm_fact_candidate(conn, fact_candidate_id: int,
                            conflict_resolution: str | None = None) -> dict:
    """§6.2/§6.3. Without a claim_ref, or when the value doesn't collide with
    that claim's own resume metric, confirms straight into fact_ledger
    (E4: provenance is always candidate_asserted, hardcoded here).

    When it DOES collide (claim_ref set, claim has its own metric_value, and
    the new value differs), confirmation halts:
      - conflict_resolution is None -> status set to 'conflicted', nothing
        written to fact_ledger, caller must re-call with a resolution (E5).
      - 'resume_right' -> fact_candidate rejected, nothing written.
      - 'new_value_right' | 'both_different' -> written to fact_ledger AND
        a ResumeDiscrepancy is recorded. resume_master.json / resume_claim
        is NEVER modified by any branch of this function."""
    fc = conn.execute(
        "SELECT * FROM fact_candidate WHERE id = ?", (fact_candidate_id,)).fetchone()
    if not fc:
        raise ValueError(f"no fact_candidate with id {fact_candidate_id}")
    if fc["status"] not in ("pending", "conflicted"):
        raise ValueError(f"fact_candidate {fact_candidate_id} is already {fc['status']}")

    conflicting = False
    if fc["claim_ref"] and fc["fact_type"] in ("metric", "baseline"):
        resume_numbers = _resume_claim_numbers(conn, fc["claim_ref"])
        if resume_numbers and fc["value"] not in resume_numbers:
            conflicting = True

    if conflicting and not conflict_resolution:
        conn.execute("UPDATE fact_candidate SET status = 'conflicted' WHERE id = ?",
                     (fact_candidate_id,))
        return {"status": "conflicted", "ledger_id": None}

    if conflicting and conflict_resolution == "resume_right":
        conn.execute("UPDATE fact_candidate SET status = 'rejected' WHERE id = ?",
                     (fact_candidate_id,))
        return {"status": "rejected", "ledger_id": None}

    ledger_id = None
    cur = conn.execute(
        """INSERT INTO fact_ledger
           (process_id, value, unit, fact_type, claim_ref, provenance,
            fact_candidate_id, confirmed_at)
           VALUES (?,?,?,?,?, 'candidate_asserted', ?, ?)""",
        (fc["process_id"], fc["value"], fc["unit"], fc["fact_type"], fc["claim_ref"],
         fact_candidate_id, _now()))
    ledger_id = cur.lastrowid
    conn.execute("UPDATE fact_candidate SET status = 'confirmed' WHERE id = ?",
                 (fact_candidate_id,))

    if conflicting:
        resume_numbers = _resume_claim_numbers(conn, fc["claim_ref"])
        conn.execute(
            """INSERT INTO resume_discrepancy
               (process_id, claim_id, resume_value, candidate_value, resolution,
                fact_candidate_id, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (fc["process_id"], fc["claim_ref"], next(iter(resume_numbers), ""), fc["value"],
             conflict_resolution, fact_candidate_id, _now()))

    return {"status": "confirmed", "ledger_id": ledger_id}


def reject_fact_candidate(conn, fact_candidate_id: int) -> None:
    conn.execute("UPDATE fact_candidate SET status = 'rejected' WHERE id = ?",
                 (fact_candidate_id,))


def process_ledger_numbers(conn, process_id: int) -> set[str]:
    return {row["value"] for row in conn.execute(
        "SELECT value FROM fact_ledger WHERE process_id = ?", (process_id,)).fetchall()}


# ==================================================================== §12 E7

def evaluate_prepared_answer(conn, version_id: int) -> dict:
    """§12 policy, correctly enforced ahead of the engine it will one day
    call. E7: only ever called on an explicit request (no autosave/keystroke
    path exists to this function at all — that's enforced by it simply never
    being wired to one). Guards implemented now: identical body_text to an
    already-evaluated version returns the cached result; a daily cap per
    question. The actual scoring call is NOT implemented (no T§5 engine
    exists yet) -- this returns a clear 'unavailable' status rather than a
    fabricated score."""
    row = conn.execute(
        "SELECT * FROM prepared_answer_version WHERE id = ?", (version_id,)).fetchone()
    if not row:
        raise ValueError(f"no prepared_answer_version with id {version_id}")

    if row["evaluation_ref"] is not None:
        return {"status": "cached", "evaluation_ref": row["evaluation_ref"]}

    same_text = conn.execute(
        """SELECT evaluation_ref FROM prepared_answer_version
           WHERE process_id=? AND question_source=? AND question_ref_id=?
             AND body_text=? AND evaluation_ref IS NOT NULL
           ORDER BY id DESC LIMIT 1""",
        (row["process_id"], row["question_source"], row["question_ref_id"],
         row["body_text"])).fetchone()
    if same_text:
        return {"status": "cached", "evaluation_ref": same_text["evaluation_ref"]}

    today = _now()[:10]
    count_today = conn.execute(
        """SELECT COUNT(*) AS n FROM prepared_answer_version
           WHERE process_id=? AND question_source=? AND question_ref_id=?
             AND evaluation_ref IS NOT NULL AND created_at LIKE ?""",
        (row["process_id"], row["question_source"], row["question_ref_id"], f"{today}%")
    ).fetchone()["n"]
    if count_today >= MAX_EVALUATIONS_PER_QUESTION_PER_DAY:
        return {"status": "capped",
               "detail": f"{MAX_EVALUATIONS_PER_QUESTION_PER_DAY}/day limit reached for this question"}

    return {"status": "unavailable",
           "detail": "Phase 2's evaluation engine (T§5) is not built yet — this answer "
                     "is saved and ready to evaluate once it exists."}
