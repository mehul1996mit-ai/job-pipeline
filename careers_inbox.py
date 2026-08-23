"""careers_inbox.py — derives careers@/jobs@/hr@ generic role addresses
from a company's own VERIFIED domain (company_domains.py must have already
filled it — this module never guesses a domain itself).

This is the volume floor of the 2026-08-18 outreach redesign (see CLAUDE.md
for the full reasoning): named contacts from recruiter_mine.py/A3-A5 are the
highest-yield channel but structurally can't reach 30/day given this repo's
real company-discovery rate. A generic role inbox at a verified domain can —
it's the one contact type that scales with company COUNT rather than with
research effort. Reply rate is honestly the lowest tier (well under the
named-contact channels), and this is never presented as anything else.

Every write still goes through contact_resolution.resolve_contact() — same
MX-record check, same suppression/dedupe check as every other channel. The
domain-match check is closer to a formality here (the candidate address IS
literally <role>@<the company's own domain>), but it's still the same call,
not a bypass.
"""
from __future__ import annotations

import datetime

import contact_resolution as a5
import outreach_store as store

# Priority order — careers@ is the most conventional and most likely to be
# a real, monitored inbox; hr@ is last since at a large company it's often
# a generic HR-operations line rather than anything recruiting-adjacent.
ROLE_LOCALS = ("careers", "jobs", "hr")

GENERIC_TA_NODE_NAME = "Careers Team (generic inbox)"


def _get_or_create_generic_node(conn, company_id, now):
    existing = conn.execute(
        "SELECT id FROM authority_node WHERE company_id = ? AND source = 'derived_role_inbox'",
        (company_id,)).fetchone()
    if existing:
        return existing["id"]
    return store.insert_authority_node(
        conn, company_id, GENERIC_TA_NODE_NAME, source="derived_role_inbox",
        created_at=now, node_type="generic_ta", confidence=0.5,
    )


def derive_for_company(conn, company_id, domain, now=None):
    """Tries careers@/jobs@/hr@<domain> in priority order, stops at the
    first one that clears resolve_contact()'s gates (MX + not suppressed +
    not a dup). Returns the written contact_channel id, or None if every
    candidate fails (a real, expected outcome — plenty of domains have MX
    records for none of these conventional locals)."""
    now = now or datetime.datetime.utcnow().isoformat()
    node_id = _get_or_create_generic_node(conn, company_id, now)
    for local in ROLE_LOCALS:
        candidate = f"{local}@{domain}"
        channel_id = a5.resolve_contact(
            conn, node_id, candidate, consent_basis="careers_page_published",
            source_url=None, company_domain=domain, captured_at=now,
        )
        if channel_id is not None:
            return channel_id
    return None


def backfill_careers_inboxes(conn, log=print, now=None):
    """Runs derive_for_company() for every company that has a verified
    domain but no derived_role_inbox contact yet. Returns
    {"filled": n, "no_valid_local": n}."""
    now = now or datetime.datetime.utcnow().isoformat()
    rows = conn.execute(
        """SELECT c.id, c.name, c.domain FROM company c
           WHERE c.domain IS NOT NULL
             AND NOT EXISTS (
               SELECT 1 FROM authority_node an
               WHERE an.company_id = c.id AND an.source = 'derived_role_inbox'
             )"""
    ).fetchall()

    filled = no_valid_local = 0
    for row in rows:
        channel_id = derive_for_company(conn, row["id"], row["domain"], now)
        if channel_id is not None:
            filled += 1
        else:
            no_valid_local += 1
            log(f"careers_inbox: no valid careers@/jobs@/hr@ found for "
                f"{row['name']!r} ({row['domain']})")

    log(f"careers_inbox: backfill complete — {filled} filled, {no_valid_local} with no valid local")
    return {"filled": filled, "no_valid_local": no_valid_local}
