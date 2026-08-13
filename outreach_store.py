"""SQLite store for Career Agent's company-centric/outreach data (A2/A3/A5/A8/A9).

This is the ONLY module permitted to INSERT into these tables — every other
module must go through the functions here. That is what makes the F2 gate
(contact_channel.consent_basis) actually load-bearing instead of a comment:
the schema's NOT NULL constraint is the last line of defense, but
insert_contact_channel() is the first one, and it validates against
policy/contact_allowlist.yaml before the DB even sees the row.

job_pipeline's existing job/CV/scoring data stays exactly where it is (CSV +
seen_jobs.json, see main.py/matcher.py) — this file only owns the NEW
relational entities that company targeting, the authority graph, contact
resolution and outreach tracking need. Two stores, not a rewrite of one.

DB file: data/career_agent.sqlite3 (WAL mode, gitignored like the rest of data/).
"""
import os
import sqlite3
from contextlib import contextmanager

import yaml

DB_PATH = os.environ.get("CAREER_AGENT_DB_PATH") or os.path.join(
    os.path.dirname(__file__), "data", "career_agent.sqlite3")
POLICY_DIR = os.path.join(os.path.dirname(__file__), "policy")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS company (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT,
    relevance_score REAL,
    relevance_explain_json TEXT,
    hiring_signal_score REAL,
    status TEXT DEFAULT 'ACTIVE',              -- ACTIVE | DORMANT
    source_floor TEXT,                         -- 'user_allowlist' if force-included, else NULL
    is_conflict_of_interest INTEGER NOT NULL DEFAULT 0,
    size_band TEXT,                            -- e.g. '500-2000', '2000-10000' — NULL until known
    headcount_estimate INTEGER,
    domain TEXT,                                -- e.g. 'razorpay.com' — NULL until known; A5 domain-match gate
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS authority_node (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES company(id),
    person_name TEXT NOT NULL,
    title TEXT,
    function TEXT,
    node_type TEXT,                             -- A3's function_owner|hiring_manager|ta_lead_function|generic_ta
    seniority_band TEXT,                        -- an actual seniority band (junior/senior/VP/...), never node_type
    owns_req_likelihood REAL,
    warm_path_distance INTEGER,
    warm_path_via TEXT,
    public_profile_url TEXT,
    source TEXT NOT NULL,                      -- provenance: where this node came from
    confidence REAL,
    is_contactable INTEGER NOT NULL DEFAULT 0,  -- 0 until a contact_channel is attached
    created_at TEXT NOT NULL
);

-- consent_basis NOT NULL is the F2 gate at the schema level. This table has
-- no other insert path than insert_contact_channel() below.
CREATE TABLE IF NOT EXISTS contact_channel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    authority_node_id INTEGER NOT NULL REFERENCES authority_node(id),
    channel_type TEXT NOT NULL,
    value TEXT NOT NULL,
    consent_basis TEXT NOT NULL,
    source_url TEXT,
    captured_at TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES company(id),
    authority_node_id INTEGER,
    job_id TEXT,
    channel_id INTEGER,
    draft_gmail_id TEXT,
    gmail_message_id TEXT,                      -- A9: same id Gmail keeps after send, used to detect sent-via-Gmail
    gmail_thread_id TEXT,                       -- A9: used to detect replies via gmail.readonly
    subject TEXT,
    body TEXT,
    state TEXT NOT NULL DEFAULT 'DRAFTED',      -- DRAFTED|SENT_BY_USER|REPLIED|INTERVIEW|REJECTED|CLOSED
    created_at TEXT NOT NULL,
    user_sent_at TEXT,
    followup_count INTEGER NOT NULL DEFAULT 0,
    next_followup_due TEXT,
    closed_reason TEXT
);

CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    event_type TEXT NOT NULL,
    payload_json TEXT,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppression (
    value TEXT PRIMARY KEY,
    scope TEXT NOT NULL,                        -- 'email' | 'company'
    reason TEXT,
    added_at TEXT NOT NULL
);
"""


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


def _migrate_add_columns(conn, table, columns):
    """SQLite has no ADD COLUMN IF NOT EXISTS — check PRAGMA table_info and
    add only what's missing, so re-running against an already-created DB
    (e.g. from before this column existed) doesn't error."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, coltype in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")


