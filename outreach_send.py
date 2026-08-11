"""The ONLY module in this repo permitted to call Gmail's send API.

Every other module (outreach.py/A8, outreach_crm.py/A9) only ever creates
drafts and tracks state — nothing else sends. This module exists because
Mehul asked for a low-friction batch-review flow instead of full
auto-send; full auto-send was declined (see CLAUDE.md's 2026-08-10 entry
for the reasoning: an unreviewed cold email to a real hiring contact is a
different risk class than a bad score sitting in a CSV — it can't be
unsent, and specific_fact/thesis quality is an explicit human judgment
call per outreach.py's own docstring).

F1 is renegotiated here, not removed. gmail_auth.py now requests the real
send-capable scope (SCOPES there is the only place scopes are defined),
but the ONLY function anywhere allowed to call it is send_approved_draft()
below, and it refuses outright without an explicit confirmed=True passed
by its caller — the actual human approval happens one level up, in
streamlit_app.py's "Approve & send" button handler, the only caller that
ever passes confirmed=True. career_agent_smoke_test.py's F1 check is a
whitelist now, not a blanket ban: the send scope string is only permitted
in gmail_auth.py (this paragraph avoids spelling it literally so it
doesn't trip its own guard), and a live drafts().send()/messages().send()
call is only permitted in this file.

send_approved_draft() sends the EXISTING Gmail draft exactly as it was
created and reviewed — it never recomposes the message, so what a human
reviewed is provably what gets sent.
"""
import datetime
import os

import outreach_crm as a9


def list_pending_review(conn):
    """Every outreach still sitting as a Gmail draft, with enough context
    for a real review — company, contact, full subject/body, not a
    snippet. The .eml fallback path (draft_gmail_id NULL) is excluded:
    there's no Gmail draft to send here, those were already handed to
    Mehul as files to send by hand. Oldest first, so nothing silently
    ages out of view."""
    rows = conn.execute(
        """SELECT outreach.*, company.name AS company_name,
                  authority_node.person_name AS person_name,
                  contact_channel.value AS to_email
           FROM outreach
           JOIN company ON company.id = outreach.company_id
           LEFT JOIN authority_node ON authority_node.id = outreach.authority_node_id
           LEFT JOIN contact_channel ON contact_channel.id = outreach.channel_id
           WHERE outreach.state = 'DRAFTED' AND outreach.draft_gmail_id IS NOT NULL
           ORDER BY outreach.created_at ASC"""
    ).fetchall()
    return [dict(r) for r in rows]


def send_approved_draft(conn, service, outreach_id, confirmed=False):
    """Sends outreach_id's existing Gmail draft via drafts().send() —
    refuses without confirmed=True (defense in depth on top of the UI's
    own approve click; nothing can reach this by accident), refuses under
    CI (F7, same boundary as every other send-adjacent path here), refuses
    if the outreach isn't DRAFTED or has no Gmail draft to send. On
    success, transitions the outreach row to SENT_BY_USER through A9's own
    validated state machine (outreach_crm.update_outreach_state) so the
    CRM's history stays consistent regardless of which path sent it."""
    if os.environ.get("CI"):
        raise RuntimeError("outreach_send.send_approved_draft refused: CI env var is set (F7)")
    if not confirmed:
        raise ValueError("send_approved_draft refuses without confirmed=True — this must "
                          "come from an explicit human approval action, never a default")

    row = conn.execute("SELECT * FROM outreach WHERE id = ?", (outreach_id,)).fetchone()
    if row is None:
        raise ValueError(f"outreach {outreach_id} not found")
    if row["state"] != "DRAFTED":
        raise ValueError(f"outreach {outreach_id} is {row['state']}, not DRAFTED — nothing to send")
    if not row["draft_gmail_id"]:
        raise ValueError(f"outreach {outreach_id} has no Gmail draft (.eml fallback path) — "
                          f"send that file yourself, this module can't")

    sent = service.users().drafts().send(userId="me", body={"id": row["draft_gmail_id"]}).execute()

    at = datetime.datetime.utcnow().isoformat()
    a9.update_outreach_state(conn, outreach_id, "SENT_BY_USER",
                              reason="approved_via_review_ui", at=at)
    return sent
