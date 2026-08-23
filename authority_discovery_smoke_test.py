"""Offline checks for authority_discovery.py. Network (_search) is
monkeypatched. The extraction tests below use REAL captured SerpApi output
(a live 'Paytm' search from 2026-08-18 — see CLAUDE.md) as the primary
regression fixture, not synthetic snippets. The first version of this
module's extraction was tested only against clean synthetic prose and
passed cleanly, then produced garbage ("Ads. Bangalore", "Job Descriptio")
against real, noisy SEO/job-board text in production — this file exists
specifically so that failure mode can't come back silently.
"""
import os
import sys
import tempfile

import authority_discovery as ad
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


print("\n== authority_discovery.py")

# --- find_name_title_candidates: real captured SerpApi output (Paytm,
#     2026-08-18) — the actual production regression fixture -------------
REAL_PAYTM_TEXT = (
    "Vikas Kumar - Talent Acquisition Lead – Fintech | Paytm .... Vikas Kumar. "
    "Talent Acquisition Lead – Fintech | Paytm Payments Services | Leadership & "
    "Volume Hiring | Driving Scale & Efficiency | Building & Nurturing High- ...\n"
    "Kapil Kaushal - DGM - Talent Acquisition @ Paytm. DGM - Talent Acquisition @ "
    "Paytm | Building & Scaling Product & Engineering Teams – Competent and "
    "diligent HR professional with 12+ years of expertise in ...\n"
    "Talent Acquisition- Leadership Hiring - Paytm. This critical role will be "
    "responsible for defining and executing a robust talent acquisition strategy "
    "for senior leadership positions across Paytm. You will ...\n"
    "Career at Paytm. Career at Paytm – Leaves competitive with the industry in "
    "all categories such as marriage leave, PL, CL, Sick Leave & more. – Team "
    "Outing and Engagement ...\n"
    "Meghna Sethi - Senior Manager, Talent Acquisition at Paytm. Meghna Sethi is "
    "a Senior Manager, Talent Acquisition at Paytm Money, bringing expertise in "
    "strategic talent acquisition and development.\n"
    "Paytm - Product Management - Senior Director - Ads. Product Management - "
    "Senior Director - Ads. Bangalore, Karnataka. Product – Paytm Ads / ... "
    "Strong stakeholder management across business, technology, and GTM ...\n"
    "Talent Acquisition - TL/AM- Noida - Noida - Paytm - 3 to 5 .... Job "
    "Description for Talent Acquisition - TL/AM- Noida in Paytm in Noida for 3 "
    "to 5 years of experience. Apply Now ... Lead and motivate the Talent ...\n"
    "Leadership Team - Paytm. The Leadership Team at Paytm Payments Bank is "
    "responsible for defining and executing the company's strategic vision, "
    "managing operations across product ..."
)
real_candidates = ad.find_name_title_candidates(REAL_PAYTM_TEXT, "Paytm")
real_names = [c["name"] for c in real_candidates]

check("extracts the 2 genuine named people from real noisy search output",
      set(real_names) >= {"Vikas Kumar", "Kapil Kaushal"}, f"(got {real_names})")
check("does NOT extract 'Ads. Bangalore' (business unit + city, not a person)",
      "Ads. Bangalore" not in real_names and "Ads" not in real_names)
check("does NOT extract 'Job Descriptio'/'Job Description' (boilerplate phrase)",
      not any("Job Descriptio" in n for n in real_names))
check("does NOT extract 'Engineering Teams'/'Nurturing High' (tagline fragments)",
      "Engineering Teams" not in real_names and "Nurturing High" not in real_names)
check("does NOT extract 'Leadership Team' (a section heading, not a person)",
      "Leadership Team" not in real_names)
check("real result count stays small and precise (2-4), not a flood of noise",
      2 <= len(real_candidates) <= 4, f"(got {len(real_candidates)}: {real_names})")

vikas = next((c for c in real_candidates if c["name"] == "Vikas Kumar"), None)
check("Vikas Kumar classifies as generic_ta (no specific function named alongside "
      "the TA title — matches authority_graph.py's own function_owner > hiring_manager "
      "> ta_lead_function > generic_ta distinction)",
      vikas and vikas["node_type"] == "generic_ta")

# --- clean synthetic cases, matching the REAL line-leading structure -------
text1 = "Neeraj Jain - Head of Product at PhonePe. Neeraj Jain leads the product org."
candidates = ad.find_name_title_candidates(text1, "PhonePe")
names = [c["name"] for c in candidates]
check("extracts a real name from a clean 'Name - Title' line",
      "Neeraj Jain" in names, f"(got {names})")
c = next((c for c in candidates if c["name"] == "Neeraj Jain"), None)
check("classifies the function correctly (product)", c and c["function"] == "product")
check("classifies the node_type correctly (function_owner, 'head of' marker)",
      c and c["node_type"] == "function_owner")

