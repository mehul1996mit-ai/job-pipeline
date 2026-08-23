"""outreach_shortlist.py — ties a day's job_queue_YYYY-MM-DD.csv to Career
Agent contacts and generates drafts, ranked by contact yield tier first and
posting freshness second.

Yield tiers (see the 2026-08-18 volume-vs-yield analysis in CLAUDE.md):
  1. inbound_recruiter  — someone who already emailed Mehul (recruiter_mine.py)
  2. derived_role_inbox — careers@/jobs@/hr@ at a verified domain (careers_inbox.py)
  3. (none)             — no usable contact yet; shown but never drafted

Freshness matters because reply rates on a fresh posting are meaningfully
higher than a two-week-old one — this reads data/seen_jobs.json's
first_seen date (already tracked by the daily pipeline, previously unused
for outreach prioritization).

generate_drafts_for_day() is the volume engine: it walks the ranked
shortlist and calls outreach.draft_outreach() for each eligible row until
either the day's shortlist is exhausted or ratelimit's F4 caps refuse
(daily cap, per-company cooldown) — both already enforced inside
check_preconditions(), this module doesn't duplicate that logic, just
stops cleanly on the first refusal reason that isn't itself a signal to
skip-and-continue. Every draft still lands as DRAFTED, reviewed via the
existing Approve & send flow — nothing here sends anything.
"""
from __future__ import annotations

import csv
import datetime
import json
import os

import outreach as a8
import outreach_store as store

QUEUE_DIR = os.path.join(os.path.dirname(__file__), "data")
SEEN_JOBS_PATH = os.path.join(QUEUE_DIR, "seen_jobs.json")

TIER_RANK = {"inbound_recruiter": 0, "derived_role_inbox": 1, None: 2}
SUBJECT_WORD_CEILING = a8.SUBJECT_WORD_CEILING


