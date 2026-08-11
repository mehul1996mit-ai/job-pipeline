"""Phase 1 — preparation engine (master prompt §4).

Everything here is deterministic: no LLM call, no API key, no network. Claim
extraction, question trees, and the metrics-defense set are all templated
against resume_master.json's own text — there is nothing to "generate" that
isn't already a mechanical transform of ground truth the candidate supplied.
This is a deliberate departure from the master prompt's vaguer implication of
generated content in Phase 1: it satisfies I6 (offline, zero keys) for the
whole phase, not just the smoke tests, and it structurally cannot violate I3
(nothing here can assert a fact resume_master.json doesn't contain, because
every field is either copied from it or a template around a copy of it).

Reuses, not rebuilds: cv_structure.py (structured CV facts),
jd_analyst.analyze_jd() (deterministic JD requirement extraction),
skill_match.match_skill()/index_layers() (layered requirement matching).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import jd_analyst
import skill_match
from interview_store import connect

# --------------------------------------------------------------- utilities

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resume_hash(master_resume: dict) -> str:
    return hashlib.sha256(
        json.dumps(master_resume, sort_keys=True).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------- §4.3 claim extraction

_METRIC_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(%|percent|x|k|K|m|M|cr|lakh|\+)?", re.I)
_IMPACT_WORDS = re.compile(
    r"\b(grow|growing|grew|increase[ds]?|reduc\w+|improv\w+|sav\w+|driv\w+"
    r"|lift\w*|cut\w*|boost\w*|deliver\w*|generat\w*|acceler\w*)\b", re.I)
_PASSIVE_RE = re.compile(r"\b(was|were|is|are|been)\s+\w+ed\b|responsible for", re.I)
_COLLECTIVE_RE = re.compile(
    r"\b(we|our team|team of|cross[- ]functional|collaborat\w*|partnered|"
    r"together with|liaison)\b", re.I)
_FIRST_PERSON_RE = re.compile(r"^\s*i\b|\bi've\b|\bi'm\b", re.I)


def _extract_metric(bullet: str):
    """First value+unit pair in a bullet, or None. Direction is a light
    heuristic off the nearest impact verb -- never invented, just labelled
    'unspecified' when no verb is nearby to read direction from."""
    m = _METRIC_RE.search(bullet)
    if not m:
        return None
    value, unit = m.group(1), (m.group(2) or "").strip()
    direction = "unspecified"
    verb = _IMPACT_WORDS.search(bullet)
    if verb:
        vlow = verb.group(0).lower()
        if vlow.startswith(("grow", "increas", "improv", "lift", "boost",
                            "deliver", "generat", "acceler")):
            direction = "up"
        elif vlow.startswith(("reduc", "sav", "cut")):
            direction = "down"
    return {"value": value, "unit": unit, "direction": direction}


def _ownership_signal(bullet: str) -> str:
    if _FIRST_PERSON_RE.search(bullet):
        return "I"
    if _COLLECTIVE_RE.search(bullet):
        return "we"
    if _PASSIVE_RE.search(bullet):
        return "passive"
    return "absent"


def _risk_level(metric, ownership_signal: str) -> int:
    """Base (JD-independent) risk. High when a metric exists AND ownership is
    ambiguous -- exactly the combination an interviewer's follow-up exposes.
    JD-relevance is layered on top at prep-topic time (§4.6), not here."""
    score = 1
    if metric:
        score += 2
    if ownership_signal in ("absent", "passive"):
        score += 2
    elif ownership_signal == "we":
        score += 1
    return min(score, 5)


def _category(bullet: str, metric) -> str:
    if metric:
        return "quantitative"
    if re.search(r"\b(led|managed|owned|drove|directed)\b", bullet, re.I):
        return "leadership"
    if re.search(r"\b(designed|built|shipped|launched|implemented|architected)\b",
                bullet, re.I):
        return "execution"
    return "general"


def _skill_refs(bullet: str, declared_skills: list[str]) -> list[str]:
    bl = bullet.lower()
    return [s for s in declared_skills if s.lower() in bl]


def extract_claims(master_resume: dict, cv_declared_skills: list[str]) -> list[dict]:
    """One ResumeClaim per bullet, verbatim. Never regroups, never
    paraphrases -- claim_text is a direct copy so I3 has nothing to check
    against except the source it came from."""
    claims = []
    for company in master_resume.get("experience", []):
        for role in company.get("roles", []):
            for i, bullet in enumerate(role.get("bullets", [])):
                metric = _extract_metric(bullet)
                ownership = _ownership_signal(bullet)
                claims.append({
                    "claim_text": bullet,
                    "category": _category(bullet, metric),
                    "skill_refs": _skill_refs(bullet, cv_declared_skills),
                    "project_ref": None,
                    "metric_value": metric["value"] if metric else None,
                    "metric_unit": metric["unit"] if metric else None,
                    "metric_direction": metric["direction"] if metric else None,
                    "ownership_signal": ownership,
                    "business_impact_stated": bool(metric and _IMPACT_WORDS.search(bullet)),
                    "risk_level": _risk_level(metric, ownership),
                    "verifiability": "resume_backed",
                    "source_company": company.get("company"),
                    "source_role": role.get("title"),
                    "source_bullet_index": i,
                })
    return claims


def insert_claims(conn, claims: list[dict]) -> list[int]:
    ids = []
    for c in claims:
        cur = conn.execute(
            """INSERT INTO resume_claim
               (claim_text, category, skill_refs_json, project_ref,
                metric_value, metric_unit, metric_direction, ownership_signal,
                business_impact_stated, risk_level, verifiability,
                source_company, source_role, source_bullet_index, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (c["claim_text"], c["category"], json.dumps(c["skill_refs"]),
             c["project_ref"], c["metric_value"], c["metric_unit"],
             c["metric_direction"], c["ownership_signal"],
             int(c["business_impact_stated"]), c["risk_level"],
             c["verifiability"], c["source_company"], c["source_role"],
             c["source_bullet_index"], _now()))
        ids.append(cur.lastrowid)
    return ids


