"""recruiter_mine.py — the highest-yield outreach channel: people who
already emailed Mehul about a role.

WHY THIS EXISTS. Reply rates to a recruiter who contacted you first run far
higher than any cold-outreach channel this repo can build (industry-typical
cold-email reply rates are under 2%; replying to someone who already reached
out to you is closer to 40-80% — see the 2026-08-18 volume-vs-yield analysis
in CLAUDE.md for the full reasoning). `inbound_recruiter` has been on
policy/contact_allowlist.yaml's F2 allowlist since the very first version of
that file — this is the first thing that actually populates it.

Same "it's his own data" legitimacy as sources/job_alert_email.py: this
reads Mehul's own personal inbox READ-ONLY (BODY.PEEK, never marks read,
never deletes, never sends) via IMAP, using the SAME credentials as that
module (JOB_ALERT_EMAIL / JOB_ALERT_APP_PASSWORD — one mailbox, two
different read passes over it). No LinkedIn automation, no scraping.

WHAT IT DOES NOT DO. It does not attempt real email threading — replying
in-thread would need to come from the SAME account the recruiter wrote to,
but every send in this repo goes through the dedicated career-agent Gmail
account (mehul.96.mit@gmail.com) via outreach_send.py, not Mehul's personal
inbox. A future reply is a fresh, separate email that explicitly references
the recruiter's own message (captured here as an event payload) rather than
a threaded reply. This is a real limitation, not hidden: a genuinely
in-thread reply from Mehul's own personal account, sent by Mehul himself,
would likely out-perform anything this pipeline drafts — see CLAUDE.md.

DETECTION IS DELIBERATELY CONSERVATIVE. False positives here write a
contact_channel with consent_basis=inbound_recruiter into a real database —
getting this wrong misrepresents consent, not just clutters a list. A
message only qualifies if it has BOTH a real personal "From" name (not a
portal/system sender) AND at least one strong recruiter-intent phrase in
subject or body. Newsletters, job-ALERT digests (job_alert_email.py's own
territory — those list many jobs, this wants ONE direct message about
Mehul specifically), and automated ATS rejections are excluded by design.
"""
from __future__ import annotations

import datetime
import email
import html
import imaplib
import json
import os
import re
from email.header import decode_header, make_header
from email.utils import parseaddr

import outreach_store as store

# Sender domains that are never a real recruiter contact themselves — either
# a portal relay (the recruiter's real address isn't in the From header) or
# a generic consumer provider that tells us nothing about their employer.
PORTAL_RELAY_DOMAINS = {
    "naukri.com", "linkedin.com", "indeed.com", "shine.com", "foundit.in",
    "monsterindia.com", "timesjobs.com", "iimjobs.com", "hirist.com",
    "hirist.tech", "instahyre.com", "cutshort.io", "wellfound.com",
    "glassdoor.com", "notifications.linkedin.com",
}
GENERIC_CONSUMER_DOMAINS = {
    "gmail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com",
    "rediffmail.com", "icloud.com", "protonmail.com",
}

# Automated senders that structurally can't be a 1:1 recruiter message,
# regardless of body content — job-alert digests, ATS no-reply addresses.
AUTOMATED_SENDER_RE = re.compile(
    r"no-?reply|do-?not-?reply|notifications?@|alerts?@|digest@|jobs-noreply|"
    r"mailer-?daemon", re.I)

# Requires a real recruiter to be writing directly and specifically about a
# role — not a generic marketing/promo phrase.
RECRUITER_INTENT_RE = re.compile(
    r"\b(opportunit(y|ies)|open(ing)?s? for|hiring for|role (of|at)|"
    r"position (of|at)|interested in your profile|come across your profile|"
    r"connect regarding|reaching out (about|regarding)|"
    r"(job|role) description attached|would (love|like) to (connect|discuss)|"
    r"exploring (this|the) opportunity)\b", re.I)

_TAG_RE = re.compile(r"<[^>]+>")


