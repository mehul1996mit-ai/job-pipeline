"""A9 — CRM & calibration loop.

Tracks what actually happens after a draft becomes a real, human-sent
email: sent/reply detection using the gmail.readonly scope already granted
in gmail_auth.py, a follow-up scheduler bounded by ratelimit's F4 caps, and
the 30-day weight refit that authority_graph.py's owns_req_likelihood()
docstring has been pointing at since A3 ("needs A9 with n>=20 real
outcomes, per master prompt §9"). Same two hard rules as feedback.py's
learning loop: it NEVER auto-applies a refit, and it NEVER concludes below
the n>=20 floor.

Read-only w.r.t. Gmail — detect_sent_via_gmail() and check_for_replies()
only ever call messages()/threads() GET, never send/modify/delete. Sending
a follow-up is still a human action; record_followup_sent() is called
AFTER you've actually sent one, same boundary as the rest of this repo.

As of 2026-08-09 there are ZERO real outreach rows in the system (see
CLAUDE.md/README.md — A8 is live-verified but nothing has been sent to a
real contact yet). refit_owns_req_likelihood() against the live DB will
honestly report "not enough data" right now — that's the correct, expected
output, not a bug. Every function here is real and unit-tested against
synthetic fixtures in career_agent_smoke_test.py.
"""
import datetime
import json

import outreach_store as store
import ratelimit

# --------------------------------------------------------------- state machine
# The only transitions this module will perform. Nothing may jump straight
# from DRAFTED to REPLIED/INTERVIEW, etc. — every hop is validated and every
# hop logs an event (never a silent UPDATE).
ALLOWED_TRANSITIONS = {
    "DRAFTED": {"SENT_BY_USER", "CLOSED"},
    "SENT_BY_USER": {"REPLIED", "REJECTED", "CLOSED"},
    "REPLIED": {"INTERVIEW", "REJECTED", "CLOSED"},
    "INTERVIEW": {"REJECTED", "CLOSED"},
    "REJECTED": {"CLOSED"},
    "CLOSED": set(),
}

# closed_reason values that mean "this person asked not to be contacted
# again" — the ONLY case this module auto-suppresses a channel. A cold
# no-reply, a filled role, or a plain rejection are NOT consent withdrawal
# and must never suppress on their own.
DO_NOT_CONTACT_REASONS = {"declined_do_not_contact", "user_opted_out"}


def update_outreach_state(conn, outreach_id, new_state, reason=None, at=None, payload=None):
    """The only permitted state-transition path. Raises ValueError on a
    transition not in ALLOWED_TRANSITIONS. Always logs an event; on CLOSED,
    stores closed_reason and — only for an explicit do-not-contact reason —
    adds the channel to the suppression table so a later re-outreach can't
    happen by accident."""
    at = at or datetime.datetime.utcnow().isoformat()
    row = conn.execute("SELECT * FROM outreach WHERE id = ?", (outreach_id,)).fetchone()
    if row is None:
        raise ValueError(f"outreach {outreach_id} not found")
    current = row["state"]
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if new_state not in allowed:
        raise ValueError(f"{current} -> {new_state} is not an allowed transition "
                          f"(allowed from {current}: {sorted(allowed) or 'none, terminal state'})")

    fields = {"state": new_state}
    if new_state == "SENT_BY_USER":
        fields["user_sent_at"] = at
    if new_state == "CLOSED":
        fields["closed_reason"] = reason

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE outreach SET {set_clause} WHERE id = ?", (*fields.values(), outreach_id))
    store.log_event(conn, "outreach", outreach_id, f"STATE_{new_state}",
                     json.dumps({"from": current, "reason": reason, **(payload or {})}), at)

    if new_state == "CLOSED" and reason in DO_NOT_CONTACT_REASONS and row["channel_id"]:
        channel = conn.execute("SELECT value FROM contact_channel WHERE id = ?",
                                (row["channel_id"],)).fetchone()
        if channel:
            value = channel["value"].strip().lower()
            if not store.is_suppressed(conn, value):
                conn.execute(
                    "INSERT INTO suppression (value, scope, reason, added_at) VALUES (?, 'email', ?, ?)",
                    (value, f"outreach_{outreach_id}_{reason}", at),
                )
                store.log_event(conn, "contact_channel", row["channel_id"], "AUTO_SUPPRESSED",
                                 json.dumps({"outreach_id": outreach_id, "reason": reason}), at)

    updated = dict(row)
    updated.update(fields)
    return updated