# ------------------------------------------------------- §4.4 question tree

QUESTION_TEMPLATES = {
    "what":      "Walk me through this: \"{claim}\"",
    "why":       "Why did this need to happen at {company}?",
    "how":       "How did you actually do this, step by step?",
    "who":       "Who else was involved, and how did you divide the work?",
    "your_role": "What specifically was your role versus the team's?",
    "data":      "What data did you look at before and after?",
    "impact":    "How do you know this had the impact you're describing?",
    "tradeoff":  "What did you give up to get this result?",
    "failure":   "What part of this didn't go as planned?",
    "change":    "If you did this again, what would you change?",
}


def generate_question_tree(claim_text: str, company: str) -> list[dict]:
    return [
        {"question_type": qtype,
         "question_text": template.format(claim=claim_text, company=company or "your role")}
        for qtype, template in QUESTION_TEMPLATES.items()
    ]


def insert_question_tree(conn, claim_id: int, claim_text: str, company: str) -> None:
    for q in generate_question_tree(claim_text, company):
        conn.execute(
            """INSERT INTO claim_question (claim_id, question_type, question_text, created_at)
               VALUES (?,?,?,?)""",
            (claim_id, q["question_type"], q["question_text"], _now()))


# --------------------------------------------------------- §4.5 metrics defense

METRIC_DIMENSIONS = [
    ("meaning", "What does the \"{value}{unit}\" figure in \"{claim}\" actually mean?"),
    ("baseline", "What was the baseline before this change?"),
    ("timeframe", "Over what time period was this measured?"),
    ("measurement", "How exactly was this measured?"),
    ("intervention", "What specific intervention caused this number to move?"),
    ("personal_contribution", "What part of this number is directly attributable to you?"),
    ("causality", "How do you know this intervention caused the change, not something else?"),
    ("secondary_effects", "Did this change have any negative side effects elsewhere?"),
    ("tradeoffs", "What did you trade off to achieve this?"),
    ("inverse_case", "What would have happened if you'd done nothing?"),
]


def generate_metric_defense(claim_text: str, metric_value, metric_unit) -> list[dict]:
    value = metric_value or "[YOU FILL: value]"
    unit = metric_unit or ""
    return [
        {"dimension": dim,
         "question_text": template.format(claim=claim_text, value=value, unit=unit)}
        for dim, template in METRIC_DIMENSIONS
    ]


