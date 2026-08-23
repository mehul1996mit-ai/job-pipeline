"""Offline checks for careers_inbox.py. Mocks contact_resolution.has_mx_record
(real DNS otherwise) — same discipline as contact_resolution's own smoke
coverage for the MX-dependent paths."""
import os
import sys
import tempfile

import careers_inbox as ci
import contact_resolution as a5
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


print("\n== careers_inbox.py")

tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "ci.sqlite3")
store.init_db(db_path)
now = "2026-08-18T00:00:00"

orig_mx = a5.has_mx_record

# --- careers@ succeeds first try --------------------------------------------
with store.connect(db_path) as conn:
    conn.execute(
        "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
        "VALUES ('Razorpay', 'razorpay.com', 0, ?, ?)", (now, now))
    company_id = conn.execute("SELECT id FROM company WHERE name='Razorpay'").fetchone()["id"]

    a5.has_mx_record = lambda domain: domain == "razorpay.com"
    channel_id = ci.derive_for_company(conn, company_id, "razorpay.com", now)
    check("careers@ succeeds when the domain has an MX record", channel_id is not None)
    row = conn.execute("SELECT value, consent_basis FROM contact_channel WHERE id=?",
                        (channel_id,)).fetchone()
    check("the written address is careers@<domain> (first in priority order)",
          row["value"] == "careers@razorpay.com")
    check("consent_basis is careers_page_published",
          row["consent_basis"] == "careers_page_published")

    node = conn.execute(
        "SELECT * FROM authority_node WHERE company_id=? AND source='derived_role_inbox'",
        (company_id,)).fetchone()
    check("a generic_ta authority_node was created for this company",
          node is not None and node["node_type"] == "generic_ta")

    # re-deriving for the same company reuses the SAME node, doesn't duplicate it
    node_id_2 = ci._get_or_create_generic_node(conn, company_id, now)
    check("a second call reuses the existing generic-inbox node (no duplicate)",
          node_id_2 == node["id"])

# --- careers@ fails MX, falls through to jobs@ then hr@ ---------------------
with store.connect(db_path) as conn:
    conn.execute(
        "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
        "VALUES ('FallthroughCo', 'fallthrough.com', 0, ?, ?)", (now, now))
    company_id2 = conn.execute("SELECT id FROM company WHERE name='FallthroughCo'").fetchone()["id"]

    def mx_only_for_hr(domain):
        return domain == "fallthrough.com"  # domain itself has MX, all locals would pass —
                                              # simulate per-local rejection differently below

    # resolve_contact's MX check is domain-level, not local-part-level, so to
    # actually test fallthrough we reject the domain for the first N calls.
    call_state = {"n": 0}

    def mx_reject_first_two(domain):
        call_state["n"] += 1
        return call_state["n"] > 2  # careers@, jobs@ "fail" (domain looks MX-less those
                                     # two calls), hr@ succeeds on the 3rd

    a5.has_mx_record = mx_reject_first_two
    channel_id2 = ci.derive_for_company(conn, company_id2, "fallthrough.com", now)
    check("falls through careers@ and jobs@ to succeed on hr@",
          channel_id2 is not None)
    row2 = conn.execute("SELECT value FROM contact_channel WHERE id=?", (channel_id2,)).fetchone()
    check("the address that succeeded is hr@ (third in priority order)",
          row2["value"] == "hr@fallthrough.com", f"(got {row2['value']})")

# --- all three fail -> None, no channel written -----------------------------
with store.connect(db_path) as conn:
    conn.execute(
        "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
        "VALUES ('NoMxCo', 'nomx.com', 0, ?, ?)", (now, now))
    company_id3 = conn.execute("SELECT id FROM company WHERE name='NoMxCo'").fetchone()["id"]

    a5.has_mx_record = lambda domain: False
    channel_id3 = ci.derive_for_company(conn, company_id3, "nomx.com", now)
    check("returns None when no candidate local has a valid MX record",
          channel_id3 is None)
    n_channels = conn.execute(
        "SELECT COUNT(*) c FROM contact_channel cc JOIN authority_node an "
        "ON an.id=cc.authority_node_id WHERE an.company_id=?", (company_id3,)).fetchone()["c"]
    check("no contact_channel row was written for the all-fail case", n_channels == 0)

a5.has_mx_record = orig_mx

# --- backfill_careers_inboxes: skips companies without a domain, skips
#     companies that already have a derived_role_inbox node ----------------
with store.connect(db_path) as conn:
    conn.execute(
        "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
        "VALUES ('NoDomainCo', NULL, 0, ?, ?)", (now, now))

    a5.has_mx_record = lambda domain: True
    stats = ci.backfill_careers_inboxes(conn, log=lambda *a, **k: None, now=now)
    # Razorpay already has a node from the earlier test -> skipped this run.
    # FallthroughCo and NoMxCo already have nodes too -> skipped.
    # NoDomainCo has no domain -> excluded by the WHERE clause.
    check("backfill_careers_inboxes finds 0 companies left to process "
          "(all either already-derived or domain-less)",
          stats["filled"] == 0 and stats["no_valid_local"] == 0, f"({stats})")

a5.has_mx_record = orig_mx

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