def init_db(db_path=DB_PATH):
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate_add_columns(conn, "company", [
            ("size_band", "TEXT"),
            ("headcount_estimate", "INTEGER"),
            ("domain", "TEXT"),
        ])
        _migrate_add_columns(conn, "outreach", [
            ("gmail_message_id", "TEXT"),
            ("gmail_thread_id", "TEXT"),
        ])
        _migrate_add_columns(conn, "authority_node", [
            ("node_type", "TEXT"),
        ])
        # One-time backfill: node_type was previously (mis)written into
        # seniority_band (see CLAUDE.md 2026-08-12 audit finding). Copy it
        # across for any existing row that has a seniority_band value
        # matching a real node_type enum but no node_type of its own yet —
        # never touches a row that already has node_type set, and never
        # touches seniority_band itself (it may hold a real value already).
        conn.execute(
            """UPDATE authority_node SET node_type = seniority_band
               WHERE node_type IS NULL AND seniority_band IN
                 ('function_owner', 'hiring_manager', 'ta_lead_function', 'generic_ta')""")


def _load_valid_consent_basis():
    path = os.path.join(POLICY_DIR, "contact_allowlist.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return set(data["valid_consent_basis"])


def _load_valid_node_sources():
    path = os.path.join(POLICY_DIR, "authority_node_sources.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return set(data["valid_sources"])


def insert_authority_node(conn, company_id, person_name, source, created_at,
                           title=None, function=None, node_type=None,
                           seniority_band=None,
                           owns_req_likelihood=None, warm_path_distance=None,
                           warm_path_via=None, public_profile_url=None,
                           confidence=None):
    """The only permitted write path for authority_node. Raises ValueError if
    `source` isn't on policy/authority_node_sources.yaml — node discovery is
    restricted to public non-scraped sources + manual entry (master prompt
    §5.2), no social-graph traversal. There is no bypass parameter.

    `node_type` is A3's function_owner/hiring_manager/ta_lead_function/
    generic_ta classification; `seniority_band` is a real seniority band
    (junior/senior/VP/...) if one is ever known. These are separate columns
    — node_type was previously (mis)written into seniority_band, see
    CLAUDE.md's 2026-08-12 audit finding, fixed here."""
    if not source:
        raise ValueError("source is required (see policy/authority_node_sources.yaml)")
    valid = _load_valid_node_sources()
    if source not in valid:
        raise ValueError(
            f"source {source!r} is not on the allowlist ({sorted(valid)}) — "
            f"authority_node discovery is restricted to public/manual sources"
        )
    cur = conn.execute(
        """INSERT INTO authority_node
           (company_id, person_name, title, function, node_type, seniority_band,
            owns_req_likelihood, warm_path_distance, warm_path_via,
            public_profile_url, source, confidence, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (company_id, person_name, title, function, node_type, seniority_band,
         owns_req_likelihood, warm_path_distance, warm_path_via,
         public_profile_url, source, confidence, created_at),
    )
    return cur.lastrowid


def insert_contact_channel(conn, authority_node_id, channel_type, value,
                            consent_basis, source_url, captured_at, confidence,
                            verified=False):
    """The only permitted write path for contact_channel. Raises ValueError
    if consent_basis is missing or not on the allowlist — this is F2, not a
    style preference. There is no bypass parameter."""
    if not consent_basis:
        raise ValueError("consent_basis is required — F2 gate (see policy/contact_allowlist.yaml)")
    valid = _load_valid_consent_basis()
    if consent_basis not in valid:
        raise ValueError(
            f"consent_basis {consent_basis!r} is not on the allowlist "
            f"({sorted(valid)}) — F2 gate refuses this insert"
        )
    cur = conn.execute(
        """INSERT INTO contact_channel
           (authority_node_id, channel_type, value, consent_basis, source_url,
            captured_at, verified, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (authority_node_id, channel_type, value, consent_basis, source_url,
         captured_at, int(verified), confidence),
    )
    if confidence >= 0.6:
        conn.execute(
            "UPDATE authority_node SET is_contactable = 1 WHERE id = ?",
            (authority_node_id,),
        )
    return cur.lastrowid


def log_event(conn, entity_type, entity_id, event_type, payload_json, at):
    conn.execute(
        """INSERT INTO event (entity_type, entity_id, event_type, payload_json, at)
           VALUES (?, ?, ?, ?, ?)""",
        (entity_type, entity_id, event_type, payload_json, at),
    )


def is_suppressed(conn, value):
    row = conn.execute("SELECT 1 FROM suppression WHERE value = ?", (value,)).fetchone()
    return row is not None
