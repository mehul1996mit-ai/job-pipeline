"""Offline checks for outreach_shortlist.py. Writes a scratch queue CSV +
seen_jobs.json into a temp dir and monkeypatches QUEUE_DIR/SEEN_JOBS_PATH
so this never touches the real data/ directory."""
import csv
import json
import os
import sys
import tempfile

import contact_resolution as a5
import outreach_shortlist as osl
import outreach_store as store

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {label} {detail}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {detail}")


print("\n== outreach_shortlist.py")

tmpdir = tempfile.mkdtemp()
orig_queue_dir = osl.QUEUE_DIR
orig_seen_path = osl.SEEN_JOBS_PATH
osl.QUEUE_DIR = tmpdir
osl.SEEN_JOBS_PATH = os.path.join(tmpdir, "seen_jobs.json")

date_str = "2026-08-18"
queue_path = os.path.join(tmpdir, f"job_queue_{date_str}.csv")
with open(queue_path, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["company", "title", "url", "score"])
    w.writeheader()
    w.writerow({"company": "Razorpay", "title": "Senior Product Manager",
                "url": "https://razorpay.com/jobs/1", "score": "72"})
    w.writerow({"company": "FreshCo", "title": "Product Manager - Growth",
                "url": "https://freshco.com/jobs/2", "score": "65"})
    w.writerow({"company": "NoContactCo", "title": "Associate PM",
                "url": "https://nocontact.com/jobs/3", "score": "55"})

with open(osl.SEEN_JOBS_PATH, "w", encoding="utf-8") as f:
    json.dump({
        "h1": {"first_seen": "2026-08-16", "url": "https://razorpay.com/jobs/1"},
        "h2": {"first_seen": "2026-08-18", "url": "https://freshco.com/jobs/2"},
    }, f)

# --- load_queue_for_day / days_since ----------------------------------------
rows = osl.load_queue_for_day(date_str)
check("load_queue_for_day reads the 3 scratch rows", len(rows) == 3)
check("load_queue_for_day returns [] for a day with no queue file",
      osl.load_queue_for_day("2099-01-01") == [])
check("days_since computes correctly against a fixed 'today'",
      osl.days_since("2026-08-16", today=__import__("datetime").date(2026, 8, 18)) == 2)
check("days_since returns None for a missing date", osl.days_since(None) is None)

# --- fixture DB: Razorpay has an inbound_recruiter contact, FreshCo has a
#     derived_role_inbox contact, NoContactCo has none -----------------------
db_path = os.path.join(tmpdir, "shortlist.sqlite3")
store.init_db(db_path)
now = "2026-08-18T00:00:00"
orig_mx = a5.has_mx_record
a5.has_mx_record = lambda domain: True

