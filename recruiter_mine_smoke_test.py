"""Offline checks for recruiter_mine.py. No network, no real IMAP —
imaplib.IMAP4_SSL is monkeypatched with a fake in-memory mailbox for the
end-to-end mine() tests, same spirit as this repo's other fake-double
tests (career_agent_smoke_test.py's _FakeGmailService, etc.).
"""
import email
import json
import os
import sys
import tempfile
from email.message import EmailMessage

import outreach_store as store
import recruiter_mine as rm

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {label} {detail}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {detail}")


def make_msg(from_header, subject, body, msg_id="<1@test>"):
    msg = EmailMessage()
    msg["From"] = from_header
    msg["Subject"] = subject
    msg["Date"] = "Tue, 18 Aug 2026 10:00:00 +0530"
    msg["Message-Id"] = msg_id
    msg.set_content(body)
    return email.message_from_bytes(msg.as_bytes())


print("\n== recruiter_mine.py")

# --- is_recruiter_message classification --------------------------------
qualifies, reason = rm.is_recruiter_message(make_msg(
    "Priya Sharma <priya.sharma@razorpay.com>", "Exploring an opportunity at Razorpay",
    "Hi Mehul, I came across your profile and think there's a great opportunity "
    "for a Product Manager role at Razorpay. Would love to connect."))
check("real recruiter message with intent phrase qualifies", qualifies, f"({reason})")

qualifies, reason = rm.is_recruiter_message(make_msg(
    "LinkedIn Job Alerts <jobs-noreply@linkedin.com>", "5 new jobs match your search",
    "Product Manager at Google\nProduct Manager at Meta\nSee all matches"))
check("automated job-alert digest sender is rejected", not qualifies, f"({reason})")

qualifies, reason = rm.is_recruiter_message(make_msg(
    "Naukri.com Team <alerts@naukri.com>", "Your weekly digest",
    "Here are jobs matching your profile this week."))
check("portal system display name (not a person) is rejected", not qualifies, f"({reason})")

qualifies, reason = rm.is_recruiter_message(make_msg(
    "Priya Sharma <priya.sharma@somenewsletter.com>", "Check out our newsletter",
    "Hi, thought you'd enjoy this week's tech roundup."))
check("person-like sender with NO recruiter-intent phrase is rejected", not qualifies, f"({reason})")

qualifies, reason = rm.is_recruiter_message(make_msg(
    "no-reply@ats-system.com", "Application received",
    "Thank you for applying. We will review your application shortly."))
check("no-reply/automated address is rejected regardless of body", not qualifies, f"({reason})")

# --- _looks_like_person_name ---------------------------------------------
check("'Priya Sharma' looks like a person", rm._looks_like_person_name("Priya Sharma"))
check("'LinkedIn Talent Solutions' does not look like a person",
      not rm._looks_like_person_name("LinkedIn Talent Solutions"))
check("'Team Naukri' does not look like a person",
      not rm._looks_like_person_name("Team Naukri"))
check("empty display name does not look like a person",
      not rm._looks_like_person_name(""))

# --- _company_guess --------------------------------------------------------
check("corporate domain guesses a company name",
      rm._company_guess("razorpay.com") == "Razorpay")
check("generic consumer domain guesses nothing",
      rm._company_guess("gmail.com") is None)
check("portal relay domain guesses nothing",
      rm._company_guess("naukri.com") is None)

# --- _get_or_create_company -------------------------------------------------
tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "rm.sqlite3")
store.init_db(db_path)
now = "2026-08-18T00:00:00"
with store.connect(db_path) as conn:
    id1 = rm._get_or_create_company(conn, "Razorpay", "razorpay.com", now)
    id2 = rm._get_or_create_company(conn, "Razorpay", "razorpay.com", now)
    check("_get_or_create_company returns the same id for a repeat domain", id1 == id2)

    id3 = rm._get_or_create_company(conn, None, None, now)
    id4 = rm._get_or_create_company(conn, None, None, now)
    check("two unknown-company contacts (no domain) share the placeholder row",
          id3 == id4)
    row = conn.execute("SELECT is_conflict_of_interest FROM company WHERE id=?",
                        (id1,)).fetchone()
    check("a newly-created company from recruiter_mine is not marked conflict-of-interest",
          row["is_conflict_of_interest"] == 0)

# --- end-to-end mine() against a fake IMAP mailbox --------------------------
class _FakeIMAP:
    """Mimics just enough of imaplib.IMAP4_SSL for mine()'s call sequence."""
    def __init__(self, messages):
        self._messages = messages  # list of raw bytes

    def login(self, user, password):
        pass

    def select(self, folder, readonly=True):
        pass

    def search(self, charset, criteria):
        ids = [str(i).encode() for i in range(len(self._messages))]
        return "OK", [b" ".join(ids)]

    def fetch(self, mid, spec):
        idx = int(mid)
        return "OK", [(b"1", self._messages[idx])]

    def logout(self):
        pass


