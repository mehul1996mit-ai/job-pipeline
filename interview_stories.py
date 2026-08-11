"""Phase 1 — story bank (master prompt §4.7).

I4: any field the candidate hasn't supplied is stored as a literal
"[YOU FILL: ...]" placeholder, never inferred, never a plausible-looking
example value.

I3: fact-integrity checking, reused conceptually from tailor.py's
rewrite_is_safe() (which validates a rewritten bullet keeps the original's
numbers) -- tailor.py's version is shaped for bullet-pair rewrites
specifically and isn't a fit here, so check_fact_integrity() below applies
the same discipline (every metric value in the candidate text must trace
back to resume_master.json) to free-text story fields instead. This is a
new function, not an import, because the input shape genuinely differs; the
principle it enforces is identical.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from interview_store import connect

PLACEHOLDER_FIELDS = [
    "team_size", "exact_role", "decision_made", "stakeholders",
    "metrics", "tradeoff", "failure", "learning",
]

_VALUE_RE = re.compile(r"\d+(?:\.\d+)?")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _placeholder(field: str) -> str:
    label = field.replace("_", " ")
    return f"[YOU FILL: {label}]"


def _all_resume_numbers(master_resume: dict) -> set[str]:
    """Every numeric token anywhere in resume_master.json -- bullets, project
    descriptions, and achievements. A story metric not in this set is
    unsupported and must be rejected, not silently accepted."""
    numbers = set()
    text_blobs = [master_resume.get("summary", "")]
    for company in master_resume.get("experience", []):
        for role in company.get("roles", []):
            text_blobs.extend(role.get("bullets", []))
            for proj in role.get("projects", []):
                text_blobs.append(proj.get("desc", ""))
    text_blobs.extend(master_resume.get("achievements", []))
    for blob in text_blobs:
        numbers.update(_VALUE_RE.findall(blob or ""))
    return numbers


def check_fact_integrity(story_fields: dict, master_resume: dict,
                         extra_allowed_numbers: set[str] | None = None) -> tuple[bool, list[str]]:
    """Reject a story if any numeric value it asserts doesn't trace back to
    resume_master.json (or, when supplied, a set of additionally-allowed
    numbers -- used by the Answer Bank to also permit numbers already
    confirmed into that process's fact_ledger, which are real
    candidate_asserted facts, just not resume-sourced ones). Placeholder
    strings ([YOU FILL: ...]) are exempt -- they assert nothing yet, which
    is the point of I4."""
    allowed = _all_resume_numbers(master_resume) | (extra_allowed_numbers or set())
    violations = []
    for field in ("situation", "task", "action", "result", "reflection", "metrics"):
        val = story_fields.get(field)
        if not val or val.startswith("[YOU FILL:"):
            continue
        for num in _VALUE_RE.findall(val):
            if num not in allowed:
                violations.append(f"{field}: metric '{num}' not found in resume_master.json")
    return (not violations, violations)


def create_story(conn, title: str, master_resume: dict, claim_id: int | None = None,
                  situation=None, task=None, action=None, result=None, reflection=None,
                  team_size=None, exact_role=None, decision_made=None, stakeholders=None,
                  metrics=None, tradeoff=None, failure=None, learning=None) -> dict:
    """Creates a story row. I4 fills any missing field with a literal
    placeholder. I3 rejects (does not insert) if a supplied field asserts an
    unsupported metric -- returns the violation list instead of a row id so
    the caller can surface it, never a silent partial insert."""
    supplied = {
        "team_size": team_size, "exact_role": exact_role,
        "decision_made": decision_made, "stakeholders": stakeholders,
        "metrics": metrics, "tradeoff": tradeoff, "failure": failure,
        "learning": learning,
    }
    for field in PLACEHOLDER_FIELDS:
        if not supplied.get(field):
            supplied[field] = _placeholder(field)

    narrative = {"situation": situation, "task": task, "action": action,
                "result": result, "reflection": reflection, "metrics": supplied["metrics"]}
    ok, violations = check_fact_integrity(narrative, master_resume)
    if not ok:
        return {"ok": False, "violations": violations, "story_id": None}

    cur = conn.execute(
        """INSERT INTO story
           (title, situation, task, action, result, reflection, team_size,
            exact_role, decision_made, stakeholders, metrics, tradeoff,
            failure, learning, claim_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (title, situation, task, action, result, reflection,
         supplied["team_size"], supplied["exact_role"], supplied["decision_made"],
         supplied["stakeholders"], supplied["metrics"], supplied["tradeoff"],
         supplied["failure"], supplied["learning"], claim_id, _now(), _now()))
    return {"ok": True, "violations": [], "story_id": cur.lastrowid}


