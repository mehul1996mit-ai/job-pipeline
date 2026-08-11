"""Local smoke test for Career Agent's P0 guardrails + A2/A3/A5/A8/A9.
No API keys needed. A5/A8's MX-record checks do real DNS lookups (no API
key, but does need network reachability) — everything else is offline.
These live lookups are occasionally flaky against transient DNS hiccups
(observed 2026-08-09) — a failure isolated to has_mx_record()/
resolve_contact() and nothing else usually clears on a re-run; a failure
that repeats 2+ times in a row is a real bug, not flakiness.

Run:  python career_agent_smoke_test.py

Verifies: (1) F1 — the gmail.send scope string and a live Gmail send API
call are each confined to exactly one whitelisted file (gmail_auth.py and
outreach_send.py respectively) — renegotiated 2026-08-10 from a blanket
ban to a whitelist, see CLAUDE.md, (2) F2 — contact_channel refuses a
null/invalid consent_basis and accepts a valid one, (3) F4 — draft-per-day
and per-company-cooldown caps are enforced and can't be raised past the
ceiling by config, (4) A2 — the allowlist floor force-includes every
company, Bajaj Finance is flagged conflict-of-interest, and floor companies
are exempt from the DORMANT cap, (5) A3 — node source is gated the same way
F2 gates consent_basis, function/node-type classification matches known
cases, owns_req_likelihood modifiers apply correctly, and warm_path_distance
only ever comes from network.yaml, never a traversal, (6) A5 — email
syntax/MX/domain-match/suppression/dedupe all gate before
insert_contact_channel ever gets called, and confidence scoring behaves,
(7) A8 — every §8 precondition (conflict-of-interest, confidence floor,
suppression, F4 caps, owns_req_likelihood/warm_path floor for no-job
outreach) gates before a draft is created, and composition validation
(specificity required, length ceilings) is enforced. Uses the .eml fallback
path (gmail_service=None), not live Gmail — the real Gmail path was
verified manually against mehul.96.mit@gmail.com, see CLAUDE.md.
(8) A9 — the outreach state machine, do-not-contact-only suppression,
follow-up caps, and the 30-day weight refit's honest-shortfall/math paths.
(9) Outreach review/send — send_approved_draft() refuses without an
explicit confirmed=True, refuses for a non-DRAFTED or .eml-fallback
outreach, refuses under CI, and on success sends the EXACT existing draft
(never recomposes) and transitions state through A9's own machine. Uses a
fake Gmail service double, not a live send.
"""
import datetime
import os
import sqlite3
import sys
import tempfile

PASS, FAIL = "  [PASS]", "  [FAIL]"
failures = 0


def check(name, condition, detail=""):
    global failures
    print(f"{PASS if condition else FAIL} {name} {detail}")
    if not condition:
        failures += 1


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

print("== 1. F1 — human sends, always (whitelist model, renegotiated 2026-08-10)")
# Two things are checked separately: (a) the gmail.send SCOPE STRING may
# only appear where scopes are declared (gmail_auth.py), and (b) a live
# Gmail send API CALL (drafts().send(/messages().send() — a re-decompose-
# and-send in a different module would bypass outreach_send.py's
# confirmed=True gate entirely, so this checks the call site, not just the
# scope) may only appear in outreach_send.py, the one function anywhere
# permitted to actually send. Anywhere else finding either string is a
# real violation, not a style nit — see CLAUDE.md's 2026-08-10 entry for
# why this moved from a blanket ban to a whitelist.
import re

SEND_SCOPE_ALLOWED_IN = {"gmail_auth.py"}
SEND_CALL_ALLOWED_IN = {"outreach_send.py"}
SEND_CALL_RE = re.compile(r"(drafts\(\)|messages\(\))\.send\(")

scope_hits, call_hits = [], []
for root, dirs, files in os.walk(REPO_ROOT):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "data")]
    for fn in files:
        # .md is deliberately excluded: docs (CLAUDE.md/README.md) legitimately
        # name these strings in prose while explaining the guard — only
        # code/config paths matter here.
        if not fn.endswith((".py", ".yaml", ".yml")):
            continue
        path = os.path.join(root, fn)
        if path == os.path.abspath(__file__):
            continue  # this file names the forbidden strings in its own checks
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        if "messages/send" in text or "gmail.send" in text.replace('"', "").replace("'", ""):
            if fn not in SEND_SCOPE_ALLOWED_IN:
                scope_hits.append(path)
        if SEND_CALL_RE.search(text) and fn not in SEND_CALL_ALLOWED_IN:
            call_hits.append(path)
check("gmail.send scope string appears only in gmail_auth.py", not scope_hits, str(scope_hits))
check("a live drafts().send()/messages().send() call exists ONLY in outreach_send.py",
      not call_hits, str(call_hits))

with open(os.path.join(REPO_ROOT, "outreach_send.py"), "r", encoding="utf-8") as f:
    send_module_text = f.read()
check("outreach_send.py itself really does call a Gmail send API (the whitelist "
      "isn't just permitting an empty file)", bool(SEND_CALL_RE.search(send_module_text)))

print("\n== 2. F2 — consented contacts only")
import outreach_store as store

tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "test.sqlite3")
store.init_db(db_path)
now = datetime.datetime.utcnow().isoformat()

