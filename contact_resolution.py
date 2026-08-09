"""A5 — Contact Resolution.

Turns a candidate (authority_node, email, consent_basis) into either a
written contact_channel row or a logged, honest rejection. F2's actual gate
is outreach_store.insert_contact_channel() (consent_basis must be on
policy/contact_allowlist.yaml) — this module is everything that has to be
true BEFORE that call: real RFC-5322-shaped syntax, a domain that actually
receives mail (MX lookup — DNS only, never an SMTP handshake, never a
RCPT TO probe), a domain that matches the company's own (or a documented
subsidiary's), not on the suppression list, and not a duplicate of a
channel already stored.

What this module does NOT do: guess an address from a name pattern
(first.last@company.com), scrape LinkedIn, or fall back to a "best effort"
path when a real consent_basis isn't available. Per the master prompt:
"the absence of a contact is a valid, expected outcome" — most people don't
have a publicly reachable email, and this module returning None for them
is correct, not a failure.
"""
import datetime
import re

import dns.resolver

import outreach_store as store

EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")

# Documented priors, not yet fitted — same status as A3's owns_req_likelihood
# base rates (needs A9 with real reply-rate outcomes to calibrate, per §9).
CONFIDENCE_BASE = {
    "careers_page_published": 0.65,
    "job_post_listed_contact": 0.60,
    "ats_apply_by_email": 0.75,
    "user_existing_relationship": 0.95,
    "user_network_referral": 0.85,
    "inbound_recruiter": 0.90,
    "portal_opt_in_channel": 0.65,
}
DOMAIN_EXACT_MATCH_BONUS = 0.05
DOMAIN_SUBSIDIARY_PENALTY = -0.05
RECENCY_DECAY_DAYS = 180
RECENCY_DECAY_PENALTY = 0.05
CORROBORATION_BONUS_PER_SOURCE = 0.05
CORROBORATION_BONUS_CAP = 0.15


class Rejected(Exception):
    """Raised internally with a machine-readable reason; resolve_contact()
    catches this, logs a NO_CONSENTED_CONTACT event, and returns None."""


def validate_syntax(email):
    return bool(email) and bool(EMAIL_RE.match(email.strip()))


def email_domain(email):
    return email.strip().rsplit("@", 1)[-1].lower()


def has_mx_record(domain):
    """DNS lookup only — this is the F2/F6 boundary: never an SMTP
    handshake, never a RCPT TO probe against the real mail server."""
    try:
        answers = dns.resolver.resolve(domain, "MX")
        return len(answers) > 0
    except Exception:
        return False


def domain_relationship(candidate_domain, company_domain, subsidiary_domains=None):
    """Returns 'exact', 'subsidiary', or None (unrelated — hard reject)."""
    candidate_domain = candidate_domain.lower()
    if company_domain and candidate_domain == company_domain.lower():
        return "exact"
    for sub in (subsidiary_domains or []):
        if candidate_domain == sub.lower():
            return "subsidiary"
    return None


def is_duplicate(conn, email):
    row = conn.execute(
        "SELECT 1 FROM contact_channel WHERE lower(value) = lower(?)", (email.strip(),)
    ).fetchone()
    return row is not None


def compute_confidence(consent_basis, domain_relation, captured_at, corroboration_count=1):
    base = CONFIDENCE_BASE[consent_basis]
    score = base
    if domain_relation == "exact":
        score += DOMAIN_EXACT_MATCH_BONUS
    elif domain_relation == "subsidiary":
        score += DOMAIN_SUBSIDIARY_PENALTY

    corroboration_bonus = min(
        max(0, corroboration_count - 1) * CORROBORATION_BONUS_PER_SOURCE,
        CORROBORATION_BONUS_CAP,
    )
    score += corroboration_bonus

    captured_dt = datetime.datetime.fromisoformat(captured_at)
    days_old = (datetime.datetime.utcnow() - captured_dt).days
    if days_old > RECENCY_DECAY_DAYS:
        score -= RECENCY_DECAY_PENALTY

    return round(max(0.0, min(1.0, score)), 2)


def resolve_contact(conn, authority_node_id, candidate_email, consent_basis, source_url,
                     company_domain, subsidiary_domains=None, corroboration_count=1,
                     captured_at=None, channel_type="email"):
    """The full A5 pipeline. Returns the written row's id on success, or None
    with a NO_CONSENTED_CONTACT-family event logged on rejection — the
    caller should treat None as the expected common case, not an error."""
    captured_at = captured_at or datetime.datetime.utcnow().isoformat()

    def reject(reason):
        store.log_event(conn, "authority_node", authority_node_id, "NO_CONSENTED_CONTACT",
                         f'{{"reason": "{reason}", "candidate": "{candidate_email}"}}', captured_at)
        return None

    if not consent_basis or consent_basis not in CONFIDENCE_BASE:
        return reject("consent_basis missing or not on the allowlist")
    if not candidate_email or not validate_syntax(candidate_email):
        return reject("email fails RFC-5322-shaped syntax check")

    domain = email_domain(candidate_email)
    if store.is_suppressed(conn, candidate_email.strip().lower()):
        return reject("email is on the suppression list")
    if is_duplicate(conn, candidate_email):
        return reject("email already stored for another node (case-insensitive dedupe)")
    if not has_mx_record(domain):
        return reject(f"domain {domain} has no MX record — cannot receive mail")

    if not company_domain:
        return reject("company's own domain is unknown — cannot validate domain match")
    relation = domain_relationship(domain, company_domain, subsidiary_domains)
    if relation is None:
        return reject(f"domain {domain} matches neither the company's domain ({company_domain}) "
                       f"nor a documented subsidiary")

    confidence = compute_confidence(consent_basis, relation, captured_at, corroboration_count)
    node_id = store.insert_contact_channel(
        conn, authority_node_id, channel_type, candidate_email.strip(),
        consent_basis=consent_basis, source_url=source_url,
        captured_at=captured_at, confidence=confidence,
    )
    return node_id


if __name__ == "__main__":
    store.init_db()
    with store.connect() as conn:
        nodes = conn.execute(
            "SELECT authority_node.id, authority_node.person_name, company.name AS company_name "
            "FROM authority_node JOIN company ON company.id = authority_node.company_id"
        ).fetchall()
        print(f"{len(nodes)} authority node(s) exist. Contact resolution has no automated "
              f"discovery step of its own (see module docstring) — call resolve_contact() "
              f"per candidate found through an allowed consent_basis route.")