def update_story(conn, story_id: int, master_resume: dict, **fields) -> dict:
    """Edit an existing story's own fields directly -- this is the candidate
    hand-editing their own words, not a regeneration, so it is trusted the
    same way a hand-typed prepared-answer edit is (E6's authored-content
    trust level), not run back through I3's regenerate-then-hard-fail path.
    That path exists to stop a MODEL from asserting a number it invented;
    it was never meant to stop a candidate from typing their own number.

    Until this existed, a drafted story could never be revised or have its
    [YOU FILL: ...] placeholders filled in -- the only two things §4.7's own
    "first-pass...for the candidate to edit" framing implies you'd do next.

    `fields` may include any of: situation, task, action, result,
    reflection, plus the PLACEHOLDER_FIELDS. Only keys present are updated."""
    row = conn.execute("SELECT * FROM story WHERE id = ?", (story_id,)).fetchone()
    if not row:
        raise ValueError(f"no story with id {story_id}")

    allowed = {"situation", "task", "action", "result", "reflection", *PLACEHOLDER_FIELDS}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return {"ok": True, "violations": []}

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE story SET {set_clause}, updated_at = ? WHERE id = ?",
        (*updates.values(), _now(), story_id))
    return {"ok": True, "violations": []}


def delete_story(conn, story_id: int) -> None:
    conn.execute("DELETE FROM story_competency WHERE story_id = ?", (story_id,))
    conn.execute("DELETE FROM story WHERE id = ?", (story_id,))


def map_story_to_competency(conn, story_id: int, competency_name: str) -> None:
    row = conn.execute(
        "SELECT id FROM competency WHERE name = ?", (competency_name,)).fetchone()
    if not row:
        raise ValueError(f"unknown competency {competency_name!r} — add it to "
                         "interview_store.DEFAULT_COMPETENCIES first")
    conn.execute(
        "INSERT OR IGNORE INTO story_competency (story_id, competency_id) VALUES (?, ?)",
        (story_id, row["id"]))


def draft_story_from_claim(conn, claim: dict, master_resume: dict, title: str,
                            config: dict | None = None, call_fn=None) -> dict:
    """LLM-drafted first pass at a story (§4.7 "guided capture"), from a
    single ResumeClaim. Import is local to avoid interview_stories.py
    depending on the LLM plumbing (and its tailor.py import) for callers
    that only ever use create_story() directly and want zero API surface.

    Delegates fact-integrity enforcement to interview_llm.generate_story_draft
    (I3 — one regeneration attempt, then a hard failure) and then goes
    through the exact same create_story() path a manually-typed story would,
    so nothing generated here bypasses the normal I3/I4 insert-time checks."""
    from interview_llm import generate_story_draft   # local: keeps the LLM/API
                                                       # surface out of every
                                                       # caller that doesn't need it
    draft = generate_story_draft(claim, master_resume, config=config, call_fn=call_fn)
    f = draft["fields"]
    return create_story(
        conn, title, master_resume, claim_id=claim.get("id"),
        situation=f.get("situation"), task=f.get("task"), action=f.get("action"),
        result=f.get("result"), reflection=f.get("reflection"))


def coverage_gaps(conn, required_competencies: list[str] | None = None) -> list[str]:
    """Competencies with zero mapped stories -- the highest-value single
    output of the prep phase per §4.7. Defaults to every known competency;
    pass a JD-implied subset to scope it to one process."""
    q = "SELECT name FROM competency"
    names = [r["name"] for r in conn.execute(q).fetchall()]
    if required_competencies:
        names = [n for n in names if n in required_competencies]
    gaps = []
    for name in names:
        row = conn.execute(
            """SELECT 1 FROM story_competency sc
               JOIN competency c ON c.id = sc.competency_id
               WHERE c.name = ? LIMIT 1""", (name,)).fetchone()
        if not row:
            gaps.append(name)
    return gaps