with store.connect(db_path) as conn:
    conn.execute(
        "INSERT INTO company (name, created_at, updated_at) VALUES ('TestCo', ?, ?)",
        (now, now),
    )
    company_id = conn.execute("SELECT id FROM company WHERE name='TestCo'").fetchone()["id"]
    conn.execute(
        """INSERT INTO authority_node (company_id, person_name, source, created_at)
           VALUES (?, 'Jane Doe', 'careers_page', ?)""",
        (company_id, now),
    )
    node_id = conn.execute("SELECT id FROM authority_node").fetchone()["id"]

    raised_on_null = False
    try:
        store.insert_contact_channel(
            conn, node_id, "email", "jane@testco.com",
            consent_basis=None, source_url="https://testco.com/careers",
            captured_at=now, confidence=0.9,
        )
    except ValueError:
        raised_on_null = True
    check("null consent_basis raises", raised_on_null)

    raised_on_invalid = False
    try:
        store.insert_contact_channel(
            conn, node_id, "email", "jane.doe@testco.com",  # pattern-guessed shape
            consent_basis="pattern_guessed", source_url=None,
            captured_at=now, confidence=0.9,
        )
    except ValueError:
        raised_on_invalid = True
    check("consent_basis outside the allowlist raises (e.g. pattern-guessed)", raised_on_invalid)

    store.insert_contact_channel(
        conn, node_id, "email", "jane@testco.com",
        consent_basis="careers_page_published", source_url="https://testco.com/careers",
        captured_at=now, confidence=0.9,
    )
    row = conn.execute("SELECT is_contactable FROM authority_node WHERE id=?", (node_id,)).fetchone()
    check("valid consent_basis is accepted and marks the node contactable", row["is_contactable"] == 1)

    schema_raised = False
    try:
        conn.execute(
            """INSERT INTO contact_channel
               (authority_node_id, channel_type, value, consent_basis, captured_at, confidence)
               VALUES (?, 'email', 'x@y.com', NULL, ?, 0.9)""",
            (node_id, now),
        )
    except sqlite3.IntegrityError:
        schema_raised = True
    check("schema-level NOT NULL also refuses a direct bypass insert", schema_raised)

print("\n== 3. F4 — volume caps, clamped in code")
import ratelimit

check("config cannot raise the daily draft cap above 20",
      ratelimit.clamp(999, ratelimit.MAX_DRAFTS_PER_DAY) == 20)
check("config cannot raise the per-company cooldown above 21 days",
      ratelimit.clamp(999, ratelimit.MAX_DAYS_BETWEEN_OUTREACH_SAME_COMPANY) == 21)
check("config CAN lower the per-company cooldown below 21 days",
      ratelimit.clamp(1, ratelimit.MAX_DAYS_BETWEEN_OUTREACH_SAME_COMPANY) == 1)

with store.connect(db_path) as conn:
    conn.execute(
        "INSERT INTO company (name, created_at, updated_at) VALUES ('CapCo', ?, ?)", (now, now)
    )
    cap_company_id = conn.execute("SELECT id FROM company WHERE name='CapCo'").fetchone()["id"]
    today = datetime.date.today().isoformat()
    for i in range(20):
        conn.execute(
            "INSERT INTO outreach (company_id, state, created_at) VALUES (?, 'DRAFTED', ?)",
            (cap_company_id, f"{today}T{i:02d}:00:00"),
        )
    check("21st draft in a day is refused", not ratelimit.can_draft_today(conn, today=today))
    check("2nd outreach to the same company inside 21 days is refused",
          not ratelimit.can_draft_for_company(conn, cap_company_id,
                                               now=datetime.datetime.utcnow()))

print("\n== 4. A2 — company targeting")
import company_targeting as a2

allowlist = a2.load_allowlist()
check("allowlist loads and dedupes to the documented count",
      len(allowlist) > 100, f"({len(allowlist)} companies)")
names = {c["name"] for c in allowlist}
check("Perfios appears once (deduped alias)", "Perfios" in names)
check("M2P Fintech appears once (deduped alias)", "M2P Fintech" in names)
bajaj = next((c for c in allowlist if c["name"] == "Bajaj Finance"), None)
check("Bajaj Finance is flagged conflict-of-interest",
      bajaj is not None and bajaj["is_conflict_of_interest"] is True)
non_conflict = [c for c in allowlist if c["name"] != "Bajaj Finance"]
check("everything else is NOT flagged conflict-of-interest",
      all(not c["is_conflict_of_interest"] for c in non_conflict))

a2_db = os.path.join(tmpdir, "a2.sqlite3")
seen_jobs = os.path.join(tmpdir, "seen_jobs.json")
import json
with open(seen_jobs, "w", encoding="utf-8") as f:
    json.dump({
        "h1": {"first_seen": datetime.date.today().isoformat(), "company": "Razorpay", "title": "PM"},
        "h2": {"first_seen": datetime.date.today().isoformat(), "company": "Razorpay", "title": "BA"},
        "h3": {"first_seen": (datetime.date.today() - datetime.timedelta(days=200)).isoformat(),
               "company": "Razorpay", "title": "old, outside window"},
    }, f)

ranked = a2.run(db_path=a2_db, seen_jobs_path=seen_jobs)
check("run() force-includes every allowlist company",
      sum(1 for r in ranked if r["source_floor"] == "user_allowlist") == len(allowlist),
      f"({len(ranked)} total)")
razorpay = next(r for r in ranked if r["name"] == "Razorpay")
check("hiring activity inside the 90-day window is counted (2 reqs, not 3)",
      razorpay["explain"]["req_count_90d"] == 2)
zero_req_company = next(r for r in ranked if r["explain"]["req_count_90d"] == 0)
check("floor companies with zero reqs stay ACTIVE (DORMANT-exempt)",
      zero_req_company["status"] == "ACTIVE" and zero_req_company["source_floor"] == "user_allowlist")

# re-run to confirm idempotency: force-include never duplicates rows
ranked2 = a2.run(db_path=a2_db, seen_jobs_path=seen_jobs)
check("re-running is idempotent (no duplicate company rows)", len(ranked2) == len(ranked))

