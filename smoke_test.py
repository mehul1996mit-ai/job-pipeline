"""Local smoke test — no API keys needed, no network calls.

Run:  python smoke_test.py

Verifies: (1) real CV parses with bullets + keywords, (2) experience-band
filter include/exclude cases, (3) title/city/salary filters, (4) dry-run
ATS scoring + missing-keyword extraction against a sample JD.
"""
import json
import sys

import yaml

import matcher
import tailor
from cv_parser import parse_cv

PASS, FAIL = "  [PASS]", "  [FAIL]"
failures = 0


def check(name, condition, detail=""):
    global failures
    print(f"{PASS if condition else FAIL} {name} {detail}")
    if not condition:
        failures += 1


SAMPLE_JD = """
Senior Product Manager — Payments Platform
We are looking for a product manager with 3-6 years of experience building
B2C digital products. You will own the product roadmap, write BRDs, run
agile sprint ceremonies with engineering, and use analytics (Google
Analytics, funnels, A/B testing) to drive conversion. Experience with
lending or credit products is a plus. Familiarity with stakeholder
management, partner integrations and payment gateways preferred. Knowledge
of Kubernetes and Golang is nice to have.
"""

print("== 1. CV PARSE (base_cv.pdf)")
cv = parse_cv("base_cv.pdf")
check("raw text extracted", len(cv.raw_text) > 1000,
      f"({len(cv.raw_text)} chars)")
check("bullets extracted", len(cv.bullets) >= 10,
      f"({len(cv.bullets)} bullets)")
check("keywords extracted", len(cv.keywords) >= 100,
      f"({len(cv.keywords)} keywords)")
check("matching uses full CV, not just title",
      any("clevertap" in k for k in cv.keywords)
      and any("seo" in k for k in cv.keywords),
      "(deep-CV terms like clevertap/seo present in keyword set)")
print("   sample bullets:")
for b in cv.bullets[:3]:
    print(f"     - {b[:90]}")

print("\n== 2. EXPERIENCE BAND FILTER")
my = 4
cases = [
    ("3-5 years of experience", True, "band 3-5 includes 4"),
    ("2 to 4 yrs experience", True, "band 2-4 includes 4"),
    ("8-12 years of experience", False, "band 8-12 excludes 4"),
    ("10+ years of product leadership", False, "10+ excludes 4"),
    ("3+ years experience required", True, "3+ includes 4"),
    ("minimum 5 years experience", True, "min 5 within tolerance of 4"),
    ("no experience requirement stated here", True,
     "no requirement -> pass"),
]
for jd, expected, why in cases:
    got = matcher.experience_ok(jd, my)
    check(f"experience_ok('{jd[:35]}...') == {expected}", got == expected,
          f"({why})")

print("\n== 3. TITLE / CITY / SALARY FILTERS")
config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
tk = config["filters"]["title_keywords"]
cities = config["filters"]["cities"]
check("title allow: 'Senior Product Manager - Payments'",
      matcher.title_ok("Senior Product Manager - Payments", tk))
check("title allow: 'Business Analyst II'",
      matcher.title_ok("Business Analyst II", tk))
check("title reject: 'Java Developer'",
      not matcher.title_ok("Java Developer", tk))
check("city allow: 'Pune, Maharashtra'",
      matcher.city_ok("Pune, Maharashtra", cities))
check("city allow: 'Remote - India'", matcher.city_ok("Remote - India",
                                                      cities))
check("city reject: 'Chennai, TN'", not matcher.city_ok("Chennai, TN",
                                                        cities))
check("city: empty allowlist accepts all", matcher.city_ok("Chennai", []))
check("salary: no reported salary passes even with floor set",
      matcher.salary_ok({"salary_min": None, "salary_max": None}, 2000000))
check("salary: reported below floor rejected",
      not matcher.salary_ok({"salary_max": 900000}, 2000000))
check("salary: reported above floor passes",
      matcher.salary_ok({"salary_max": 2500000}, 2000000))

print("\n== 4. DRY-RUN SCORING vs SAMPLE JD")
score = matcher.ats_score(SAMPLE_JD, cv.keywords, config)
check("ATS score in range", 0 < score <= 100, f"(score={score})")
check("domain bonus applied (JD mentions lending/credit)", score >= 40,
      f"(score={score} should reflect strong overlap + domain bonus)")
irrelevant = "We need a Golang Kubernetes SRE with Terraform and AWS EKS."
low = matcher.ats_score(irrelevant, cv.keywords, config)
check("irrelevant JD scores lower", low < score,
      f"(irrelevant={low} < relevant={score})")
missing = matcher.matched_keywords(SAMPLE_JD, cv.keywords)
check("missing keywords extracted", len(missing) > 0, f"({missing[:8]})")
check("noise words filtered from missing keywords",
      all(w not in missing for w in ("the", "and", "experience", "years")))