def mark_sent(conn, outreach_id, sent_at=None):
    """Manual confirmation — you telling the CRM 'I actually sent this
    one'. Always available regardless of whether Gmail detection below is
    ever run; not sending is the same human-action boundary as not
    submitting an application, and this module can't assume Gmail access
    exists (the .eml fallback path has no message id to detect against at
    all)."""
    return update_outreach_state(conn, outreach_id, "SENT_BY_USER", at=sent_at)


def detect_sent_via_gmail(service, conn):
    """gmail.readonly only — reads label state, never sends anything.

    Matches by gmail_thread_id, NOT gmail_message_id. Confirmed live
    (2026-08-10) that Gmail reassigns a NEW message id when a draft is
    sent — true both for a human manually clicking Send in the Gmail web
    UI and for the live send call in outreach_send.py. Only the thread id
    survives the draft->sent transition. An
    earlier version of this function matched on the stored message id and
    could never fire: every real send 404'd against that stale id and was
    silently swallowed, leaving the row stuck at DRAFTED forever. Rows
    without a gmail_thread_id (the .eml fallback path) are skipped —
    mark_sent() is the only path in for those. On a match, gmail_message_id
    is updated to the real sent message's id. Returns the outreach_ids
    transitioned."""
    moved = []
    rows = conn.execute(
        "SELECT * FROM outreach WHERE state = 'DRAFTED' AND gmail_thread_id IS NOT NULL"
    ).fetchall()
    for row in rows:
        try:
            thread = service.users().threads().get(
                userId="me", id=row["gmail_thread_id"], format="minimal").execute()
        except Exception:
            continue  # thread id gone (draft discarded) — leave as DRAFTED, not an error
        sent_message = None
        for m in thread.get("messages") or []:
            labels = set(m.get("labelIds") or [])
            if "SENT" in labels and "DRAFT" not in labels:
                sent_message = m
                break
        if sent_message is None:
            continue
        conn.execute("UPDATE outreach SET gmail_message_id = ? WHERE id = ?",
                     (sent_message["id"], row["id"]))
        update_outreach_state(conn, row["id"], "SENT_BY_USER",
                               reason="gmail_sent_label_detected")
        moved.append(row["id"])
    return moved


def check_for_replies(service, conn):
    """gmail.readonly only. For every SENT_BY_USER outreach with a stored
    thread id, looks for any message in the thread NOT from you — a real
    reply. Deliberately does NOT classify the reply as good/bad/interview —
    same judgment-call boundary as A8's specific_fact; this only detects
    that a reply landed and logs its snippet so you can read it and call
    update_outreach_state() yourself with the right next state (REPLIED at
    minimum; INTERVIEW/REJECTED/CLOSED once you've actually read it).
    Returns the outreach_ids moved to REPLIED."""
    found = []
    rows = conn.execute(
        "SELECT * FROM outreach WHERE state = 'SENT_BY_USER' AND gmail_thread_id IS NOT NULL"
    ).fetchall()
    if not rows:
        return found
    my_email = (service.users().getProfile(userId="me").execute().get("emailAddress") or "").lower()
    for row in rows:
        try:
            thread = service.users().threads().get(
                userId="me", id=row["gmail_thread_id"], format="metadata",
                metadataHeaders=["From"]).execute()
        except Exception:
            continue
        reply = None
        for m in thread.get("messages") or []:
            headers = {h["name"]: h["value"] for h in (m.get("payload", {}).get("headers") or [])}
            frm = headers.get("From", "").lower()
            if my_email and my_email not in frm:
                reply = m
                break
        if reply is None:
            continue
        update_outreach_state(conn, row["id"], "REPLIED", reason="gmail_reply_detected",
                               payload={"snippet": reply.get("snippet", "")})
        found.append(row["id"])
    return found