print("\n== 5. A3 — hiring authority graph")
import authority_graph as a3

check("function owner title classifies as function_owner",
      a3.classify_node_type("Head of Product", a3.classify_function("Head of Product")) == "function_owner")
check("plain function title classifies as hiring_manager",
      a3.classify_node_type("Product Manager", a3.classify_function("Product Manager")) == "hiring_manager")
check("TA + function title classifies as ta_lead_function",
      a3.classify_node_type("Talent Acquisition Partner - Product Manager Hiring",
                             a3.classify_function("Talent Acquisition Partner - Product Manager Hiring"))
      == "ta_lead_function")
check("generic HR/TA title with no function classifies as generic_ta",
      a3.classify_node_type("HR Business Partner", a3.classify_function("HR Business Partner"))
      == "generic_ta")

score_unknown_size, _ = a3.owns_req_likelihood("function_owner", None, None)
check("function_owner base likelihood with nothing else known is the documented prior",
      score_unknown_size == 0.85)
score_small_open, _ = a3.owns_req_likelihood("function_owner", 1000, True)
check("small company + open req boosts a function owner toward 1.0 (clipped)",
      score_small_open == 1.0)
score_large_no_req, _ = a3.owns_req_likelihood("function_owner", 5000, False)
check(">=2000hc + no open req downweights a function owner below base",
      score_large_no_req == 0.60, f"(got {score_large_no_req})")

net = a3.load_network()
check("network.yaml with an empty people list loads cleanly", net == {})
distance, via = a3.warm_path_distance("Someone Not In The File", net)
check("unlisted person defaults to warm_path_distance=2 (sub-sector adjacency), not 3",
      distance == 2 and via is None)

bad_source_raised = False
try:
    with store.connect(a2_db) as conn:
        store.insert_authority_node(conn, 1, "Test Person", "linkedin_scrape", now)
except ValueError:
    bad_source_raised = True
check("insert_authority_node refuses a source outside the allowlist (e.g. linkedin_scrape)",
      bad_source_raised)

with store.connect(a2_db) as conn:
    conn.execute(
        "INSERT INTO company (name, source_floor, created_at, updated_at) "
        "VALUES ('A3TestCo', 'user_allowlist', ?, ?)", (now, now),
    )
    result = a3.add_manual_node(conn, "A3TestCo", "Priya Sharma", "Head of Product",
                                 hiring_counts={"a3testco": 1})
    check("add_manual_node writes a real row with computed function/type/likelihood",
          result["function"] == "product" and result["node_type"] == "function_owner"
          and result["owns_req_likelihood"] == 0.95,
          f"(got {result})")

    raised_unknown_company = False
    try:
        a3.add_manual_node(conn, "NotInTargetList Inc", "Nobody", "VP Product")
    except ValueError:
        raised_unknown_company = True
    check("add_node refuses a company that isn't in A2's target list yet", raised_unknown_company)

    company_id = conn.execute("SELECT id FROM company WHERE name='A3TestCo'").fetchone()["id"]

    # a second node, lower down the priority order, to prove ranking actually orders
    # by owns_req_likelihood rather than insertion order.
    a3.add_node(conn, "A3TestCo", "Manager Person", "Product Manager", source="user_manual_entry")
    ranked_nodes = a3.rank_nodes_for_company(conn, company_id)
    check("rank_nodes_for_company returns both nodes added so far", len(ranked_nodes) == 2)
    check("rank_nodes_for_company ranks the function owner above the hiring manager",
          ranked_nodes[0]["person_name"] == "Priya Sharma"
          and ranked_nodes[0]["owns_req_likelihood"] > ranked_nodes[-1]["owns_req_likelihood"])

    no_company_raised = False
    try:
        a3.add_node(conn, "NotATargetCompany", "Nobody", "Head of Product", source="user_manual_entry")
    except ValueError:
        no_company_raised = True
    check("add_node refuses to attach a node to a company outside the target list", no_company_raised)

check("classify_function: 'Business Analyst' -> business_analysis",
      a3.classify_function("Business Analyst") == "business_analysis")
check("classify_function: unrelated title -> None",
      a3.classify_function("Chief Financial Officer") is None)
check("owns_req_likelihood: generic_ta base rate 0.20",
      a3.owns_req_likelihood("generic_ta", None, None)[0] == 0.20)
distance, via = a3.warm_path_distance("Rohan Mehta", {"rohanmehta": {"distance": 1, "via": "ex-colleague"}})
check("warm_path_distance reads a real network.yaml-shaped entry when present",
      distance == 1 and via == "ex-colleague")

print("\n== 6. A5 — contact resolution")
import contact_resolution as a5

check("validate_syntax accepts a well-formed address", a5.validate_syntax("jane@testco.com"))
check("validate_syntax rejects a missing @", not a5.validate_syntax("janetestco.com"))
check("validate_syntax rejects a missing TLD", not a5.validate_syntax("jane@testco"))

check("has_mx_record is True for a real, live domain (razorpay.com)",
      a5.has_mx_record("razorpay.com"))
check("has_mx_record is False for a domain that can't exist",
      not a5.has_mx_record("this-domain-does-not-exist-jt-career-agent.invalid"))

check("domain_relationship: exact company domain match",
      a5.domain_relationship("razorpay.com", "razorpay.com") == "exact")
check("domain_relationship: documented subsidiary matches",
      a5.domain_relationship("setu.co", "pinelabs.com", ["setu.co"]) == "subsidiary")
check("domain_relationship: unrelated domain is None (hard reject)",
      a5.domain_relationship("gmail.com", "razorpay.com") is None)