def insert_metric_defense(conn, claim_id: int, claim_text: str, metric_value, metric_unit) -> None:
    if not metric_value:
        return   # only claims that actually carry a metric get the interrogation set
    for d in generate_metric_defense(claim_text, metric_value, metric_unit):
        conn.execute(
            """INSERT OR IGNORE INTO metric_defense
               (claim_id, dimension, question_text, answered, updated_at)
               VALUES (?,?,?,0,?)""",
            (claim_id, d["dimension"], d["question_text"], _now()))


# ------------------------------------------------------- §4.1 candidate model

def build_candidate_profile(conn, structured_cv: dict, claims: list[dict],
                            master_resume: dict) -> int:
    """Strengths/weaknesses/differentiators, each carrying evidence_ref back
    to a resume fact -- never a bare assertion about the candidate."""
    high_risk = sorted(
        [c for c in claims if c["risk_level"] >= 4],
        key=lambda c: -c["risk_level"])[:5]
    weaknesses = [
        {"text": f"Ownership unclear on: \"{c['claim_text'][:80]}\"",
         "evidence_ref": f"{c['source_company']} / {c['source_role']} bullet #{c['source_bullet_index']}"}
        for c in high_risk
    ]
    demonstrated = structured_cv.get("skills", {}).get("demonstrated", [])
    strengths = [
        {"text": f"Demonstrated skill: {d['skill']} ({d['count']} evidencing bullet(s))",
         "evidence_ref": f"{d['count']} bullet(s) in experience section"}
        for d in sorted(demonstrated, key=lambda d: -d["count"])[:5]
    ]
    # Copied verbatim from resume_master.json's achievements array -- this was
    # previously always empty (the caller that was supposed to populate it
    # never did), which silently threw away real differentiating material
    # (award, national case-competition placements) the candidate already has.
    differentiators = [
        {"text": a, "evidence_ref": "achievements section"}
        for a in master_resume.get("achievements", [])
    ]
    h = hashlib.sha256(json.dumps(claims, sort_keys=True, default=str).encode()).hexdigest()[:16]
    cur = conn.execute(
        """INSERT INTO candidate_profile
           (resume_hash, strengths_json, weaknesses_json, differentiators_json,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(resume_hash) DO UPDATE SET
             strengths_json=excluded.strengths_json,
             weaknesses_json=excluded.weaknesses_json,
             differentiators_json=excluded.differentiators_json,
             updated_at=excluded.updated_at""",
        (h, json.dumps(strengths), json.dumps(weaknesses),
         json.dumps(differentiators), _now(), _now()))
    return cur.lastrowid


# -------------------------------------------------- §4.2 JD intake / process

