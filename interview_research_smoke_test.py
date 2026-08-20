"""Offline smoke test for interview_research.py (company research +
comparison). No API keys, no network — grounded calls are exercised only
via a fake call_fn, same discipline as interview_smoke_test.py.

Run:  python interview_research_smoke_test.py
"""
import json
import os
import re
import tempfile

PASS, FAIL = "  [PASS]", "  [FAIL]"
failures = 0


def check(name, condition, detail=""):
    global failures
    print(f"{PASS if condition else FAIL} {name} {detail}")
    if not condition:
        failures += 1


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
os.environ["INTERVIEW_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "interview_research_test.sqlite3")

import interview_store
import interview_prep
import interview_research as research

with open(os.path.join(REPO_ROOT, "resume_master.json"), encoding="utf-8") as f:
    MASTER_RESUME = json.load(f)

interview_store.init_db()

print("== Industry classification (§7) — deterministic, no LLM")
check("bank JD text classifies as banking",
      research.classify_industry("ICICI Bank", "digital banking product manager")["industry"]
      == "banking_nbfc_housing_finance")
check("housing finance beats generic 'bank' keyword when both present",
      research.classify_industry("HomeFirst", "we are a housing finance company")["industry"]
      == "banking_nbfc_housing_finance")
check("saas JD classifies as saas",
      research.classify_industry("Acme", "B2B SaaS platform, ARR growth")["industry"] == "saas")
unclassified = research.classify_industry("Mystery Corp", "we do things")
check("unclassifiable input falls back to general + inference basis",
      unclassified["industry"] == "general" and unclassified["basis"] == "inference")
classified = research.classify_industry("X", "we are a bank")
check("classified industry is labeled fact basis, not inference",
      classified["basis"] == "fact")

print("== company/company_fact tables (§7, §46)")
JD_TEXT = "Digital lending product manager. NBFC experience preferred."
pid = interview_prep.process_new_jd("TargetBank", "Senior PM", JD_TEXT, "pasted", MASTER_RESUME)["process_id"]
with interview_store.connect() as conn:
    target_id = research.get_or_create_company(conn, pid, "target", "TargetBank", JD_TEXT)
    current_id = research.get_or_create_company(conn, pid, "current", "CurrentCo", "")
    same_again = research.get_or_create_company(conn, pid, "target", "TargetBank", JD_TEXT)
check("get_or_create_company is idempotent per (process, role)", target_id == same_again)

print("== 'current' company is shared across processes, 'target' stays isolated (§45)")
pid2 = interview_prep.process_new_jd(
    "OtherTargetBank", "PM", "Some other JD text", "pasted", MASTER_RESUME)["process_id"]
with interview_store.connect() as conn:
    conn.execute(
        "INSERT INTO company_fact (company_id, category, label, value, period, fact_type, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (current_id, "corporate", "CEO", "Jane Smith", None, "fact", "2026-01-01"))
    # A second, distinct process asking about the SAME current-company name
    # must resolve to the SAME company row -- not create a fresh, fact-less
    # duplicate. This is the actual point of the fix: research on the
    # candidate's current employer, done once, is visible from every future
    # interview process without re-researching it.
    current_id_from_other_process = research.get_or_create_company(
        conn, pid2, "current", "CurrentCo", "")
    facts_visible_from_other_process = research.get_company_facts(conn, current_id_from_other_process)
check("second process reuses the SAME current-company row",
      current_id_from_other_process == current_id)
check("research done via the first process is visible from the second process",
      "CEO" in facts_visible_from_other_process)
with interview_store.connect() as conn:
    # 'target' must NOT be shared even with an identical company_name --
    # two processes targeting the same real company each get their own row.
    target_id_2 = research.get_or_create_company(conn, pid2, "target", "TargetBank", JD_TEXT)
check("'target' with the same name in a different process gets its OWN row",
      target_id_2 != target_id)

with interview_store.connect() as conn:
    row = conn.execute("SELECT industry FROM company WHERE id=?", (target_id,)).fetchone()
check("target company industry auto-classified from JD text",
      row["industry"] == "banking_nbfc_housing_finance", detail=f"got {row['industry']}")

print("== research_company via fake search_fn + call_fn (never hits network)")


def fake_search(query):
    return "- Some Article: TargetBank was founded in 2010, CEO Jane Doe."


def fake_research_call(prompt):
    return json.dumps({
        "corporate": {"ceo": "Jane Doe", "founded": "2010"},
        "business": {"business_model": "digital lending"},
        "strategy": {"recent_priorities": ["expand to tier-2 cities"]},
    })


with interview_store.connect() as conn:
    facts = research.research_company(conn, target_id, "TargetBank",
                                      call_fn=fake_research_call, search_fn=fake_search)
check("research_company returns facts from the fake double", len(facts) >= 3, detail=f"{len(facts)} facts")
with interview_store.connect() as conn:
    stored = conn.execute("SELECT COUNT(*) AS n FROM company_fact WHERE company_id=?",
                          (target_id,)).fetchone()["n"]
check("facts persisted to company_fact", stored == len(facts))
check("facts sourced from search snippets are labeled 'estimate', not 'fact' "
      "(a search snippet is not a primary source)",
      all(f["fact_type"] == "estimate" for f in facts))

print("== ResearchUnavailable — never silently falls back to an unsearched guess")


def failing_search(query):
    raise research.ResearchUnavailable("no results")


raised = False
try:
    with interview_store.connect() as conn:
        research.research_company(conn, target_id, "TargetBank", search_fn=failing_search)
except research.ResearchUnavailable:
    raised = True
check("a failing search step raises ResearchUnavailable, not a silent empty result", raised)

print("== collect_financial_metrics (§8/9) — industry-specific metric list enforced")


def fake_metrics_call(prompt):
    # Deliberately includes one metric NOT in the banking list, to verify
    # it gets filtered out rather than trusted blindly.
    return json.dumps([
        {"metric": "AUM", "value": "50000 Cr", "period": "FY2025", "fact_type": "fact"},
        {"metric": "GNPA", "value": "1.8%", "period": "FY2025", "fact_type": "fact"},
        {"metric": "ARR", "value": "999", "period": "FY2025", "fact_type": "estimate"},
    ])


with interview_store.connect() as conn:
    metrics = research.collect_financial_metrics(
        conn, target_id, "banking_nbfc_housing_finance", "TargetBank",
        call_fn=fake_metrics_call, search_fn=fake_search)
check("only metrics from the requested industry library are accepted",
      {m["label"] for m in metrics} == {"AUM", "GNPA"},
      detail=f"got {[m['label'] for m in metrics]}")

print("== SerpApi quota tracking (§ company research cap, shared account quota)")

_orig_usage_file = research.SERPAPI_USAGE_FILE
_scratch_usage_file = os.path.join(tempfile.mkdtemp(), "serpapi_usage_test.json")
research.SERPAPI_USAGE_FILE = _scratch_usage_file
try:
    usage = research._check_and_reserve_serpapi_call()
    check("a fresh month starts with zero company_research_count",
          usage["company_research_count"] == 0)
    research._record_serpapi_call(usage)
    reloaded = research._load_serpapi_usage()
    check("recording a call persists company_research_count",
          reloaded["company_research_count"] == 1)

    # Simulate hitting the company-research sub-cap.
    research._save_serpapi_usage({"month": research._current_month(), "count": 0,
                                  "company_research_count": research.COMPANY_RESEARCH_MONTHLY_CAP})
    cap_raised = False
    try:
        research._check_and_reserve_serpapi_call()
    except research.ResearchUnavailable:
        cap_raised = True
    check("company-research sub-cap refuses further calls once reached", cap_raised)

    # Simulate job search having already used most of the shared account quota.
    research._save_serpapi_usage({"month": research._current_month(), "count": 235,
                                  "company_research_count": 0})
    shared_raised = False
    try:
        research._check_and_reserve_serpapi_call(monthly_quota=250, quota_buffer=20)
    except research.ResearchUnavailable:
        shared_raised = True
    check("shared account quota (job search + company research combined) is respected, "
          "even when the company-research sub-cap alone has room", shared_raised)
finally:
    research.SERPAPI_USAGE_FILE = _orig_usage_file

print("== compare_companies (§12/13) — pure reduction, no LLM call")
with interview_store.connect() as conn:
    # current company gets its own AUM + a metric target doesn't have
    conn.execute(
        "INSERT INTO company_fact (company_id, category, label, value, period, fact_type, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (current_id, "financial", "AUM", "30000 Cr", "FY2025", "fact", "2026-01-01"))
    conn.execute(
        "UPDATE company SET industry='banking_nbfc_housing_finance' WHERE id=?", (current_id,))
    comparison = research.compare_companies(conn, pid)
check("comparison includes shared metric AUM", any(c["dimension"] == "AUM" for c in comparison))
check("comparison excludes GNPA (only target has it)",
      not any(c["dimension"] == "GNPA" for c in comparison))
aum_row = next(c for c in comparison if c["dimension"] == "AUM")
check("same-industry comparison is labeled 'comparable'", aum_row["comparability"] == "comparable")
check("interpretation mentions both values", "30000 Cr" in aum_row["interpretation"]
      and "50000 Cr" in aum_row["interpretation"])

print("== compare_companies — cross-industry never forced (§12)")
with interview_store.connect() as conn:
    conn.execute("UPDATE company SET industry='saas' WHERE id=?", (current_id,))
    cross_comparison = research.compare_companies(conn, pid)
aum_cross = next(c for c in cross_comparison if c["dimension"] == "AUM")
check("cross-industry metric outside general library is 'not_comparable'",
      aum_cross["comparability"] == "not_comparable")
with interview_store.connect() as conn:
    stored_comparisons = conn.execute(
        "SELECT COUNT(*) AS n FROM company_comparison WHERE process_id=?", (pid,)).fetchone()["n"]
check("re-running compare_companies replaces rows instead of accumulating duplicates "
      "(found live: clicking Compare more than once duplicated every row)",
      stored_comparisons == len(cross_comparison),
      detail=f"{stored_comparisons} stored vs {len(cross_comparison)} returned")

print("== compare_roles (§22) — deterministic, CV + JD only, no research needed")
with interview_store.connect() as conn:
    roles = research.compare_roles(conn, pid, MASTER_RESUME,
                                   "We need strong stakeholder management and lending domain expertise.")
check("role comparison produces at least one dimension", len(roles) > 0)
check("every row has a status of transferable or gap",
      all(r["status"] in ("transferable", "gap") for r in roles))
with interview_store.connect() as conn:
    stored_roles = conn.execute(
        "SELECT COUNT(*) AS n FROM role_comparison WHERE process_id=?", (pid,)).fetchone()["n"]
check("role comparisons persisted", stored_roles == len(roles))

with interview_store.connect() as conn:
    # Re-run with the SAME inputs -- must replace, not accumulate.
    roles_again = research.compare_roles(conn, pid, MASTER_RESUME,
                                         "We need strong stakeholder management and lending domain expertise.")
    stored_roles_again = conn.execute(
        "SELECT COUNT(*) AS n FROM role_comparison WHERE process_id=?", (pid,)).fetchone()["n"]
check("re-running compare_roles replaces rows instead of accumulating duplicates "
      "(found live: clicking Compare more than once tripled every row)",
      stored_roles_again == len(roles_again) == len(roles),
      detail=f"{stored_roles_again} stored vs {len(roles_again)} returned (first run had {len(roles)})")

print("== I1-equivalent: no send-scope / send() call anywhere in this file")
SEND_CALL_RE = re.compile(r"(drafts\(\)|messages\(\))\.send\(")
with open(__file__, encoding="utf-8") as f:
    check("no Gmail send call site", not SEND_CALL_RE.search(f.read()))

print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
raise SystemExit(1 if failures else 0)