conf_exact = a5.compute_confidence("careers_page_published", "exact", now)
conf_subsidiary = a5.compute_confidence("careers_page_published", "subsidiary", now)
check("confidence: exact domain match scores higher than subsidiary",
      conf_exact > conf_subsidiary)
conf_referral = a5.compute_confidence("user_network_referral", "exact", now)
check("confidence: user_network_referral (0.85 base) beats careers_page_published (0.65 base)",
      conf_referral > conf_exact)
old_captured = (datetime.datetime.utcnow() - datetime.timedelta(days=300)).isoformat()
conf_stale = a5.compute_confidence("careers_page_published", "exact", old_captured)
check("confidence: a >180-day-old capture is penalized vs a fresh one",
      conf_stale < conf_exact)

with store.connect(db_path) as conn:
    # domain='razorpay.com' so has_mx_record has a real, live domain to check
    # against without depending on a fake test domain resolving.
    conn.execute(
        "INSERT INTO company (name, domain, created_at, updated_at) "
        "VALUES ('A5TestCo', 'razorpay.com', ?, ?)", (now, now),
    )
    company_id = conn.execute("SELECT id FROM company WHERE name='A5TestCo'").fetchone()["id"]
    conn.execute(
        "INSERT INTO authority_node (company_id, person_name, source, created_at) "
        "VALUES (?, 'Contact Test Person', 'user_manual_entry', ?)", (company_id, now),
    )
    a5_node_id = conn.execute("SELECT id FROM authority_node WHERE person_name='Contact Test Person'").fetchone()["id"]

    result = a5.resolve_contact(conn, a5_node_id, "jane@razorpay.com", "careers_page_published",
                                 "https://razorpay.com/careers", company_domain="razorpay.com")
    check("resolve_contact writes a channel when every gate passes", result is not None)

    rejected_bad_syntax = a5.resolve_contact(conn, a5_node_id, "not-an-email", "careers_page_published",
                                              None, company_domain="razorpay.com")
    check("resolve_contact rejects bad syntax (returns None, not an exception)",
          rejected_bad_syntax is None)

    rejected_wrong_domain = a5.resolve_contact(conn, a5_node_id, "someone@gmail.com", "careers_page_published",
                                                None, company_domain="razorpay.com")
    check("resolve_contact rejects a domain that doesn't match the company's own",
          rejected_wrong_domain is None)

    rejected_dup = a5.resolve_contact(conn, a5_node_id, "JANE@RAZORPAY.COM", "careers_page_published",
                                       None, company_domain="razorpay.com")
    check("resolve_contact rejects a case-insensitive duplicate of an existing channel",
          rejected_dup is None)

    conn.execute("INSERT INTO suppression (value, scope, added_at) VALUES (?, 'email', ?)",
                  ("suppressed@razorpay.com", now))
    rejected_suppressed = a5.resolve_contact(conn, a5_node_id, "suppressed@razorpay.com",
                                              "careers_page_published", None, company_domain="razorpay.com")
    check("resolve_contact rejects a suppressed address", rejected_suppressed is None)

    events = conn.execute(
        "SELECT COUNT(*) AS n FROM event WHERE event_type='NO_CONSENTED_CONTACT'"
    ).fetchone()["n"]
    check("every rejection logs a NO_CONSENTED_CONTACT event (not a silent drop)", events == 4)

print("\n== 7. A8 — outreach composer")
import outreach as a8