print("\n== 5. TAILORED RESUME FILE BUILD (offline, no network)")
master = json.loads(open("resume_master.json", encoding="utf-8").read())
fake_tailored = {
    "tailored_summary": "Reworded summary for this job.",
    "bullets_to_lead_with": [
        # slightly paraphrased vs the real bullet -- fuzzy match must find it
        "Used Google Analytics and CleverTap to drive data-informed decisions",
        "Managed Instagram and Facebook Ads campaigns to cut cost-per-click",
    ],
    "keywords_to_add_if_true": ["seo", "facebook ads"],
    "honest_gap_note": "test",
}
tailored_resume = tailor.build_tailored_resume(master, fake_tailored)
check("summary replaced", tailored_resume["summary"] == fake_tailored["tailored_summary"])
check("name locked", tailored_resume["name"] == master["name"])
check("contact locked", tailored_resume["contact_line"] == master["contact_line"])

pl_role = tailored_resume["experience"][0]["roles"][1]  # Digital Platforms role
check("lead bullet moved to front (Bajaj role)",
      "Google Analytics and CleverTap" in pl_role["bullets"][0])
kantha_role = tailored_resume["experience"][1]["roles"][0]
check("lead bullet moved to front (Kantha role)",
      "Instagram Ads and Facebook Ads" in kantha_role["bullets"][0])
check("no bullet text was fabricated (matched bullet is verbatim original)",
      pl_role["bullets"][0] ==
      "Used Google Analytics and CleverTap to track user behaviour and KPIs, "
      "driving data-informed decisions that lifted conversion rates 23%.")
check("skills reordered toward matched keywords",
      "seo" in tailored_resume["skills"][0]["items"].lower()
      or "facebook" in tailored_resume["skills"][0]["items"].lower())
total_bullets_before = sum(len(r["bullets"]) for c in master["experience"]
                          for r in c["roles"])
total_bullets_after = sum(len(r["bullets"]) for c in tailored_resume["experience"]
                          for r in c["roles"])
check("no bullets lost or added during reorder",
      total_bullets_before == total_bullets_after,
      f"({total_bullets_before} == {total_bullets_after})")

# Regression: two lead bullets landing in the SAME role must preserve the
# LLM's priority order (1st listed ends up 1st), not get silently reversed.
same_role_fields = {
    "tailored_summary": "",
    # The tailor prompt instructs the LLM to return bullets VERBATIM (not
    # paraphrased), so realistic candidates are near-exact copies of the
    # source bullet -- that's what we test the priority-order fix against.
    "bullets_to_lead_with": [
        # both match bullets inside Bajaj's "Home Loan" role, in this order
        "Own product strategy for Home Loan digital acquisition, "
        "translating business and credit-policy requirements into "
        "scalable lead-generation and qualification systems.",
        "Design and ship the MCP (Minimum Credit Parameters) Master and "
        "Lead Allocation Master -- a rules engine that pre-qualifies "
        "leads and routes them to the right lending partner based on "
        "credit profile, eligibility, and business rules.",
    ],
    "keywords_to_add_if_true": [],
}
same_role_resume = tailor.build_tailored_resume(master, same_role_fields)
hl_role = same_role_resume["experience"][0]["roles"][0]
check("same-role priority order preserved (1st listed lead bullet is 1st)",
      hl_role["bullets"][0].startswith("Own product strategy"),
      f"(got: \"{hl_role['bullets'][0][:60]}...\")")
check("same-role 2nd priority in 2nd position",
      hl_role["bullets"][1].startswith("Design and ship the MCP"),
      f"(got: \"{hl_role['bullets'][1][:60]}...\")")

print("\n== 6. JD-ALIGNED REWORDING (fact-integrity validation)")
orig = ("Led development and management of digital platforms (app and web) "
        "for the Personal Loan product, growing user engagement 27% and "
        "user acquisition 14%.")
good_rw = ("Led digital product management for Personal Loan platforms "
           "(app and web), driving client activation and engagement growth "
           "of 27% and user acquisition of 14%.")
bad_rw_newnum = orig.replace("27%", "45%")
bad_rw_addclaim = orig + (" Also owned Wholesale Banking activation "
                          "strategy across payments and liquidity journeys "
                          "for institutional clients worldwide, and more.")
check("valid rewording accepted (same numbers, JD vocabulary)",
      tailor.rewrite_is_safe(orig, good_rw))
check("rewrite changing a metric REJECTED",
      not tailor.rewrite_is_safe(orig, bad_rw_newnum))
check("rewrite ballooning with added claims REJECTED",
      not tailor.rewrite_is_safe(orig, bad_rw_addclaim))
check("empty rewrite REJECTED", not tailor.rewrite_is_safe(orig, ""))

