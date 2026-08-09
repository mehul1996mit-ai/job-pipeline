"""A8 — Outreach Composer.

Creates Gmail DRAFTS only — never calls messages.send (F1). Every
precondition in master prompt §8 is enforced here before a single draft is
created; a failed precondition returns (False, reason) rather than raising,
so callers can report why nothing was drafted (mirroring A5's "the absence
of a contact is a valid, expected outcome" pattern — most precondition
checks are expected to fail most of the time, by design, not by bug).

What this module does NOT do: generate the outreach thesis, find the
company-specific fact, or draft the actual prose. That needs real judgment
about what's genuinely specific to a company and defensible in an interview
follow-up — the master prompt's own bar (§8.3) is "could not be true of a
competitor," which is not something to fake with a template. compose_draft()
takes subject/body/specific_fact as inputs from whoever is doing that
thinking (Mehul, or a future LLM-prompted step reviewed before use) and
enforces the MECHANICAL gates: word-count ceilings, the specificity
field being genuinely present (not proof it's true — that's a human
judgment call this code cannot make), and CI/F1/F4/F7 boundaries.
"""
import base64
import datetime
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import outreach_store as store
import ratelimit

OUT_DRAFTS_DIR = os.path.join(os.path.dirname(__file__), "out", "drafts")
SUBJECT_WORD_CEILING = 8
BODY_WORD_CEILING = 150
MIN_OWNS_REQ_LIKELIHOOD_FOR_COLD_OUTREACH = 0.6
MAX_WARM_PATH_DISTANCE_FOR_COLD_OUTREACH = 2
MIN_CHANNEL_CONFIDENCE = 0.6


def _word_count(text):
    return len((text or "").split())


def check_preconditions(conn, company_id, authority_node_id, channel_id, job_id=None):
    """Returns (True, None) or (False, reason). All checks from master
    prompt §8, in the order given there."""
    company = conn.execute("SELECT * FROM company WHERE id = ?", (company_id,)).fetchone()
    if company is None:
        return False, "company not found"

    if company["is_conflict_of_interest"]:
        store.log_event(conn, "company", company_id, "MANUAL_REVIEW_ONLY",
                         '{"reason": "conflict_of_interest"}', datetime.datetime.utcnow().isoformat())
        return False, "conflict_of_interest — routed to manual review, never auto-drafted"

    channel = conn.execute("SELECT * FROM contact_channel WHERE id = ?", (channel_id,)).fetchone()
    if channel is None:
        return False, "contact_channel not found"
    if not channel["consent_basis"]:
        return False, "consent_basis missing (F2)"
    if channel["confidence"] < MIN_CHANNEL_CONFIDENCE:
        return False, f"channel confidence {channel['confidence']} < {MIN_CHANNEL_CONFIDENCE}"

    if store.is_suppressed(conn, channel["value"].strip().lower()):
        return False, "channel is on the suppression list"

    if not ratelimit.can_draft_for_company(conn, company_id):
        return False, "outreach to this company within the last 21 days (F4)"
    if not ratelimit.can_draft_today(conn):
        return False, "daily draft cap already reached (F4)"

    node = conn.execute("SELECT * FROM authority_node WHERE id = ?", (authority_node_id,)).fetchone()
    if node is None:
        return False, "authority_node not found"

    if job_id is None:
        # company-centric (no open req) outreach — the higher bar from §8:
        # cold outreach to a distance-3 node with no req is not permitted.
        if (node["owns_req_likelihood"] is None
                or node["owns_req_likelihood"] < MIN_OWNS_REQ_LIKELIHOOD_FOR_COLD_OUTREACH):
            return False, (f"owns_req_likelihood too low for company-centric outreach "
                            f"(need >= {MIN_OWNS_REQ_LIKELIHOOD_FOR_COLD_OUTREACH})")
        if (node["warm_path_distance"] is None
                or node["warm_path_distance"] > MAX_WARM_PATH_DISTANCE_FOR_COLD_OUTREACH):
            return False, (f"warm_path_distance too far for company-centric outreach "
                            f"(need <= {MAX_WARM_PATH_DISTANCE_FOR_COLD_OUTREACH})")

    return True, None


def validate_composition(subject, body, specific_fact):
    """Mechanical gates only — see module docstring for what this
    deliberately does NOT verify (whether the fact is actually true/specific,
    whether the thesis is actually good). Returns (True, None) or
    (False, reason)."""
    if not specific_fact or not specific_fact.strip():
        return False, ("no specific_fact supplied — §8.3 requires at least one fact that "
                        "could not be true of a competitor; report 'insufficient specific "
                        "basis' instead of drafting rather than send a generic email")
    if _word_count(subject) > SUBJECT_WORD_CEILING:
        return False, f"subject exceeds {SUBJECT_WORD_CEILING} words"
    if _word_count(body) > BODY_WORD_CEILING:
        return False, f"body exceeds {BODY_WORD_CEILING} words"
    return True, None