# own db, not the shared db_path — section 3 already filled 20 outreach rows
# into today's date there for the F4 daily-cap test, which would otherwise
# make every check_preconditions() call below see the cap as pre-exhausted.
a8_db = os.path.join(tmpdir, "a8.sqlite3")
store.init_db(a8_db)
with store.connect(a8_db) as conn:
    conn.execute(
        "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
        "VALUES ('A8TestCo', 'razorpay.com', 0, ?, ?)", (now, now),
    )
    a8_company_id = conn.execute("SELECT id FROM company WHERE name='A8TestCo'").fetchone()["id"]
    conn.execute(
        "INSERT INTO authority_node (company_id, person_name, source, owns_req_likelihood, "
        "warm_path_distance, created_at) VALUES (?, 'Good Node', 'user_manual_entry', 0.9, 1, ?)",
        (a8_company_id, now),
    )
    a8_node_id = conn.execute("SELECT id FROM authority_node WHERE person_name='Good Node'").fetchone()["id"]
    good_channel_id = a5.resolve_contact(conn, a8_node_id, "good@razorpay.com", "user_existing_relationship",
                                          None, company_domain="razorpay.com")
    check("test fixture: contact channel resolved for the precondition tests", good_channel_id is not None)

    ok, reason = a8.check_preconditions(conn, a8_company_id, a8_node_id, good_channel_id)
    check("check_preconditions passes for a valid, well-formed setup", ok, f"({reason})")

    conn.execute(
        "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
        "VALUES ('ConflictCo', 'razorpay.com', 1, ?, ?)", (now, now),
    )
    conflict_id = conn.execute("SELECT id FROM company WHERE name='ConflictCo'").fetchone()["id"]
    ok, reason = a8.check_preconditions(conn, conflict_id, a8_node_id, good_channel_id)
    check("conflict-of-interest company is refused, regardless of everything else", not ok)
    mro_events = conn.execute(
        "SELECT COUNT(*) AS n FROM event WHERE event_type='MANUAL_REVIEW_ONLY'"
    ).fetchone()["n"]
    check("conflict-of-interest refusal logs MANUAL_REVIEW_ONLY (not silently dropped)", mro_events == 1)

    conn.execute(
        "INSERT INTO authority_node (company_id, person_name, source, owns_req_likelihood, "
        "warm_path_distance, created_at) VALUES (?, 'Low Conf Node', 'user_manual_entry', 0.9, 1, ?)",
        (a8_company_id, now),
    )
    low_conf_node_id = conn.execute("SELECT id FROM authority_node WHERE person_name='Low Conf Node'").fetchone()["id"]
    conn.execute(
        """INSERT INTO contact_channel (authority_node_id, channel_type, value, consent_basis,
           captured_at, verified, confidence) VALUES (?, 'email', 'lowconf@razorpay.com',
           'job_post_listed_contact', ?, 0, 0.4)""",
        (low_conf_node_id, now),
    )
    low_conf_channel_id = conn.execute(
        "SELECT id FROM contact_channel WHERE value='lowconf@razorpay.com'"
    ).fetchone()["id"]
    ok, reason = a8.check_preconditions(conn, a8_company_id, low_conf_node_id, low_conf_channel_id)
    check("channel confidence below 0.6 is refused", not ok, f"({reason})")

    conn.execute(
        "INSERT INTO authority_node (company_id, person_name, source, owns_req_likelihood, "
        "warm_path_distance, created_at) VALUES (?, 'Weak Owner Node', 'user_manual_entry', 0.3, 3, ?)",
        (a8_company_id, now),
    )
    weak_node_id = conn.execute("SELECT id FROM authority_node WHERE person_name='Weak Owner Node'").fetchone()["id"]
    weak_channel_id = a5.resolve_contact(conn, weak_node_id, "weak@razorpay.com", "user_existing_relationship",
                                          None, company_domain="razorpay.com")
    ok, reason = a8.check_preconditions(conn, a8_company_id, weak_node_id, weak_channel_id, job_id=None)
    check("company-centric (no-req) outreach refuses low owns_req_likelihood + distant warm_path",
          not ok, f"({reason})")
    ok, reason = a8.check_preconditions(conn, a8_company_id, weak_node_id, weak_channel_id, job_id="some-job-id")
    check("the same weak node IS allowed when there's a real job_id (not company-centric cold outreach)",
          ok, f"({reason})")

    ok, reason = a8.validate_composition("A fine short subject", "A short body.", "")
    check("validate_composition refuses an empty specific_fact (§8.3)", not ok)
    ok, reason = a8.validate_composition("This subject has way more than eight words in it easily",
                                          "Body.", "fact")
    check("validate_composition refuses a subject over 8 words", not ok)
    ok, reason = a8.validate_composition("Short subject", " ".join(["word"] * 200), "fact")
    check("validate_composition refuses a body over 150 words", not ok)
    ok, reason = a8.validate_composition("Short subject", "A short, valid body.",
                                          "Company just launched X, per their own newsroom")
    check("validate_composition passes a well-formed, specific draft", ok, f"({reason})")

    result = a8.draft_outreach(conn, a8_company_id, a8_node_id, good_channel_id,
                                "Short subject", "A short, valid body.",
                                "Company just launched X, per their own newsroom",
                                gmail_service=None)
    draft_ok = isinstance(result, dict) and result.get("eml_path") and os.path.exists(result["eml_path"])
    check("draft_outreach succeeds via the .eml fallback (no live Gmail service passed)",
          draft_ok, f"({result})")
    if draft_ok:
        outreach_row = conn.execute("SELECT state FROM outreach WHERE id=?", (result["outreach_id"],)).fetchone()
        check("the outreach row is recorded as DRAFTED", outreach_row["state"] == "DRAFTED")
    else:
        check("the outreach row is recorded as DRAFTED", False, "(skipped, draft_outreach itself failed)")

    os.environ["CI"] = "true"
    ci_raised = False
    try:
        a8.draft_outreach(conn, a8_company_id, a8_node_id, good_channel_id,
                           "x", "y", "z", gmail_service=None)
    except RuntimeError:
        ci_raised = True
    finally:
        del os.environ["CI"]
    check("draft_outreach refuses to run at all when CI env var is set (F7)", ci_raised)

def _write_test_channel(conn, node_id, email, consent_basis="user_existing_relationship", confidence=0.9):
    """Sections 8/9 test A9/outreach_send logic, not A5's live-DNS domain
    gate — writing the channel directly through insert_contact_channel()
    (still F2-gated) instead of a5.resolve_contact() keeps these fixtures
    from being yet more rolls against has_mx_record()'s documented,
    unretried live-DNS flakiness. A5 itself is already covered in section 6."""
    return store.insert_contact_channel(
        conn, node_id, "email", email, consent_basis=consent_basis,
        source_url=None, captured_at=now, confidence=confidence,
    )


print("\n== 8. A9 — CRM & calibration")
import outreach_crm as a9