rw_fields = {
    "tailored_summary": "",
    "bullets_to_lead_with": [],
    "rewritten_bullets": [
        {"original": orig, "rewritten": good_rw},
        {"original": orig, "rewritten": bad_rw_newnum},  # must be ignored
    ],
    "keywords_to_add_if_true": [],
}
JD_FOR_SKILLS = ("We want experience with Google Analytics, A/B Testing "
                 "and SEO plus stakeholder management.")
rw_resume = tailor.build_tailored_resume(master, rw_fields,
                                         jd_text=JD_FOR_SKILLS)
pl_bullets = rw_resume["experience"][0]["roles"][1]["bullets"]
check("valid rewrite applied in place",
      any(b == good_rw for b in pl_bullets))
check("original wording replaced (not duplicated)",
      not any(b == orig for b in pl_bullets)
      and sum(1 for b in pl_bullets if b == good_rw) == 1)
analytics_group = next(g for g in rw_resume["skills"]
                       if g["label"] == "Analytics & Tools")
first_items = [s.strip() for s in
               analytics_group["items"].split(",")][:3]
check("skill ITEMS reordered toward JD mentions",
      "Google Analytics" in first_items and "A/B Testing" in first_items,
      f"(first items: {first_items})")
all_items_before = sorted(
    s.strip() for g in master["skills"] for s in g["items"].split(","))
all_items_after = sorted(
    s.strip() for g in rw_resume["skills"] for s in g["items"].split(","))
check("no skill items added/removed/renamed by reorder",
      all_items_before == all_items_after)

print("\n== 7. CHANGE LOG / PORTAL PRIORITY / REMOTE-ONLY")
cl = tailor.change_log(master, rw_resume)
check("change log reports reworded bullet",
      "1 bullet(s) reworded" in cl, f"({cl})")
check("change log reports skill resequencing",
      "Skill items resequenced" in cl)
check("unchanged resume -> 'No changes' log",
      tailor.change_log(master, master) == "No changes vs base CV")

import dedupe
dup_jobs = [
    {"source": "adzuna", "company": "Citi", "title": "Senior BA",
     "location": "Pune", "url": "adz"},
    {"source": "workday", "company": "Citi", "title": "Senior BA",
     "location": "Pune", "url": "wd"},
]
kept = dedupe.dedupe_cross_source(dup_jobs)
check("cross-source dupe keeps DIRECT employer ATS over aggregator",
      len(kept) == 1 and kept[0]["source"] == "workday")
check("apply_channel tagged", kept[0].get("apply_channel") == "direct")

remote_cfg = {"filters": {"title_keywords": ["product manager"],
                          "cities": [], "remote_only": True},
              "profile": {"experience_years": 4}}
check("remote_only drops on-site listing",
      not matcher.passes_filters(
          {"title": "Product Manager", "location": "Pune, MH",
           "description": ""}, remote_cfg))
check("remote_only keeps remote listing",
      matcher.passes_filters(
          {"title": "Product Manager", "location": "Remote - India",
           "description": ""}, remote_cfg))

print("\n== 8. FOLLOW-UP TRACKER / WEEKLY STATS (offline)")
from datetime import date, timedelta
import tracker

today = date(2026, 7, 16)
old = (today - timedelta(days=8)).isoformat()
fresh = (today - timedelta(days=2)).isoformat()
rows = [
    {"applied": "yes", "url": "u1", "title": "PM", "company": "A",
     "applied_on": old, "_queue_date": old, "score": "82",
     "source": "adzuna", "tailored_summary": "x"},
    {"applied": "yes", "url": "u2", "title": "BA", "company": "B",
     "applied_on": fresh, "_queue_date": fresh, "score": "70",
     "source": "workday", "tailored_summary": "x"},
    {"applied": "response", "url": "u3", "title": "PM", "company": "C",
     "applied_on": old, "_queue_date": old, "score": "85",
     "source": "workday", "tailored_summary": "x"},
    {"applied": "no", "url": "u4", "title": "PM", "company": "D",
     "applied_on": "", "_queue_date": old, "score": "60",
     "source": "adzuna", "tailored_summary": ""},
]
due = tracker.followups_due(rows, days=7, today=today)
check("8-day-old 'yes' is due for follow-up",
      any(r["url"] == "u1" for r in due))
check("2-day-old 'yes' NOT due", not any(r["url"] == "u2" for r in due))
check("row with recorded outcome NOT nudged",
      not any(r["url"] == "u3" for r in due))
check("unapplied row NOT nudged", not any(r["url"] == "u4" for r in due))
due2 = tracker.followups_due(rows, days=7, today=today,
                             already={"u1": "2026-07-15"})
check("already-nudged application not nudged twice", len(due2) == 0)

stats = tracker.weekly_stats(rows)
check("stats reports applied count", "applied: 3" in stats)
check("stats reports positive-response rate", "1/3" in stats)
check("stats breaks down by score band", "80+" in stats)

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
sys.exit(1 if failures else 0)