with store.connect(db_path) as conn:
    for name, domain in (("Razorpay", "razorpay.com"), ("FreshCo", "freshco.com"),
                          ("NoContactCo", "nocontact.com")):
        conn.execute(
            "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?)", (name, domain, now, now))
    razorpay_id = conn.execute("SELECT id FROM company WHERE name='Razorpay'").fetchone()["id"]
    freshco_id = conn.execute("SELECT id FROM company WHERE name='FreshCo'").fetchone()["id"]

    rec_node = store.insert_authority_node(
        conn, razorpay_id, "Priya Sharma", source="inbound_email", created_at=now,
        node_type="ta_lead_function")
    store.insert_contact_channel(
        conn, rec_node, "email", "priya.sharma@razorpay.com",
        consent_basis="inbound_recruiter", source_url=None, captured_at=now,
        confidence=0.9, verified=True)

    import careers_inbox as ci
    ci.derive_for_company(conn, freshco_id, "freshco.com", now)

    # FreshCo also has a named person discovered separately (e.g. by
    # authority_discovery.py) — the careers@ draft should be personalized
    # to them, even though the SEND target stays the careers@ inbox.
    store.insert_authority_node(
        conn, freshco_id, "Ananya Rao", source="press_release", created_at=now,
        title="VP Product", function="product", node_type="function_owner")

    # --- best_named_reference_for_company ---
    named = osl.best_named_reference_for_company(conn, freshco_id)
    check("FreshCo's best named reference is the discovered VP Product",
          named is not None and named["person_name"] == "Ananya Rao")
    named_razorpay = osl.best_named_reference_for_company(conn, razorpay_id)
    check("Razorpay's named reference is Priya Sharma (the recruiter node itself — "
          "she's a real named person, source != derived_role_inbox)",
          named_razorpay is not None and named_razorpay["person_name"] == "Priya Sharma")
    c99 = osl.best_named_reference_for_company(conn, 99999)
    check("a company with no named nodes returns None", c99 is None)

    # --- best_contact_for_company ---
    c = osl.best_contact_for_company(conn, razorpay_id)
    check("Razorpay's best contact is the inbound recruiter",
          c is not None and c["tier"] == "inbound_recruiter"
          and c["to_email"] == "priya.sharma@razorpay.com")
    c2 = osl.best_contact_for_company(conn, freshco_id)
    check("FreshCo's best contact is the derived careers@ inbox",
          c2 is not None and c2["tier"] == "derived_role_inbox")
    c3 = osl.best_contact_for_company(conn, 99999)
    check("a company with no contacts returns None", c3 is None)

    # --- build_shortlist_for_day: ranking ---
    shortlist = osl.build_shortlist_for_day(conn, date_str, now=now)
    check("shortlist has all 3 queue rows", len(shortlist) == 3)
    check("Razorpay (recruiter tier) ranks first",
          shortlist[0]["company_name"] == "Razorpay")
    check("FreshCo (careers@ tier) ranks second",
          shortlist[1]["company_name"] == "FreshCo")
    check("NoContactCo (no contact) ranks last",
          shortlist[2]["company_name"] == "NoContactCo" and shortlist[2]["contact"] is None)
    check("Razorpay's freshness is correctly computed from seen_jobs.json",
          shortlist[0]["freshness_days"] == 2)
    check("none of the 3 rows show as already-outreached yet",
          not any(r["already_outreached"] for r in shortlist))

    # --- generate_drafts_for_day: eml fallback (service=None), max_new=1 ---
    stats = osl.generate_drafts_for_day(conn, None, date_str, max_new=1, log=lambda *a, **k: None)
    check("generate_drafts_for_day drafts exactly 1 (max_new cap respected)",
          stats["drafted"] == 1, f"({stats})")
    check("the one drafted row is Razorpay (highest-ranked)",
          conn.execute(
              "SELECT company_id FROM outreach ORDER BY id DESC LIMIT 1"
          ).fetchone()["company_id"] == razorpay_id)

    # --- second call: Razorpay already outreached, FreshCo drafts next,
    #     NoContactCo has no contact ---
    stats2 = osl.generate_drafts_for_day(conn, None, date_str, log=lambda *a, **k: None)
    check("second run drafts FreshCo (the only remaining eligible row)",
          stats2["drafted"] == 1, f"({stats2})")
    check("second run skips Razorpay as already-outreached",
          stats2["skipped_already_sent"] == 1, f"({stats2})")
    check("second run skips NoContactCo for having no contact",
          stats2["skipped_no_contact"] == 1, f"({stats2})")

    # --- subject/body honesty check ---
    row = conn.execute("SELECT subject, body FROM outreach WHERE company_id=?",
                        (razorpay_id,)).fetchone()
    check("subject stays within the 8-word ceiling",
          len(row["subject"].split()) <= osl.SUBJECT_WORD_CEILING)
    check("body references the real posting title (freshness-based specific_fact)",
          "Senior Product Manager" in row["body"])

    # --- FreshCo's draft (careers@ tier) should be personalized to the
    #     named reference, even though the send target is still careers@ ---
    fresh_row2 = conn.execute(
        """SELECT outreach.body, contact_channel.value AS to_email FROM outreach
           JOIN contact_channel ON contact_channel.id = outreach.channel_id
           WHERE outreach.company_id = ?""", (freshco_id,)).fetchone()
    check("FreshCo's draft sends to careers@freshco.com (the verified inbox, not a personal guess)",
          fresh_row2["to_email"] == "careers@freshco.com")
    check("FreshCo's draft body opens with 'Attn: Ananya Rao', not a generic 'Hi,'",
          fresh_row2["body"].startswith("Attn: Ananya Rao"))

a5.has_mx_record = orig_mx
osl.QUEUE_DIR = orig_queue_dir
osl.SEEN_JOBS_PATH = orig_seen_path

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