text2 = "Khilan Haria is the CPO of Razorpay, overseeing the product roadmap."
candidates2 = ad.find_name_title_candidates(text2, "Razorpay")
names2 = [c["name"] for c in candidates2]
check("extracts a name next to an acronym title (CPO) via the 'is the' pattern",
      "Khilan Haria" in names2, f"(got {names2})")

# --- false-positive guards --------------------------------------------------
text3 = "Razorpay's Head of Product team has been expanding rapidly this year."
candidates3 = ad.find_name_title_candidates(text3, "Razorpay")
check("does not extract the company's own name as a candidate",
      not any(c["name"] == "Razorpay" for c in candidates3))

text4 = "The Product team at Acme Corp grew by 20% this quarter, said the CEO."
candidates4 = ad.find_name_title_candidates(text4, "Acme Corp")
check("no candidate is fabricated when no real name leads the line",
      len(candidates4) == 0 or all(c["name"] != "Acme Corp" for c in candidates4))

# --- _is_plausible_name ------------------------------------------------------
check("rejects a single-word 'name'", not ad._is_plausible_name("Razorpay", "Razorpay"))
check("rejects a name containing a stopword", not ad._is_plausible_name("Chief Officer", "X"))
check("accepts a genuine 2-word name", ad._is_plausible_name("Priya Sharma", "Razorpay"))

# --- _infer_source -----------------------------------------------------------
check("infers company_leadership_page when the company's own domain appears in context",
      ad._infer_source("posted on razorpay.com/about", "razorpay.com") == "company_leadership_page")
check("infers press_release when the domain doesn't appear",
      ad._infer_source("reported by TechCrunch", "razorpay.com") == "press_release")

# --- discover_for_company: end-to-end against a scratch DB, _search mocked -
tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "ad.sqlite3")
store.init_db(db_path)
now = "2026-08-18T00:00:00"

orig_search = ad._search
ad._search = lambda name, log=print: (
    "Neeraj Jain - Head of Product at PhonePe. Neeraj Jain discussed the roadmap.", "fake query")

with store.connect(db_path) as conn:
    conn.execute(
        "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
        "VALUES ('PhonePe', 'phonepe.com', 0, ?, ?)", (now, now))
    company_id = conn.execute("SELECT id FROM company WHERE name='PhonePe'").fetchone()["id"]

    new_ids = ad.discover_for_company(conn, company_id, "PhonePe", "phonepe.com", now,
                                       log=lambda *a, **k: None)
    check("discover_for_company writes exactly 1 new node from the mocked search",
          len(new_ids) == 1, f"(got {len(new_ids)})")
    node = conn.execute("SELECT * FROM authority_node WHERE id=?", (new_ids[0],)).fetchone()
    check("the written node has the correct name/title/node_type",
          node["person_name"] == "Neeraj Jain" and node["node_type"] == "function_owner")
    check("the written node's source is a valid authority_node_sources.yaml value "
          "(not a made-up string)", node["source"] in ("company_leadership_page", "press_release"))
    check("no contact_channel was written for this node (name discovery never creates a send target)",
          conn.execute("SELECT COUNT(*) c FROM contact_channel WHERE authority_node_id=?",
                        (new_ids[0],)).fetchone()["c"] == 0)

    events = conn.execute(
        "SELECT * FROM event WHERE entity_id=? AND event_type='AUTHORITY_DISCOVERY_MATCH'",
        (new_ids[0],)).fetchall()
    check("an AUTHORITY_DISCOVERY_MATCH event was logged with the source query/context",
          len(events) == 1)

    # re-running against the same company + same mocked result should NOT duplicate
    new_ids_2 = ad.discover_for_company(conn, company_id, "PhonePe", "phonepe.com", now,
                                         log=lambda *a, **k: None)
    check("a second run against the same company finds 0 new nodes (dedup by name)",
          len(new_ids_2) == 0)

# --- discover_for_allowlist: skips companies that already have a real node -
with store.connect(db_path) as conn:
    conn.execute(
        "INSERT INTO company (name, domain, is_conflict_of_interest, created_at, updated_at) "
        "VALUES ('FreshCo', 'freshco.com', 0, ?, ?)", (now, now))

    ad._search = lambda name, log=print: (
        "Ananya Rao - VP Product at FreshCo. Ananya Rao announced the launch.", "fake query 2") \
        if name == "FreshCo" else None

    stats = ad.discover_for_allowlist(conn, log=lambda *a, **k: None, now=now)
    check("discover_for_allowlist skips PhonePe (already has a real node) and processes FreshCo only",
          stats["companies_processed"] == 1, f"({stats})")
    check("discover_for_allowlist found 1 new node for FreshCo",
          stats["nodes_found"] == 1, f"({stats})")

ad._search = orig_search

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
