"""Offline checks for company_domains.py. DNS resolution and HTTP fetches
are monkeypatched — this module makes real network calls in normal
operation, but the test suite stays offline per this repo's own I6
discipline (see interview_research_smoke_test.py for the same pattern)."""
import json
import os
import sys
import tempfile

import company_domains as cd
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


print("\n== company_domains.py")

# --- slugify_company_name ---------------------------------------------------
check("strips 'Private Limited' suffix",
      cd.slugify_company_name("Razorpay Software Private Limited") == "razorpaysoftware")
check("strips 'Technologies India'",
      cd.slugify_company_name("Setu Technologies India") == "setu")
check("lowercases and strips spaces/punctuation",
      cd.slugify_company_name("Pine Labs Pvt. Ltd.") == "pinelabs")

# --- _free_tier_guess: only accepts a domain that BOTH resolves and passes
#     the homepage check; tries .com before .in -----------------------------
orig_resolves = cd._domain_resolves
orig_mentions = cd._homepage_mentions_company

cd._domain_resolves = lambda domain: domain in ("razorpay.com",)
cd._homepage_mentions_company = lambda domain, name, log=print: domain == "razorpay.com"
result = cd._free_tier_guess("Razorpay Private Limited", log=lambda *a, **k: None)
check("free-tier guess resolves to the correct verified .com domain",
      result == "razorpay.com", f"(got {result})")

cd._domain_resolves = lambda domain: True  # resolves but...
cd._homepage_mentions_company = lambda domain, name, log=print: False  # ...never verifies
result = cd._free_tier_guess("Some Random Company", log=lambda *a, **k: None)
check("a domain that resolves but fails the homepage check is rejected, not guessed",
      result is None)

cd._domain_resolves = lambda domain: False
result = cd._free_tier_guess("Nonexistent Corp", log=lambda *a, **k: None)
check("a domain that doesn't resolve at all returns None", result is None)

cd._domain_resolves = orig_resolves
cd._homepage_mentions_company = orig_mentions

# --- backfill_company_domains: only fills verified rows, leaves the rest ---
tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "domains.sqlite3")
store.init_db(db_path)
now = "2026-08-18T00:00:00"
with store.connect(db_path) as conn:
    for name in ("Razorpay", "Ghost Company XYZ", "Paytm"):
        conn.execute(
            "INSERT INTO company (name, is_conflict_of_interest, created_at, updated_at) "
            "VALUES (?, 0, ?, ?)", (name, now, now),
        )

    def fake_free_tier(name, log=print):
        return {"Razorpay": "razorpay.com", "Paytm": "paytm.com"}.get(name)

    orig_free_tier = cd._free_tier_guess
    cd._free_tier_guess = fake_free_tier
    try:
        stats = cd.backfill_company_domains(conn, use_serpapi=False, log=lambda *a, **k: None)
        check("backfill fills the two companies with a real free-tier match",
              stats["filled"] == 2, f"({stats})")
        check("backfill leaves the unresolvable company as unresolved, not guessed",
              stats["unresolved"] == 1, f"({stats})")
        row = conn.execute("SELECT domain FROM company WHERE name='Razorpay'").fetchone()
        check("Razorpay's domain is written correctly", row["domain"] == "razorpay.com")
        row = conn.execute("SELECT domain FROM company WHERE name='Ghost Company XYZ'").fetchone()
        check("Ghost Company XYZ's domain stays NULL rather than a bad guess",
              row["domain"] is None)
    finally:
        cd._free_tier_guess = orig_free_tier

# --- SerpApi fallback: only called when free tier fails, respects the cap --
usage_path = cd.SERPAPI_USAGE_FILE
usage_backup = None
if os.path.exists(usage_path):
    with open(usage_path, encoding="utf-8") as f:
        usage_backup = f.read()

os.environ["SERPAPI_KEY"] = "fake-key"
call_count = {"n": 0}


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def fake_requests_get(url, params=None, timeout=None, **kw):
    call_count["n"] += 1
    if "serpapi.com" in url:
        return _FakeResp({"organic_results": [{"link": "https://verified-domain.com/about"}]})
    raise AssertionError("unexpected request")


orig_requests_get = cd.requests.get
orig_mentions_2 = cd._homepage_mentions_company
cd.requests.get = fake_requests_get
cd._homepage_mentions_company = lambda domain, name, log=print: domain == "verified-domain.com"

try:
    with store.connect(db_path) as conn2:
        cd._save_usage({"month": cd._current_month(), "count": 0,
                         "company_research_count": 0, "domain_backfill_count": 0})
        cd._free_tier_guess = lambda name, log=print: None  # force fallback
        result = cd._serpapi_domain_lookup("Some New Company", log=lambda *a, **k: None)
        check("SerpApi fallback returns a verified domain from the top organic result",
              result == "verified-domain.com")
        check("exactly one real HTTP call was made (to SerpApi)", call_count["n"] == 1)

        usage = cd._load_usage()
        check("domain_backfill_count increments after a real SerpApi call",
              usage["domain_backfill_count"] == 1)

        cd._save_usage({"month": cd._current_month(), "count": 0,
                         "company_research_count": 0,
                         "domain_backfill_count": cd.DOMAIN_BACKFILL_MONTHLY_CAP})
        call_count["n"] = 0
        result = cd._serpapi_domain_lookup("Another Company", log=lambda *a, **k: None)
        check("SerpApi fallback refuses once the monthly cap is reached",
              result is None and call_count["n"] == 0)
finally:
    cd.requests.get = orig_requests_get
    cd._homepage_mentions_company = orig_mentions_2
    del os.environ["SERPAPI_KEY"]
    if usage_backup is not None:
        with open(usage_path, "w", encoding="utf-8") as f:
            f.write(usage_backup)
    elif os.path.exists(usage_path):
        os.remove(usage_path)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