# ------------------------------------------------------------------ follow-ups
def schedule_followup(conn, outreach_id, days_from_now=7, at=None):
    """Sets next_followup_due. Refuses (raises) if the F4 follow-up cap is
    already reached or the outreach isn't in a state a follow-up makes
    sense for (SENT_BY_USER — no reply yet to follow up on)."""
    at = at or datetime.datetime.utcnow()
    row = conn.execute("SELECT * FROM outreach WHERE id = ?", (outreach_id,)).fetchone()
    if row is None:
        raise ValueError(f"outreach {outreach_id} not found")
    if row["state"] != "SENT_BY_USER":
        raise ValueError(f"can only schedule a follow-up for SENT_BY_USER outreach, this is {row['state']}")
    if not ratelimit.can_followup(row["followup_count"]):
        raise ValueError(f"follow-up cap reached ({row['followup_count']}/"
                          f"{ratelimit.MAX_FOLLOWUPS_PER_THREAD}, F4)")
    due = (at + datetime.timedelta(days=days_from_now)).date().isoformat()
    conn.execute("UPDATE outreach SET next_followup_due = ? WHERE id = ?", (due, outreach_id))
    store.log_event(conn, "outreach", outreach_id, "FOLLOWUP_SCHEDULED",
                     json.dumps({"due": due}), at.isoformat())
    return due


def record_followup_sent(conn, outreach_id, at=None):
    """Call AFTER you've actually sent a follow-up — this never sends one
    itself. Increments followup_count (F4-capped) and clears
    next_followup_due so due_followups() stops surfacing it until
    scheduled again."""
    at = at or datetime.datetime.utcnow().isoformat()
    row = conn.execute("SELECT * FROM outreach WHERE id = ?", (outreach_id,)).fetchone()
    if row is None:
        raise ValueError(f"outreach {outreach_id} not found")
    if not ratelimit.can_followup(row["followup_count"]):
        raise ValueError(f"follow-up cap reached ({row['followup_count']}/"
                          f"{ratelimit.MAX_FOLLOWUPS_PER_THREAD}, F4)")
    conn.execute(
        "UPDATE outreach SET followup_count = followup_count + 1, next_followup_due = NULL WHERE id = ?",
        (outreach_id,),
    )
    store.log_event(conn, "outreach", outreach_id, "FOLLOWUP_SENT",
                     json.dumps({"followup_count": row["followup_count"] + 1}), at)


def due_followups(conn, today=None):
    """SENT_BY_USER outreach, past its scheduled follow-up date, still
    under the F4 cap. Doesn't send anything — this is what a dashboard/CLI
    surfaces to you to act on."""
    today = today or datetime.date.today().isoformat()
    rows = conn.execute(
        "SELECT * FROM outreach WHERE state = 'SENT_BY_USER' "
        "AND next_followup_due IS NOT NULL AND next_followup_due <= ?",
        (today,),
    ).fetchall()
    return [dict(r) for r in rows if ratelimit.can_followup(r["followup_count"])]


# ------------------------------------------------------------------ calibration
MIN_OUTCOMES_FOR_REFIT = 20   # master prompt §9's own floor — same rule
                               # authority_graph.py's owns_req_likelihood()
                               # docstring has cited since A3.
MIN_PER_NODE_TYPE = 4         # a per-type prior needs examples of that type,
                               # not just overall volume.
MAX_LIKELIHOOD_NUDGE = 0.25   # absolute cap per refit, same discipline as
                               # feedback.py's MAX_WEIGHT_NUDGE (that one's
                               # relative because weights must sum to a
                               # total; these are independent 0-1
                               # probabilities, so the cap is additive).
REFIT_MIN_AGE_DAYS = 30       # the "30-day weight refit": a still-open
                               # SENT_BY_USER thread only counts as a
                               # negative (no-reply) signal once it's been
                               # open at least this long — a fresh send
                               # isn't evidence of anything yet.

POSITIVE_STATES = {"REPLIED", "INTERVIEW"}
NEGATIVE_TERMINAL_STATES = {"REJECTED"}


def _outcome_rows(conn, now=None):
    """(node_type, outcome) pairs for every outreach with a real, knowable
    outcome. Excludes anything still ambiguous (DRAFTED, a fresh
    SENT_BY_USER under REFIT_MIN_AGE_DAYS, a CLOSED whose reason isn't
    informative) — same principle as feedback.py excluding 'partial'
    rather than forcing it onto a binary axis."""
    now = now or datetime.datetime.utcnow()
    rows = conn.execute(
        "SELECT outreach.*, authority_node.seniority_band AS node_type "
        "FROM outreach JOIN authority_node ON authority_node.id = outreach.authority_node_id "
        "WHERE outreach.state != 'DRAFTED'"
    ).fetchall()
    usable = []
    for r in rows:
        state = r["state"]
        if not r["node_type"]:
            continue
        if state in POSITIVE_STATES:
            usable.append((r["node_type"], 1))
        elif state in NEGATIVE_TERMINAL_STATES:
            usable.append((r["node_type"], 0))
        elif state == "CLOSED" and r["closed_reason"] not in DO_NOT_CONTACT_REASONS:
            usable.append((r["node_type"], 0))
        elif state == "SENT_BY_USER" and r["user_sent_at"]:
            age = (now - datetime.datetime.fromisoformat(r["user_sent_at"])).days
            if age >= REFIT_MIN_AGE_DAYS:
                usable.append((r["node_type"], 0))
        # else: no knowable outcome yet — excluded, not counted as either class.
    return usable