a9_db = os.path.join(tmpdir, "a9.sqlite3")
store.init_db(a9_db)
with store.connect(a9_db) as conn:
    conn.execute(
        "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
        "VALUES ('A9TestCo', 'razorpay.com', 0, ?, ?)", (now, now),
    )
    a9_company_id = conn.execute("SELECT id FROM company WHERE name='A9TestCo'").fetchone()["id"]
    conn.execute(
        "INSERT INTO authority_node (company_id, person_name, source, seniority_band, "
        "owns_req_likelihood, warm_path_distance, created_at) VALUES "
        "(?, 'CRM Node', 'user_manual_entry', 'function_owner', 0.9, 1, ?)",
        (a9_company_id, now),
    )
    a9_node_id = conn.execute("SELECT id FROM authority_node WHERE person_name='CRM Node'").fetchone()["id"]
    a9_channel_id = _write_test_channel(conn, a9_node_id, "crm@razorpay.com")
    check("test fixture: contact channel written for A9 tests", a9_channel_id is not None)

    outreach_result = a8.draft_outreach(conn, a9_company_id, a9_node_id, a9_channel_id,
                                         "Short subject", "A short, valid body.",
                                         "Company just launched X, per their own newsroom",
                                         gmail_service=None)
    a9_outreach_id = outreach_result["outreach_id"]

    # state machine
    bad_jump_raised = False
    try:
        a9.update_outreach_state(conn, a9_outreach_id, "REPLIED")
    except ValueError:
        bad_jump_raised = True
    check("DRAFTED -> REPLIED (skipping SENT_BY_USER) is refused", bad_jump_raised)

    a9.mark_sent(conn, a9_outreach_id)
    row = conn.execute("SELECT state, user_sent_at FROM outreach WHERE id=?", (a9_outreach_id,)).fetchone()
    check("mark_sent transitions DRAFTED -> SENT_BY_USER and stamps user_sent_at",
          row["state"] == "SENT_BY_USER" and row["user_sent_at"] is not None)

    a9.update_outreach_state(conn, a9_outreach_id, "REPLIED", reason="gmail_reply_detected")
    a9.update_outreach_state(conn, a9_outreach_id, "CLOSED", reason="declined_do_not_contact")
    row = conn.execute("SELECT state, closed_reason FROM outreach WHERE id=?", (a9_outreach_id,)).fetchone()
    check("valid chain SENT_BY_USER -> REPLIED -> CLOSED succeeds and records closed_reason",
          row["state"] == "CLOSED" and row["closed_reason"] == "declined_do_not_contact")
    check("an explicit do-not-contact close auto-suppresses the channel",
          store.is_suppressed(conn, "crm@razorpay.com"))

    terminal_raised = False
    try:
        a9.update_outreach_state(conn, a9_outreach_id, "SENT_BY_USER")
    except ValueError:
        terminal_raised = True
    check("CLOSED is terminal — no further transition is allowed", terminal_raised)

    # a second outreach (a fresh contact — the first channel is now suppressed
    # by the do-not-contact close above), inserted directly rather than via
    # a8.draft_outreach (which would refuse a 2nd draft to the SAME company
    # this soon under F4's 21-day cooldown — irrelevant to what's being
    # tested here, which is A9's suppression behavior in isolation) that
    # closes WITHOUT an explicit do-not-contact reason must NOT be
    # suppressed — a plain rejection or no-response isn't consent withdrawal.
    a9_channel2_id = _write_test_channel(conn, a9_node_id, "crm2@razorpay.com")
    conn.execute(
        "INSERT INTO outreach (company_id, authority_node_id, channel_id, state, created_at) "
        "VALUES (?, ?, ?, 'DRAFTED', ?)",
        (a9_company_id, a9_node_id, a9_channel2_id, now),
    )
    outreach2_id = conn.execute(
        "SELECT id FROM outreach WHERE channel_id=?", (a9_channel2_id,)
    ).fetchone()["id"]
    a9.mark_sent(conn, outreach2_id)
    a9.update_outreach_state(conn, outreach2_id, "REJECTED")
    a9.update_outreach_state(conn, outreach2_id, "CLOSED", reason="role_filled")
    check("a CLOSED with an ordinary reason ('role_filled') does NOT suppress its channel",
          not store.is_suppressed(conn, "crm2@razorpay.com"))
    role_filled_suppressions = conn.execute(
        "SELECT COUNT(*) AS n FROM suppression WHERE reason LIKE '%role_filled%'"
    ).fetchone()["n"]
    check("no suppression row is written for a non-do-not-contact close reason",
          role_filled_suppressions == 0)

    # follow-ups — a 3rd outreach row, same reasoning as outreach2 above for
    # inserting directly rather than through draft_outreach's F4 gate.
    a9_channel3_id = _write_test_channel(conn, a9_node_id, "crm3@razorpay.com")
    conn.execute(
        "INSERT INTO outreach (company_id, authority_node_id, channel_id, state, created_at) "
        "VALUES (?, ?, ?, 'DRAFTED', ?)",
        (a9_company_id, a9_node_id, a9_channel3_id, now),
    )
    o3_id = conn.execute("SELECT id FROM outreach WHERE channel_id=?", (a9_channel3_id,)).fetchone()["id"]
    a9.mark_sent(conn, o3_id)
    due = a9.schedule_followup(conn, o3_id, days_from_now=-1)  # due yesterday, to test due_followups now
    check("schedule_followup returns the due date", due is not None)
    due_list = a9.due_followups(conn)
    check("due_followups surfaces the just-scheduled, overdue follow-up",
          any(d["id"] == o3_id for d in due_list))
    a9.record_followup_sent(conn, o3_id)
    row = conn.execute("SELECT followup_count, next_followup_due FROM outreach WHERE id=?", (o3_id,)).fetchone()
    check("record_followup_sent increments followup_count and clears next_followup_due",
          row["followup_count"] == 1 and row["next_followup_due"] is None)

    for _ in range(ratelimit.MAX_FOLLOWUPS_PER_THREAD - 1):
        a9.schedule_followup(conn, o3_id, days_from_now=-1)
        a9.record_followup_sent(conn, o3_id)
    cap_raised = False
    try:
        a9.schedule_followup(conn, o3_id, days_from_now=-1)
    except ValueError:
        cap_raised = True
    check("schedule_followup refuses once the F4 follow-up cap is reached", cap_raised)

    scheduling_wrong_state_raised = False
    try:
        a9.schedule_followup(conn, a9_outreach_id, days_from_now=1)  # a9_outreach_id is CLOSED
    except ValueError:
        scheduling_wrong_state_raised = True
    check("schedule_followup refuses for a non-SENT_BY_USER outreach", scheduling_wrong_state_raised)

    # calibration refit — insufficient data is the expected real-world state
    refit = a9.refit_owns_req_likelihood(conn)
    check("refit_owns_req_likelihood honestly reports insufficient data below n=20",
          refit["ready"] is False and refit["total"] < 20, f"({refit['note']})")

    # synthetic fixture: manufacture >=20 outcomes across node types to prove
    # the refit math itself (never that it auto-applies anything).
    synth_types = (["function_owner"] * 8 + ["hiring_manager"] * 6
                   + ["ta_lead_function"] * 4 + ["generic_ta"] * 4)
    for i, ntype in enumerate(synth_types):
        conn.execute(
            "INSERT INTO authority_node (company_id, person_name, source, seniority_band, "
            "owns_req_likelihood, warm_path_distance, created_at) VALUES "
            "(?, ?, 'user_manual_entry', ?, 0.5, 1, ?)",
            (a9_company_id, f"Synth {i}", ntype, now),
        )
        synth_node_id = conn.execute("SELECT id FROM authority_node WHERE person_name=?",
                                      (f"Synth {i}",)).fetchone()["id"]
        # function_owner and hiring_manager reply more often in this fixture;
        # ta_lead_function/generic_ta rarely do — deliberately shaped so the
        # refit's direction is checkable.
        replies = i % 2 == 0 if ntype in ("function_owner", "hiring_manager") else i % 4 == 0
        state = "REPLIED" if replies else "REJECTED"
        conn.execute(
            "INSERT INTO outreach (company_id, authority_node_id, channel_id, state, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (a9_company_id, synth_node_id, a9_channel_id, state, now),
        )

    refit2 = a9.refit_owns_req_likelihood(conn)
    check("refit_owns_req_likelihood becomes ready once every node type has >=4 samples",
          refit2["ready"] is True, f"({refit2.get('note')})")
    if refit2["ready"]:
        check("refit never proposes a value outside [0, 1]",
              all(0.0 <= v <= 1.0 for v in refit2["proposed_priors"].values()))
        check("refit never moves a prior by more than the documented absolute cap",
              all(abs(refit2["proposed_priors"][t] - refit2["current_priors"][t])
                  <= a9.MAX_LIKELIHOOD_NUDGE + 1e-9 for t in refit2["current_priors"]))
        check("refit does NOT mutate authority_graph.NODE_TYPE_BASE_LIKELIHOOD (proposal only)",
              a3.NODE_TYPE_BASE_LIKELIHOOD == {"function_owner": 0.85, "hiring_manager": 0.70,
                                                "ta_lead_function": 0.45, "generic_ta": 0.20})

    # detect_sent_via_gmail(): fake service shaped like real Gmail behavior,
    # confirmed live 2026-08-10 — sending a draft (via the Gmail web UI OR
    # drafts().send()) assigns the message a NEW id; only the thread id
    # survives. An earlier version of this function matched on the stored
    # message id, which no longer exists post-send — every real send 404'd
    # and was silently swallowed, leaving the row stuck at DRAFTED forever.
    class _FakeThreadsResource:
        def __init__(self, thread_state):
            self._thread_state = thread_state  # {"messages": [...]}

        def get(self, userId, id, format):
            return self

        def execute(self):
            return self._thread_state

    class _FakeGmailService:
        def __init__(self, thread_state):
            self._threads = _FakeThreadsResource(thread_state)

        def users(self):
            return self

        def threads(self):
            return self._threads

    conn.execute(
        "INSERT INTO outreach (company_id, authority_node_id, channel_id, state, "
        "subject, body, gmail_thread_id, created_at) VALUES "
        "(?, ?, ?, 'DRAFTED', 'Detect-sent test subject', 'body', 'thread-det-1', ?)",
        (a9_company_id, a9_node_id, a9_channel_id, now),
    )
    det_outreach_id = conn.execute(
        "SELECT id FROM outreach WHERE gmail_thread_id = 'thread-det-1'").fetchone()["id"]

    still_draft_service = _FakeGmailService(
        {"messages": [{"id": "msg-draft-1", "labelIds": ["DRAFT"]}]})
    moved_none = a9.detect_sent_via_gmail(still_draft_service, conn)
    row = conn.execute("SELECT state FROM outreach WHERE id=?", (det_outreach_id,)).fetchone()
    check("detect_sent_via_gmail leaves a still-drafted thread alone",
          det_outreach_id not in moved_none and row["state"] == "DRAFTED")

    now_sent_service = _FakeGmailService(
        {"messages": [{"id": "msg-sent-2", "labelIds": ["SENT", "INBOX"]}]})
    moved_sent = a9.detect_sent_via_gmail(now_sent_service, conn)
    row = conn.execute("SELECT state, gmail_message_id FROM outreach WHERE id=?",
                        (det_outreach_id,)).fetchone()
    check("detect_sent_via_gmail matches by thread id, not the stale stored message id",
          det_outreach_id in moved_sent and row["state"] == "SENT_BY_USER")
    check("detect_sent_via_gmail updates gmail_message_id to the real post-send message id",
          row["gmail_message_id"] == "msg-sent-2")

