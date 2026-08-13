"""SQLite store for the interview-prep subsystem (Phase 1 — preparation).

I2: completely separate database from career_agent.sqlite3. This file is the
ONLY module permitted to write these tables, same discipline as
outreach_store.py. Path overridable via INTERVIEW_DB_PATH, mirroring
CAREER_AGENT_DB_PATH.

Phase 2 (practice/evaluation — §5-§10 of the master prompt) is not built yet.
Tables here cover only §4's preparation engine: candidate model, resume
claims + question trees, metrics defense, JD intake/requirement matching,
prep topics, and the story bank.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("INTERVIEW_DB_PATH") or os.path.join(
    os.path.dirname(__file__), "data", "interview_prep.sqlite3")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS candidate_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_hash TEXT NOT NULL UNIQUE,
    strengths_json TEXT NOT NULL,
    weaknesses_json TEXT NOT NULL,
    differentiators_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One row per resume bullet. claim_text is copied verbatim from
-- resume_master.json, never generated — this IS the I3 ground truth, not
-- something that needs checking against it.
CREATE TABLE IF NOT EXISTS resume_claim (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_text TEXT NOT NULL,
    category TEXT NOT NULL,
    skill_refs_json TEXT NOT NULL,
    project_ref TEXT,
    metric_value TEXT,
    metric_unit TEXT,
    metric_direction TEXT,
    ownership_signal TEXT NOT NULL,       -- I | we | passive | absent
    business_impact_stated INTEGER NOT NULL DEFAULT 0,
    risk_level INTEGER NOT NULL,          -- 1-5, base (JD-independent)
    verifiability TEXT NOT NULL,          -- resume_backed | candidate_asserted | unsupported
    source_company TEXT,
    source_role TEXT,
    source_bullet_index INTEGER,
    created_at TEXT NOT NULL
);

-- The What/Why/How/... tree, generated once per claim from a fixed template
-- set (deterministic — no LLM, no drift, no re-generation cost per session).
CREATE TABLE IF NOT EXISTS claim_question (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES resume_claim(id),
    question_type TEXT NOT NULL,   -- what|why|how|who|your_role|data|impact|tradeoff|failure|change
    question_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- The ten-question metric interrogation set, one row per (claim, dimension).
CREATE TABLE IF NOT EXISTS metric_defense (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES resume_claim(id),
    dimension TEXT NOT NULL,       -- meaning|baseline|timeframe|measurement|intervention|
                                    -- personal_contribution|causality|secondary_effects|
                                    -- tradeoffs|inverse_case
    question_text TEXT NOT NULL,
    answered INTEGER NOT NULL DEFAULT 0,
    answer_text TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(claim_id, dimension)
);

-- §4.2 — one row per JD upload. The trigger event that wires everything else.
CREATE TABLE IF NOT EXISTS interview_process (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    role_title TEXT NOT NULL,
    jd_text TEXT NOT NULL,
    jd_source TEXT NOT NULL,        -- pasted | uploaded
    scheduled_date TEXT,
    stage TEXT,                     -- screen|hiring_manager|panel|case|final
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jd_requirement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL REFERENCES interview_process(id),
    requirement_text TEXT NOT NULL,
    tier TEXT NOT NULL,             -- must_have | preferred | key
    analyst TEXT NOT NULL,          -- deterministic | llm, from jd_analyst.py
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requirement_match (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL REFERENCES interview_process(id),
    requirement_id INTEGER NOT NULL REFERENCES jd_requirement(id),
    match_status TEXT NOT NULL,     -- matched | partial | gap
    layer TEXT NOT NULL,            -- exact|phrase|alias|stem|none, from skill_match.py
    evidence_ref TEXT,
    created_at TEXT NOT NULL
);

-- §4.6 — generated from computed gaps, not a fixed curriculum.
CREATE TABLE IF NOT EXISTS prep_topic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL REFERENCES interview_process(id),
    topic_text TEXT NOT NULL,
    source TEXT NOT NULL,           -- requirement_gap | high_risk_claim | uncovered_competency
    source_ref_id INTEGER,          -- jd_requirement.id or resume_claim.id, per `source`
    priority REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'not_started',  -- not_started|learning|reviewed|prepared|strong
    rationale TEXT,                 -- optional LLM-generated "why this matters", filled in
                                      -- lazily after topic generation, never blocking it
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competency (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- §4.7 — SITAR (Situation/Task/Action/Result/Reflection). Fields the
-- candidate hasn't supplied stay NULL; the renderer emits the I4 placeholder
-- rather than guessing.
CREATE TABLE IF NOT EXISTS story (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    situation TEXT,
    task TEXT,
    action TEXT,
    result TEXT,
    reflection TEXT,
    team_size TEXT,
    exact_role TEXT,
    decision_made TEXT,
    stakeholders TEXT,
    metrics TEXT,
    tradeoff TEXT,
    failure TEXT,
    learning TEXT,
    claim_id INTEGER REFERENCES resume_claim(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS story_competency (
    story_id INTEGER NOT NULL REFERENCES story(id),
    competency_id INTEGER NOT NULL REFERENCES competency(id),
    PRIMARY KEY (story_id, competency_id)
);

-- ============================================================ Answer Bank
-- Companion subsystem (E1-E7). Phase 1 only -- E1 forbids any write path
-- from here to a practice/mock table, and none of those tables exist yet
-- (Phase 2 was never built), so that constraint is enforced by a repo sweep
-- in answer_bank_smoke_test.py rather than by a foreign key that has
-- nothing to reference.

-- E2 (append-only). body_text on an existing row is never UPDATEd by any
-- function in interview_answers.py -- only bookkeeping columns
-- (superseded_by) are, which is the standard git-like "ref moves, commits
-- don't" pattern the spec's own schema comment describes.
CREATE TABLE IF NOT EXISTS prepared_answer_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL REFERENCES interview_process(id),
    question_source TEXT NOT NULL,        -- claim_question | metric_defense
    question_ref_id INTEGER NOT NULL,     -- claim_question.id or metric_defense.id
    question_text TEXT NOT NULL,          -- snapshot at authoring time
    claim_id INTEGER REFERENCES resume_claim(id),
    version_no INTEGER NOT NULL,
    operation TEXT NOT NULL,              -- generate|author|revise|correct_extraction|correct_import
    parent_version_id INTEGER REFERENCES prepared_answer_version(id),
    body_text TEXT NOT NULL,
    draft_status TEXT NOT NULL,           -- complete|complete_with_gaps|insufficient_context
    review_depth TEXT NOT NULL DEFAULT 'unread',  -- unread|skimmed|edited|rewritten
    source TEXT NOT NULL,                 -- generated|typed|pasted|scaffold_accepted
    seed_practice_answer_ref INTEGER,     -- read-only provenance (E1) -- always NULL until a
                                           -- practice engine exists to be read from at all
    evaluation_ref INTEGER,               -- null until T§5's evaluation engine exists (E7 §12)
    superseded_by INTEGER REFERENCES prepared_answer_version(id),
    edit_distance_from_parent INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(process_id, question_source, question_ref_id, version_no)
);

-- E4: provenance is fixed at candidate_asserted for every row this table
-- will ever contain -- there is no code path that writes resume_backed here.
CREATE TABLE IF NOT EXISTS fact_candidate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL REFERENCES interview_process(id),
    value TEXT NOT NULL,
    unit TEXT,
    fact_type TEXT NOT NULL,     -- metric|baseline|timeframe|team_size|stakeholder|tool|decision|outcome|role_detail
    source_version_id INTEGER NOT NULL REFERENCES prepared_answer_version(id),
    source_span TEXT NOT NULL,
    claim_ref INTEGER REFERENCES resume_claim(id),
    status TEXT NOT NULL DEFAULT 'pending',   -- pending|confirmed|rejected|conflicted
    provenance TEXT NOT NULL DEFAULT 'candidate_asserted',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL REFERENCES interview_process(id),
    value TEXT NOT NULL,
    unit TEXT,
    fact_type TEXT NOT NULL,
    claim_ref INTEGER REFERENCES resume_claim(id),
    provenance TEXT NOT NULL DEFAULT 'candidate_asserted',
    fact_candidate_id INTEGER REFERENCES fact_candidate(id),
    confirmed_at TEXT NOT NULL
);

-- E5: created only, never lets a candidate value overwrite resume_master.json.
CREATE TABLE IF NOT EXISTS resume_discrepancy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL REFERENCES interview_process(id),
    claim_id INTEGER NOT NULL REFERENCES resume_claim(id),
    resume_value TEXT NOT NULL,
    candidate_value TEXT NOT NULL,
    resolution TEXT NOT NULL,            -- resume_right|new_value_right|both_different
    fact_candidate_id INTEGER REFERENCES fact_candidate(id),
    created_at TEXT NOT NULL
);

-- E8/§12: disputes are reported, never fed back into scoring (E2's own
-- table, not currently written to since no evaluation engine exists yet
-- to be disputed against -- present so the shape exists ahead of it).
CREATE TABLE IF NOT EXISTS score_dispute (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prepared_answer_version_id INTEGER NOT NULL REFERENCES prepared_answer_version(id),
    dimension TEXT,
    reasoning TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_correction (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prepared_answer_version_id INTEGER NOT NULL REFERENCES prepared_answer_version(id),
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT NOT NULL
);

-- ======================================================== Company research
-- One row per (process, role) -- 'current' and 'target' are scoped to the
-- process they belong to, per §45 isolation: two processes targeting the
-- same real-world company each get their own row, so nothing leaks across
-- projects even when the company name is identical.
CREATE TABLE IF NOT EXISTS company (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL REFERENCES interview_process(id),
    role TEXT NOT NULL,              -- current | target
    company_name TEXT NOT NULL,
    industry TEXT,                   -- banking|nbfc|housing_finance|insurance|saas|ecommerce|... or NULL until classified
    industry_basis TEXT,             -- fact | inference -- §36: is the industry stated or guessed
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(process_id, role)
);

-- Per user decision 2026-08-12: no confirmation gate, no stored source URL --
-- a deliberate departure from I3's candidate-fact discipline, scoped ONLY to
-- company/industry research (never to candidate claims, which stay I3-gated
-- via resume_claim/fact_candidate). fact_type still records fact vs
-- calculated vs estimate vs inference vs hypothesis (§36) so the UI can at
-- least LABEL confidence even though nothing blocks on it.
CREATE TABLE IF NOT EXISTS company_fact (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES company(id),
    category TEXT NOT NULL,          -- corporate|business|strategy|financial|segment|competitor|news|regulatory
    label TEXT NOT NULL,             -- e.g. "CEO", "AUM", "GNPA"
    value TEXT NOT NULL,
    period TEXT,                     -- e.g. "FY2025", "Q1 FY26", or NULL for point-in-time facts
    fact_type TEXT NOT NULL DEFAULT 'fact',  -- fact|calculated|estimate|inference|hypothesis
    created_at TEXT NOT NULL
);

-- §12/13 -- current vs target, one row per compared dimension. comparability
-- is a first-class field, not an afterthought: §12 explicitly forbids a
-- forced comparison when industries genuinely differ.
CREATE TABLE IF NOT EXISTS company_comparison (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL REFERENCES interview_process(id),
    dimension TEXT NOT NULL,
    comparability TEXT NOT NULL,     -- comparable | partially_comparable | not_comparable
    current_value TEXT,
    target_value TEXT,
    interpretation TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- §22 -- current role vs target role. Deterministic: derived entirely from
-- structured_cv (current role) + jd_requirement (target role), no research
-- dependency, so this works even while company research is quota-blocked.
CREATE TABLE IF NOT EXISTS role_comparison (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process_id INTEGER NOT NULL REFERENCES interview_process(id),
    dimension TEXT NOT NULL,         -- responsibilities|scope|analytics|leadership|domain|...
    current_role_text TEXT NOT NULL,
    target_role_text TEXT NOT NULL,
    status TEXT NOT NULL,            -- transferable | gap
    recommendation TEXT NOT NULL,    -- emphasize | reframe | learn | dont_overemphasize
    created_at TEXT NOT NULL
);
"""

DEFAULT_COMPETENCIES = [
    "leadership", "ownership", "conflict", "failure", "ambiguity",
    "prioritization", "influence", "customer_focus",
]


@contextmanager
def connect(db_path=DB_PATH):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path=DB_PATH):
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        for name in DEFAULT_COMPETENCIES:
            conn.execute(
                "INSERT OR IGNORE INTO competency (name) VALUES (?)", (name,))