fake_messages = [
    EmailMessage(),
]
m1 = EmailMessage()
m1["From"] = "Ananya Rao <ananya.rao@paytm.com>"
m1["Subject"] = "Opportunity at Paytm"
m1["Date"] = "Tue, 18 Aug 2026 10:00:00 +0530"
m1.set_content("Hi Mehul, we have an opening for a Senior PM role and would "
               "love to connect regarding this opportunity.")

m2 = EmailMessage()  # should be rejected — automated digest
m2["From"] = "LinkedIn Jobs <jobs-noreply@linkedin.com>"
m2["Subject"] = "10 new jobs for you"
m2["Date"] = "Tue, 18 Aug 2026 09:00:00 +0530"
m2.set_content("New jobs matching your profile this week.")

m3 = EmailMessage()  # duplicate contact, same address as m1 — should be deduped
m3["From"] = "Ananya Rao <ananya.rao@paytm.com>"
m3["Subject"] = "Following up on the opportunity"
m3["Date"] = "Wed, 19 Aug 2026 10:00:00 +0530"
m3.set_content("Just following up — still interested in connecting regarding this role?")

fake_raw = [m.as_bytes() for m in (m1, m2, m3)]

mine_db = os.path.join(tmpdir, "mine.sqlite3")
store.init_db(mine_db)

orig_imap_ssl = rm.imaplib.IMAP4_SSL
rm.imaplib.IMAP4_SSL = lambda host: _FakeIMAP(fake_raw)
os.environ["JOB_ALERT_EMAIL"] = "fake@gmail.com"
os.environ["JOB_ALERT_APP_PASSWORD"] = "fake-app-password"

orig_open = open
def _fake_open(path, *a, **kw):
    if str(path).endswith("config.yaml"):
        import io
        return io.StringIO("recruiter_mine:\n  enabled: true\n")
    return orig_open(path, *a, **kw)

import builtins
builtins.open = _fake_open
try:
    with store.connect(mine_db) as conn:
        new_ids = rm.mine(conn, log=lambda *a, **k: None, now=now)
        check("mine() writes exactly 1 new contact (m2 rejected, m3 deduped against m1)",
              len(new_ids) == 1, f"(got {len(new_ids)})")
        row = conn.execute(
            "SELECT * FROM contact_channel WHERE value='ananya.rao@paytm.com'").fetchone()
        check("the written contact has consent_basis=inbound_recruiter",
              row is not None and row["consent_basis"] == "inbound_recruiter")
        check("the written contact is marked verified (a real inbound message, not inferred)",
              row["verified"] == 1)
        node = conn.execute("SELECT * FROM authority_node WHERE id=?",
                             (row["authority_node_id"],)).fetchone()
        check("the authority_node source is inbound_email", node["source"] == "inbound_email")
        events = conn.execute(
            "SELECT * FROM event WHERE entity_id=? AND event_type='INBOUND_RECRUITER_MESSAGE'",
            (node["id"],)).fetchall()
        check("exactly one INBOUND_RECRUITER_MESSAGE event logged (not one per duplicate mail)",
              len(events) == 1)
        payload = json.loads(events[0]["payload_json"])
        check("the event payload captures the original subject for later reply context",
              "Opportunity at Paytm" in payload["subject"])

        # second run against the same mailbox should find 0 new (already mined)
        new_ids_2 = rm.mine(conn, log=lambda *a, **k: None, now=now)
        check("a second run over the same mailbox finds 0 new contacts (dedup persists)",
              len(new_ids_2) == 0)
finally:
    builtins.open = orig_open
    rm.imaplib.IMAP4_SSL = orig_imap_ssl
    del os.environ["JOB_ALERT_EMAIL"]
    del os.environ["JOB_ALERT_APP_PASSWORD"]

# --- disabled / unconfigured paths return [] cleanly ------------------------
disabled_db = os.path.join(tmpdir, "disabled.sqlite3")
store.init_db(disabled_db)
with store.connect(disabled_db) as conn:
    result = rm.mine(conn, log=lambda *a, **k: None)
    check("mine() returns [] when disabled in config (default state)", result == [])

os.environ.pop("JOB_ALERT_EMAIL", None)
os.environ.pop("JOB_ALERT_APP_PASSWORD", None)
builtins.open = _fake_open
try:
    with store.connect(disabled_db) as conn:
        result = rm.mine(conn, log=lambda *a, **k: None)
        check("mine() returns [] when enabled but credentials are unset", result == [])
finally:
    builtins.open = orig_open

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