def load_queue_for_day(date_str: str) -> list[dict]:
    path = os.path.join(QUEUE_DIR, f"job_queue_{date_str}.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _load_seen_jobs() -> dict:
    if not os.path.exists(SEEN_JOBS_PATH):
        return {}
    try:
        with open(SEEN_JOBS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _first_seen_for_url(seen_jobs: dict, url: str) -> str | None:
    for entry in seen_jobs.values():
        if entry.get("url") == url:
            return entry.get("first_seen")
    return None


def days_since(date_str: str | None, today: datetime.date | None = None) -> int | None:
    if not date_str:
        return None
    today = today or datetime.date.today()
    try:
        d = datetime.date.fromisoformat(date_str)
    except ValueError:
        return None
    return (today - d).days


def _get_or_create_company_by_name(conn, name: str, now: str) -> int:
    name = (name or "").strip()
    if not name:
        return None
    row = conn.execute("SELECT id FROM company WHERE lower(name) = lower(?)", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        """INSERT INTO company (name, category, source_floor, is_conflict_of_interest,
                                 created_at, updated_at)
           VALUES (?, 'from_job_queue', 'job_posting', 0, ?, ?)""",
        (name, now, now),
    )
    return cur.lastrowid


def best_contact_for_company(conn, company_id: int) -> dict | None:
    """Prefers an inbound_recruiter contact (highest yield) over a
    derived_role_inbox one. Only ever returns a contact that itself passed
    resolve_contact()'s gates at write time — this function doesn't
    re-validate, just picks the best already-valid one."""
    row = conn.execute(
        """SELECT cc.id AS channel_id, cc.value, cc.consent_basis,
                  an.id AS node_id, an.person_name, an.source
           FROM contact_channel cc JOIN authority_node an ON an.id = cc.authority_node_id
           WHERE an.company_id = ?
           ORDER BY CASE cc.consent_basis
                      WHEN 'inbound_recruiter' THEN 0
                      WHEN 'user_network_referral' THEN 0
                      WHEN 'user_existing_relationship' THEN 0
                      ELSE 1
                    END, cc.confidence DESC
           LIMIT 1""",
        (company_id,),
    ).fetchone()
    if row is None:
        return None
    tier = "inbound_recruiter" if row["consent_basis"] in (
        "inbound_recruiter", "user_network_referral", "user_existing_relationship"
    ) else "derived_role_inbox"
    return {
        "channel_id": row["channel_id"], "to_email": row["value"],
        "node_id": row["node_id"], "person_name": row["person_name"],
        "consent_basis": row["consent_basis"], "tier": tier,
    }


def best_named_reference_for_company(conn, company_id: int) -> dict | None:
    """The best real, named person known for this company — from A3/A5's
    manual entries or authority_discovery.py's automated regex-extraction
    pass. Deliberately excludes derived_role_inbox nodes (careers_inbox.py's
    generic "Careers Team" placeholder is not a person to name-drop).
    Ranked by node_type priority (function_owner first), matching A3's own
    priority-by-req-ownership ordering. This never becomes a send target —
    see authority_discovery.py's docstring for why the bridge to careers@
    is personalization, not a new contact_channel."""
    row = conn.execute(
        """SELECT person_name, title, node_type FROM authority_node
           WHERE company_id = ? AND source != 'derived_role_inbox' AND person_name IS NOT NULL
           ORDER BY CASE node_type
                      WHEN 'function_owner' THEN 0
                      WHEN 'hiring_manager' THEN 1
                      WHEN 'ta_lead_function' THEN 2
                      ELSE 3
                    END
           LIMIT 1""",
        (company_id,),
    ).fetchone()
    return dict(row) if row else None


def _draft_subject_body(title: str, company: str, freshness_days: int | None,
                         named_ref: dict | None = None):
    words = (title or "role").split()
    subject_title = " ".join(words[: SUBJECT_WORD_CEILING - 1]) or "role"
    subject = f"{subject_title} @ {company}".strip()
    if len(subject.split()) > SUBJECT_WORD_CEILING:
        subject = " ".join(subject.split()[:SUBJECT_WORD_CEILING])

    freshness_note = (
        f"I saw this was posted {freshness_days} day(s) ago"
        if freshness_days is not None else "I came across this posting"
    )
    greeting = f"Attn: {named_ref['person_name']}" if named_ref else "Hi"
    body = (
        f"{greeting},\n\n{freshness_note} for the {title} role at {company}, and wanted to reach "
        f"out directly. I have 4+ years of Product Management experience in digital lending/"
        f"fintech (Bajaj Finance), and this role looks like a strong fit for my background.\n\n"
        f"I'd welcome the chance to share more about my experience if there's still an opening. "
        f"My resume is attached.\n\nThanks for your time,\nMehul"
    )
    specific_fact = (
        f"A live posting for '{title}' at {company} was listed "
        + (f"{freshness_days} day(s) ago" if freshness_days is not None else "recently")
        + " — this is a real, currently-open req, not a cold guess."
    )
    if named_ref:
        specific_fact += (
            f" Addressed to {named_ref['person_name']}"
            + (f" ({named_ref['title']})" if named_ref.get("title") else "")
            + ", identified as a real point of contact for this function at the company."
        )
    return subject, body, specific_fact


def build_shortlist_for_day(conn, date_str: str, now: str | None = None) -> list[dict]:
    """Returns one row per queue posting for the given day, each carrying:
    company_id, the best available contact (or None), freshness in days,
    whether an outreach draft already exists for this exact job_id, and a
    sort tier. Ranked contact-tier first, freshness second, score third."""
    now = now or datetime.datetime.utcnow().isoformat()
    today_ref = datetime.datetime.fromisoformat(now).date()
    seen_jobs = _load_seen_jobs()
    rows = load_queue_for_day(date_str)

    shortlist = []
    for row in rows:
        company_name = (row.get("company") or "").strip()
        title = (row.get("title") or "").strip()
        url = (row.get("url") or "").strip()
        if not company_name or not url:
            continue

        company_id = _get_or_create_company_by_name(conn, company_name, now)
        contact = best_contact_for_company(conn, company_id)
        named_ref = best_named_reference_for_company(conn, company_id)
        freshness_days = days_since(_first_seen_for_url(seen_jobs, url), today=today_ref)

        existing = conn.execute(
            "SELECT id, state FROM outreach WHERE job_id = ?", (url,)).fetchone()

        try:
            score = float(row.get("score") or 0)
        except ValueError:
            score = 0.0

        shortlist.append({
            "company_id": company_id, "company_name": company_name, "title": title,
            "url": url, "score": score, "freshness_days": freshness_days,
            "contact": contact, "named_ref": named_ref,
            "already_outreached": existing is not None,
            "existing_outreach_id": existing["id"] if existing else None,
            "existing_state": existing["state"] if existing else None,
        })

    shortlist.sort(key=lambda r: (
        TIER_RANK.get(r["contact"]["tier"] if r["contact"] else None, 2),
        r["freshness_days"] if r["freshness_days"] is not None else 9999,
        -r["score"],
    ))
    return shortlist


def generate_drafts_for_day(conn, service, date_str: str, max_new: int | None = None, log=print):
    """Walks build_shortlist_for_day() in ranked order and drafts outreach
    for each eligible row (has a contact, not already outreached, passes
    F4/preconditions) until max_new is hit or the shortlist is exhausted.
    Returns {"drafted": n, "skipped_no_contact": n, "skipped_already_sent": n,
    "skipped_precondition": n}."""
    shortlist = build_shortlist_for_day(conn, date_str)
    stats = {"drafted": 0, "skipped_no_contact": 0, "skipped_already_sent": 0,
              "skipped_precondition": 0}

    for row in shortlist:
        if max_new is not None and stats["drafted"] >= max_new:
            break
        if row["already_outreached"]:
            stats["skipped_already_sent"] += 1
            continue
        if row["contact"] is None:
            stats["skipped_no_contact"] += 1
            continue

        subject, body, specific_fact = _draft_subject_body(
            row["title"], row["company_name"], row["freshness_days"], row.get("named_ref"))
        result = a8.draft_outreach(
            conn, row["company_id"], row["contact"]["node_id"], row["contact"]["channel_id"],
            subject, body, specific_fact, gmail_service=service, job_id=row["url"],
        )
        if isinstance(result, tuple):  # (False, reason) — a precondition refused
            stats["skipped_precondition"] += 1
            log(f"outreach_shortlist: skipped {row['company_name']!r} "
                f"({row['title']!r}) — {result[1]}")
            continue

        stats["drafted"] += 1
        log(f"outreach_shortlist: drafted for {row['company_name']!r} "
            f"({row['title']!r}) via {row['contact']['tier']}")

    log(f"outreach_shortlist: {date_str} — {stats}")
    return stats
