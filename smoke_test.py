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

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
sys.exit(1 if failures else 0)