print("\n== 9. Outreach review/send (batch-approve UI backend)")
import outreach_send as a9send


class _FakeSendService:
    """Mimics service.users().drafts().create(...)/.send(...).execute()
    without a real Gmail call — offline, deterministic. Records every send
    call it receives so tests can assert exactly what was sent."""
    def __init__(self):
        self.send_calls = []
        self._next_draft_n = 0
        self._pending_call = None

    def users(self):
        return self

    def drafts(self):
        return self

    def create(self, userId, body):
        self._next_draft_n += 1
        self._pending_call = ("create", self._next_draft_n)
        return self

    def send(self, userId, body):
        self.send_calls.append({"userId": userId, "body": body})
        self._pending_call = ("send",)
        return self

    def execute(self):
        if self._pending_call[0] == "create":
            n = self._pending_call[1]
            return {"id": f"fake-draft-{n}",
                    "message": {"id": f"fake-message-{n}", "threadId": f"fake-thread-{n}"}}
        return {"id": "fake-sent-message-id", "labelIds": ["SENT"]}


send_db = os.path.join(tmpdir, "send.sqlite3")
store.init_db(send_db)
with store.connect(send_db) as conn:
    conn.execute(
        "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
        "VALUES ('SendTestCo', 'razorpay.com', 0, ?, ?)", (now, now),
    )
    send_company_id = conn.execute("SELECT id FROM company WHERE name='SendTestCo'").fetchone()["id"]
    conn.execute(
        "INSERT INTO authority_node (company_id, person_name, source, seniority_band, created_at) "
        "VALUES (?, 'Send Node', 'user_manual_entry', 'function_owner', ?)",
        (send_company_id, now),
    )
    send_node_id = conn.execute("SELECT id FROM authority_node WHERE person_name='Send Node'").fetchone()["id"]
    send_channel_id = _write_test_channel(conn, send_node_id, "sendtest@razorpay.com")

    fake_service = _FakeSendService()
    draft_result = a8.draft_outreach(conn, send_company_id, send_node_id, send_channel_id,
                                      "Short subject", "A short, valid body.",
                                      "Company just launched X, per their own newsroom",
                                      gmail_service=fake_service, job_id="send-test-job")
    send_outreach_id = draft_result["outreach_id"]
    check("test fixture: a draft with a fake Gmail draft id exists to send",
          draft_result.get("draft_gmail_id") is not None)

    pending = a9send.list_pending_review(conn)
    check("list_pending_review surfaces the drafted outreach with full context",
          any(p["id"] == send_outreach_id and p["company_name"] == "SendTestCo"
              and p["to_email"] == "sendtest@razorpay.com" for p in pending))

    unconfirmed_raised = False
    try:
        a9send.send_approved_draft(conn, fake_service, send_outreach_id, confirmed=False)
    except ValueError:
        unconfirmed_raised = True
    check("send_approved_draft refuses without confirmed=True (defense in depth)", unconfirmed_raised)
    check("an unconfirmed call never actually reaches the Gmail service",
          len(fake_service.send_calls) == 0)

    result = a9send.send_approved_draft(conn, fake_service, send_outreach_id, confirmed=True)
    check("send_approved_draft succeeds when explicitly confirmed", result.get("id") == "fake-sent-message-id")
    check("the fake service actually received exactly one send call with the right draft id",
          len(fake_service.send_calls) == 1
          and fake_service.send_calls[0]["body"]["id"] == draft_result["draft_gmail_id"])
    row = conn.execute("SELECT state, user_sent_at FROM outreach WHERE id=?", (send_outreach_id,)).fetchone()
    check("a successful send transitions the outreach row to SENT_BY_USER via A9's state machine",
          row["state"] == "SENT_BY_USER" and row["user_sent_at"] is not None)

    already_sent_raised = False
    try:
        a9send.send_approved_draft(conn, fake_service, send_outreach_id, confirmed=True)
    except ValueError:
        already_sent_raised = True
    check("re-sending an already-SENT_BY_USER outreach is refused (not DRAFTED anymore)",
          already_sent_raised)
    check("no second send call reached the service on the refused re-send",
          len(fake_service.send_calls) == 1)

    # a separate company — send_company_id already has outreach from above,
    # and F4's 21-day per-company cooldown would otherwise refuse this one
    # for a reason unrelated to what's being tested here.
    conn.execute(
        "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
        "VALUES ('EmlTestCo', 'razorpay.com', 0, ?, ?)", (now, now),
    )
    eml_company_id = conn.execute("SELECT id FROM company WHERE name='EmlTestCo'").fetchone()["id"]
    conn.execute(
        "INSERT INTO authority_node (company_id, person_name, source, seniority_band, created_at) "
        "VALUES (?, 'Eml Node', 'user_manual_entry', 'function_owner', ?)",
        (eml_company_id, now),
    )
    eml_node_id = conn.execute("SELECT id FROM authority_node WHERE person_name='Eml Node'").fetchone()["id"]
    eml_channel_id = _write_test_channel(conn, eml_node_id, "emltest@razorpay.com")
    eml_result = a8.draft_outreach(conn, eml_company_id, eml_node_id, eml_channel_id,
                                    "Short subject", "A short, valid body.",
                                    "Company just launched X, per their own newsroom",
                                    gmail_service=None, job_id="eml-test-job")
    eml_outreach_id = eml_result["outreach_id"]
    check("test fixture: an .eml-fallback outreach (no Gmail draft) exists",
          eml_result.get("draft_gmail_id") is None)
    check("list_pending_review excludes .eml-fallback rows (nothing to send via Gmail)",
          not any(p["id"] == eml_outreach_id for p in a9send.list_pending_review(conn)))
    eml_send_raised = False
    try:
        a9send.send_approved_draft(conn, fake_service, eml_outreach_id, confirmed=True)
    except ValueError:
        eml_send_raised = True
    check("send_approved_draft refuses an .eml-fallback outreach (no Gmail draft to send)",
          eml_send_raised)

    os.environ["CI"] = "true"
    ci_send_raised = False
    try:
        a9send.send_approved_draft(conn, fake_service, send_outreach_id, confirmed=True)
    except RuntimeError:
        ci_send_raised = True
    finally:
        del os.environ["CI"]
    check("send_approved_draft refuses to run at all when CI env var is set (F7)", ci_send_raised)

print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
sys.exit(1 if failures else 0)