def _build_mime_message(to_email, subject, body, attachment_path=None):
    msg = MIMEMultipart()
    msg["to"] = to_email
    msg["subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    if attachment_path:
        with open(attachment_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
        part["Content-Disposition"] = f'attachment; filename="{os.path.basename(attachment_path)}"'
        msg.attach(part)
    return msg


def create_gmail_draft(service, to_email, subject, body, attachment_path=None):
    """The only Gmail API call this module makes is drafts().create() —
    never .send(). Returns (draft_id, draft_url, message_id, thread_id).
    message_id/thread_id are what A9 (outreach_crm.py) uses with the
    gmail.readonly scope to detect, later, whether you actually sent this
    draft and whether it got a reply — this module never reads them back
    itself, it only captures them at creation time."""
    msg = _build_mime_message(to_email, subject, body, attachment_path)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    draft_id = draft["id"]
    message = draft.get("message") or {}
    return (draft_id, f"https://mail.google.com/mail/u/0/#drafts?compose={draft_id}",
            message.get("id"), message.get("threadId"))


def create_eml_fallback(to_email, subject, body, attachment_path=None):
    """Used when Gmail access is blocked (admin_policy_enforced/access_denied
    — see gmail_auth.py) or Gmail auth simply isn't set up yet. The pipeline
    stays fully functional with zero Gmail access; import the .eml by hand."""
    os.makedirs(OUT_DRAFTS_DIR, exist_ok=True)
    msg = _build_mime_message(to_email, subject, body, attachment_path)
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_to = "".join(c if c.isalnum() or c in "@.-_" else "_" for c in to_email)
    path = os.path.join(OUT_DRAFTS_DIR, f"{ts}_{safe_to}.eml")
    with open(path, "wb") as f:
        f.write(msg.as_bytes())
    return path


def draft_outreach(conn, company_id, authority_node_id, channel_id, subject, body,
                    specific_fact, gmail_service=None, tailored_cv_path=None, job_id=None):
    """Full A8 pipeline: preconditions -> composition validation -> create
    (Gmail draft, or .eml fallback if gmail_service is None or Gmail
    refuses) -> record the outreach row (which is what ratelimit.py's caps
    check against, so caps are enforced automatically on the next call).
    Returns a result dict on success, or (False, reason) on any rejection —
    never raises for an expected-precondition-failure case."""
    if os.environ.get("CI"):
        raise RuntimeError("outreach.draft_outreach refused: CI env var is set — outreach is local-only (F7)")

    ok, reason = check_preconditions(conn, company_id, authority_node_id, channel_id, job_id)
    if not ok:
        return False, reason
    ok, reason = validate_composition(subject, body, specific_fact)
    if not ok:
        return False, reason

    channel = conn.execute("SELECT * FROM contact_channel WHERE id = ?", (channel_id,)).fetchone()
    to_email = channel["value"]

    gmail_message_id = gmail_thread_id = None
    if gmail_service is not None:
        draft_gmail_id, _, gmail_message_id, gmail_thread_id = create_gmail_draft(
            gmail_service, to_email, subject, body, tailored_cv_path)
        eml_path = None
    else:
        draft_gmail_id = None
        eml_path = create_eml_fallback(to_email, subject, body, tailored_cv_path)

    now = datetime.datetime.utcnow().isoformat()
    cur = conn.execute(
        """INSERT INTO outreach
           (company_id, authority_node_id, job_id, channel_id, draft_gmail_id,
            gmail_message_id, gmail_thread_id, subject, body, state, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFTED', ?)""",
        (company_id, authority_node_id, job_id, channel_id, draft_gmail_id,
         gmail_message_id, gmail_thread_id, subject, body, now),
    )
    return {
        "outreach_id": cur.lastrowid,
        "draft_gmail_id": draft_gmail_id,
        "eml_path": eml_path,
        "to": to_email,
    }


if __name__ == "__main__":
    if os.environ.get("CI"):
        raise SystemExit("outreach.py refuses to run under CI — local-only command (F7)")
    store.init_db()
    with store.connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM contact_channel").fetchone()["n"]
        print(f"{n} contact_channel row(s) exist. draft_outreach() is ready to call once "
              f"A5 (contact_resolution.py) has written at least one — there are none yet "
              f"(see career_agent_smoke_test.py's honest research-pass result).")