def _decode(raw) -> str:
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return str(raw or "")


def _body_text(msg, max_chars=4000) -> str:
    chunks = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        if "attachment" in str(part.get("Content-Disposition") or ""):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/html":
            text = html.unescape(_TAG_RE.sub(" ", text))
        chunks.append(text)
    full = re.sub(r"[ \t]+", " ", "\n".join(chunks)).strip()
    return full[:max_chars]


def _domain_of(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def _looks_like_person_name(display_name: str) -> bool:
    """Rejects portal/system display names ('Naukri.com', 'LinkedIn Jobs',
    'Team Notifications') without needing a hardcoded list of every one —
    a real person's name has no digits, no 'team'/'notification'/'noreply'-
    shaped words, and is 2-4 space-separated words."""
    if not display_name:
        return False
    name = display_name.strip()
    if re.search(
        r"\d|team|notif|alert|noreply|no-reply|jobs?\b.*\.com|@|"
        r"solutions?\b|talent\b|recruit(ing|ment)?\b|staffing\b|consulting\b|"
        r"\bhr\b|careers?\b|hiring\b|placements?\b|services\b",
        name, re.I,
    ):
        return False
    words = name.split()
    return 2 <= len(words) <= 4 and all(w[:1].isalpha() for w in words)


def _company_guess(sender_domain: str) -> str | None:
    """A corporate domain (not a portal relay, not generic consumer mail)
    is a reasonable company-name guess — strip the TLD and title-case the
    registrable part. Deliberately rough; this is a starting point for a
    human to correct, not a verified fact."""
    if not sender_domain or sender_domain in PORTAL_RELAY_DOMAINS or \
       sender_domain in GENERIC_CONSUMER_DOMAINS:
        return None
    root = sender_domain.split(".")[0]
    return root.replace("-", " ").title()


def is_recruiter_message(msg) -> tuple[bool, str]:
    """Returns (qualifies, reason). Conservative by design — see module
    docstring. Both signals must be present."""
    from_header = _decode(msg.get("From"))
    display_name, addr = parseaddr(from_header)
    if not addr:
        return False, "no parseable From address"
    if AUTOMATED_SENDER_RE.search(addr):
        return False, "automated/no-reply sender address"
    if not _looks_like_person_name(display_name):
        return False, "From display name doesn't look like a real person"

    subject = _decode(msg.get("Subject"))
    body = _body_text(msg)
    haystack = f"{subject}\n{body[:1500]}"
    if not RECRUITER_INTENT_RE.search(haystack):
        return False, "no recruiter-intent phrase in subject/body"

    return True, "person-like sender + recruiter-intent phrase"


def _get_or_create_company(conn, name_guess: str | None, domain: str | None, now: str) -> int:
    """name_guess=None means 'unknown company, contacted via a portal relay
    or personal address' — still written (the contact itself is real and
    consented), just without a company row to attach useful context to
    later. Dedupe key is the domain when we have one (more reliable than a
    rough name guess), else the literal name string."""
    if domain:
        row = conn.execute("SELECT id FROM company WHERE lower(domain) = lower(?)",
                            (domain,)).fetchone()
        if row:
            return row["id"]
    name = name_guess or "Unknown (via recruiter contact)"
    row = conn.execute("SELECT id FROM company WHERE lower(name) = lower(?)", (name,)).fetchone()
    if row:
        if domain:
            conn.execute("UPDATE company SET domain = ?, updated_at = ? WHERE id = ?",
                         (domain, now, row["id"]))
        return row["id"]
    cur = conn.execute(
        """INSERT INTO company (name, category, source_floor, domain,
                                 is_conflict_of_interest, created_at, updated_at)
           VALUES (?, 'inbound_recruiter_discovered', 'recruiter_mine', ?, 0, ?, ?)""",
        (name, domain, now, now),
    )
    return cur.lastrowid


def mine(conn, log=print, now=None):
    """Scans the personal inbox for recruiter messages, writes new
    authority_node + contact_channel rows (deduped by email address —
    never re-adds an existing contact), logs an INBOUND_RECRUITER_MESSAGE
    event per find with subject/snippet for later reply context. Returns
    the list of newly-written authority_node ids. Disabled/unconfigured is
    the expected default state — returns [] with a clear log line, exactly
    like job_alert_email.py, never an error."""
    now = now or datetime.datetime.utcnow().isoformat()
    cfg = {}
    try:
        import yaml
        with open(os.path.join(os.path.dirname(__file__), "config.yaml"), encoding="utf-8") as f:
            cfg = (yaml.safe_load(f) or {}).get("recruiter_mine") or {}
    except Exception:
        pass
    if not cfg.get("enabled"):
        log("recruiter_mine: disabled in config — skipping")
        return []

    user = os.environ.get("JOB_ALERT_EMAIL", "")
    password = os.environ.get("JOB_ALERT_APP_PASSWORD", "")
    if not user or not password:
        log("recruiter_mine: JOB_ALERT_EMAIL / JOB_ALERT_APP_PASSWORD not set — skipping "
            "(same personal-inbox credentials as sources/job_alert_email.py)")
        return []

    host = cfg.get("imap_host", "imap.gmail.com")
    folder = cfg.get("folder", "INBOX")
    days = int(cfg.get("lookback_days", 30))
    max_msgs = int(cfg.get("max_messages", 300))

    new_node_ids = []
    conn_imap = None
    try:
        conn_imap = imaplib.IMAP4_SSL(host)
        conn_imap.login(user, password)
        conn_imap.select(f'"{folder}"', readonly=True)

        from datetime import date, timedelta
        since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = conn_imap.search(None, f'(SINCE {since})')
        if typ != "OK":
            log(f"recruiter_mine: search failed ({typ}) — skipping")
            return []
        ids = (data[0] or b"").split()[-max_msgs:]
        log(f"recruiter_mine: scanning {len(ids)} message(s) in '{folder}' since {since}")

        seen_this_run = set()
        for mid in ids:
            typ, msg_data = conn_imap.fetch(mid, "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            qualifies, reason = is_recruiter_message(msg)
            if not qualifies:
                continue

            display_name, addr = parseaddr(_decode(msg.get("From")))
            addr = addr.lower()
            if addr in seen_this_run:
                continue
            seen_this_run.add(addr)

            existing = conn.execute(
                "SELECT id FROM contact_channel WHERE lower(value) = ?", (addr,)).fetchone()
            if existing:
                continue  # already mined this contact in a previous run

            domain = _domain_of(addr)
            company_name = _company_guess(domain)
            company_id = _get_or_create_company(conn, company_name, domain, now)

            node_id = store.insert_authority_node(
                conn, company_id, display_name or addr, source="inbound_email",
                created_at=now, node_type="ta_lead_function", confidence=0.7,
            )
            store.insert_contact_channel(
                conn, node_id, "email", addr, consent_basis="inbound_recruiter",
                source_url=None, captured_at=now, confidence=0.9, verified=True,
            )
            store.log_event(
                conn, "authority_node", node_id, "INBOUND_RECRUITER_MESSAGE",
                json.dumps({
                    "subject": _decode(msg.get("Subject"))[:300],
                    "snippet": _body_text(msg, max_chars=500),
                    "received": _decode(msg.get("Date")),
                }),
                now,
            )
            new_node_ids.append(node_id)

    except imaplib.IMAP4.error as e:
        log(f"recruiter_mine: IMAP error ({e}) — skipping")
        return new_node_ids
    except Exception as e:
        log(f"recruiter_mine: failed ({e}) — skipping")
        return new_node_ids
    finally:
        if conn_imap is not None:
            try:
                conn_imap.logout()
            except Exception:
                pass

    log(f"recruiter_mine: {len(new_node_ids)} new recruiter contact(s) found")
    return new_node_ids