def refit_owns_req_likelihood(conn, now=None, min_n=MIN_OUTCOMES_FOR_REFIT):
    """PROPOSES adjusted NODE_TYPE_BASE_LIKELIHOOD priors from real reply
    outcomes. Same two hard rules as feedback.py's propose_weights(): NEVER
    auto-applies (authority_graph.py's NODE_TYPE_BASE_LIKELIHOOD is never
    written by this function — copy the numbers over by hand if you want
    them), and NEVER concludes below the n>=20 floor. Below that floor (or
    with any node type under-sampled) this reports the honest shortfall —
    which, against the live DB as of 2026-08-09, is 0/20: there are zero
    real outreach outcomes yet, so this is expected, not broken."""
    usable = _outcome_rows(conn, now)
    total = len(usable)
    if total < min_n:
        return {
            "ready": False, "total": total, "min_n": min_n,
            "note": f"{total}/{min_n} real outreach outcomes so far — nothing is "
                    f"concluded below that floor (master prompt §9).",
        }

    import authority_graph as a3
    current = dict(a3.NODE_TYPE_BASE_LIKELIHOOD)
    by_type = {t: [] for t in current}
    for node_type, outcome in usable:
        by_type.setdefault(node_type, []).append(outcome)

    thin = [t for t in current if len(by_type.get(t, [])) < MIN_PER_NODE_TYPE]
    if thin:
        return {
            "ready": False, "total": total, "min_n": min_n,
            "note": f"{total} total outcomes, but these node types still have fewer "
                    f"than {MIN_PER_NODE_TYPE} samples: {thin}. A per-type prior "
                    f"needs real examples of each type, not just overall volume.",
        }

    reply_rate = {t: sum(v) / len(v) for t, v in by_type.items()}
    proposed = {}
    for t, base in current.items():
        rate = reply_rate.get(t, base)
        nudge = max(-MAX_LIKELIHOOD_NUDGE, min(MAX_LIKELIHOOD_NUDGE, rate - base))
        proposed[t] = round(max(0.0, min(1.0, base + nudge)), 2)

    moved = sorted(((t, round(proposed[t] - current[t], 2)) for t in current),
                    key=lambda kv: -abs(kv[1]))
    return {
        "ready": True, "total": total, "sample_counts": {t: len(v) for t, v in by_type.items()},
        "observed_reply_rate": {t: round(r, 2) for t, r in reply_rate.items()},
        "current_priors": current, "proposed_priors": proposed, "biggest_moves": moved,
        "note": "Proposal only — authority_graph.py's NODE_TYPE_BASE_LIKELIHOOD is "
                f"untouched by this function, and no single type moved more than "
                f"{MAX_LIKELIHOOD_NUDGE} absolute this round. Review and edit that "
                "dict by hand if you want to apply it.",
    }


if __name__ == "__main__":
    import os
    if os.environ.get("CI"):
        raise SystemExit("outreach_crm.py refuses to run under CI — local-only command (F7)")
    store.init_db()
    with store.connect() as conn:
        refit = refit_owns_req_likelihood(conn)
        print(f"Refit: {refit['note']}")

        due = due_followups(conn)
        print(f"{len(due)} follow-up(s) due.")
        for d in due:
            print(f"  outreach #{d['id']} (company_id={d['company_id']}), "
                  f"{d['followup_count']}/{ratelimit.MAX_FOLLOWUPS_PER_THREAD} follow-ups used")

        try:
            import gmail_auth
            service = gmail_auth.get_service()
        except Exception as e:
            print(f"\nGmail not available ({e}) — skipping sent/reply detection. "
                  f"Use mark_sent()/update_outreach_state() manually instead.")
        else:
            sent = detect_sent_via_gmail(service, conn)
            replied = check_for_replies(service, conn)
            print(f"\n{len(sent)} outreach row(s) detected as sent, {len(replied)} new repl{'y' if len(replied)==1 else 'ies'} detected.")