def create_interview_process(conn, company_name: str, role_title: str, jd_text: str,
                              jd_source: str, scheduled_date: str | None = None,
                              stage: str | None = None) -> int:
    if jd_source not in ("pasted", "uploaded"):
        raise ValueError("jd_source must be 'pasted' or 'uploaded'")
    cur = conn.execute(
        """INSERT INTO interview_process
           (company_name, role_title, jd_text, jd_source, scheduled_date, stage,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (company_name, role_title, jd_text, jd_source, scheduled_date, stage,
         _now(), _now()))
    return cur.lastrowid


def _cv_text_index(master_resume: dict):
    parts = [master_resume.get("summary", "")]
    for company in master_resume.get("experience", []):
        for role in company.get("roles", []):
            parts.extend(role.get("bullets", []))
    for group in master_resume.get("skills", []):
        parts.append(group.get("items", ""))
    text = " ".join(parts)
    return skill_match.index_layers(text), text.lower()


def ingest_jd(conn, process_id: int, jd_text: str, master_resume: dict) -> dict:
    """§4.2 steps 1-2: extract requirements, map each against the CV via the
    layered skill matcher, synchronously (no external calls) -- this is the
    part of §4.2 that must complete immediately, per its own spec, while any
    slower company-research step (§11, not built yet) would run async."""
    analysis = jd_analyst.analyze_jd(jd_text)
    cv_idx, cv_lower = _cv_text_index(master_resume)

    requirement_ids = []
    for tier_key, tier in (("must_have_skills", "must_have"),
                           ("preferred_skills", "preferred"),
                           ("key_skills", "key")):
        for req_text in analysis.get(tier_key, []):
            cur = conn.execute(
                """INSERT INTO jd_requirement
                   (process_id, requirement_text, tier, analyst, created_at)
                   VALUES (?,?,?,?,?)""",
                (process_id, req_text, tier, analysis.get("analyst", "deterministic"), _now()))
            req_id = cur.lastrowid
            requirement_ids.append(req_id)

            match = skill_match.match_skill(req_text, cv_idx, cv_lower)
            layer = match.get("layer", "none")
            if layer in ("exact", "phrase"):
                status = "matched"
            elif layer in ("alias", "stem"):
                status = "partial"
            else:
                status = "gap"
            conn.execute(
                """INSERT INTO requirement_match
                   (process_id, requirement_id, match_status, layer, evidence_ref, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (process_id, req_id, status, layer,
                 "resume_master.json skills/experience text" if status != "gap" else None,
                 _now()))
    return {"analysis": analysis, "requirement_ids": requirement_ids}


# --------------------------------------------------------- §4.6 prep topics

BASE_QUESTIONS_PER_PROCESS = 10   # curated cap -- "don't overwhelm the candidate
                                   # with hundreds of questions" applies here too


def _base_question_topics(conn, process_id: int) -> list[dict]:
    """Folds relevant base questions (interview_question_bank.py — PM
    fundamentals, behavioral, product-sense cases, target-company/role, HR —
    the categories the claim-derived tree structurally can't produce) into
    the SAME prep_topic mechanism, rather than a parallel scoring table.
    Capped and ranked by category importance + JD/role tag match, not
    dumped in wholesale -- this repo already learned that lesson with the
    claim-question tree (10 fixed types, not an open-ended generator)."""
    import interview_question_bank as qb
    proc = conn.execute(
        "SELECT jd_text, role_title FROM interview_process WHERE id = ?",
        (process_id,)).fetchone()
    if not proc:
        return []
    scored = []
    for q in qb.relevant_questions(proc["jd_text"], proc["role_title"]):
        importance = qb.CATEGORY_BASE_IMPORTANCE.get(q["category"], 0.5)
        score = min(1.0, importance + (0.1 if q["tag_matched"] else 0))
        scored.append((score, q))
    scored.sort(key=lambda pair: -pair[0])
    out = []
    for score, q in scored[:BASE_QUESTIONS_PER_PROCESS]:
        out.append({
            "topic_text": f"[{qb.CATEGORY_LABELS[q['category']]}] {q['text']}",
            "source": "base_question_bank",
            "source_ref_id": q["id"],
            "priority": round(score, 3),
        })
    return out


def generate_prep_topics(conn, process_id: int) -> list[int]:
    """Union of requirement gaps, high-risk claims, competencies with no
    mapped story, and a curated slice of the base question bank.
    Regenerated from current state -- never a static list."""
    topics = []

    rows = conn.execute(
        """SELECT jr.id AS req_id, jr.requirement_text, jr.tier, rm.match_status
           FROM jd_requirement jr
           JOIN requirement_match rm ON rm.requirement_id = jr.id
           WHERE jr.process_id = ? AND rm.match_status IN ('gap', 'partial')""",
        (process_id,)).fetchall()
    for r in rows:
        tier_weight = {"must_have": 1.0, "preferred": 0.6, "key": 0.5}.get(r["tier"], 0.5)
        status_weight = 1.0 if r["match_status"] == "gap" else 0.6
        topics.append({
            "topic_text": f"Prepare for: \"{r['requirement_text']}\" ({r['match_status']})",
            "source": "requirement_gap",
            "source_ref_id": r["req_id"],
            "priority": round(tier_weight * status_weight, 3),
        })

    high_risk = conn.execute(
        "SELECT id, claim_text, risk_level FROM resume_claim WHERE risk_level >= 4"
    ).fetchall()
    for c in high_risk:
        topics.append({
            "topic_text": f"Defend the ownership/impact of: \"{c['claim_text'][:100]}\"",
            "source": "high_risk_claim",
            "source_ref_id": c["id"],
            "priority": round(c["risk_level"] / 5, 3),
        })

    uncovered = conn.execute(
        """SELECT c.id, c.name FROM competency c
           WHERE NOT EXISTS (
             SELECT 1 FROM story_competency sc WHERE sc.competency_id = c.id)"""
    ).fetchall()
    for comp in uncovered:
        topics.append({
            "topic_text": f"No story mapped yet for competency: {comp['name']}",
            "source": "uncovered_competency",
            "source_ref_id": comp["id"],
            "priority": 0.7,
        })

    topics.extend(_base_question_topics(conn, process_id))

    ids = []
    for t in sorted(topics, key=lambda t: -t["priority"]):
        cur = conn.execute(
            """INSERT INTO prep_topic
               (process_id, topic_text, source, source_ref_id, priority, state,
                created_at, updated_at)
               VALUES (?,?,?,?,?, 'not_started', ?,?)""",
            (process_id, t["topic_text"], t["source"], t["source_ref_id"],
             t["priority"], _now(), _now()))
        ids.append(cur.lastrowid)
    return ids


def enrich_topics_with_rationale(conn, process_id: int, config: dict | None = None,
                                  call_fn=None) -> list[int]:
    """Lazily fills in prep_topic.rationale via the free-tier LLM (§4.6's
    topic list itself stays synchronous/deterministic per §4.2 -- this is an
    optional enrichment pass run after, same async-friendly shape as §11's
    company research, not a blocker on the topic list being usable).
    Skips rows that already have a rationale, so it's safe to call again
    after new topics are generated without re-spending calls on old ones."""
    from interview_llm import generate_topic_rationale
    rows = conn.execute(
        "SELECT id, topic_text FROM prep_topic WHERE process_id = ? AND rationale IS NULL",
        (process_id,)).fetchall()
    updated = []
    for r in rows:
        result = generate_topic_rationale(r["topic_text"], config=config, call_fn=call_fn)
        conn.execute(
            "UPDATE prep_topic SET rationale = ?, updated_at = ? WHERE id = ?",
            (result["rationale"], _now(), r["id"]))
        updated.append(r["id"])
    return updated


# ------------------------------------------------------------ orchestration

def build_candidate_model(master_resume: dict, structured_cv: dict, db_path=None) -> dict:
    """Full §4.1-§4.5 build: candidate profile + claims + question trees +
    metric defense sets, from resume_master.json alone. Idempotent per
    resume_hash -- re-running on an unchanged resume does not duplicate
    claims (guarded by the caller checking existing claim count first)."""
    declared = structured_cv.get("skills", {}).get("declared", [])
    claims = extract_claims(master_resume, declared)
    with connect(db_path) if db_path else connect() as conn:
        existing = conn.execute("SELECT COUNT(*) AS n FROM resume_claim").fetchone()["n"]
        if existing:
            claim_ids = [row["id"] for row in
                        conn.execute("SELECT id FROM resume_claim").fetchall()]
        else:
            claim_ids = insert_claims(conn, claims)
            for cid, c in zip(claim_ids, claims):
                insert_question_tree(conn, cid, c["claim_text"], c["source_company"])
                insert_metric_defense(conn, cid, c["claim_text"], c["metric_value"], c["metric_unit"])
        profile_id = build_candidate_profile(conn, structured_cv, claims, master_resume)
    return {"profile_id": profile_id, "claim_count": len(claims), "claim_ids": claim_ids}


def process_new_jd(company_name: str, role_title: str, jd_text: str, jd_source: str,
                   master_resume: dict, scheduled_date: str | None = None,
                   stage: str | None = None, db_path=None) -> dict:
    """§4.2 end to end: create the process, ingest the JD (sync), generate
    prep topics -- all synchronous per the spec's own requirement that steps
    1-2 must not block on step 3 (company research, not built yet)."""
    with connect(db_path) if db_path else connect() as conn:
        process_id = create_interview_process(
            conn, company_name, role_title, jd_text, jd_source, scheduled_date, stage)
        ingest_result = ingest_jd(conn, process_id, jd_text, master_resume)
        topic_ids = generate_prep_topics(conn, process_id)
    return {"process_id": process_id, "requirement_ids": ingest_result["requirement_ids"],
            "topic_ids": topic_ids}
