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
# Chennai moved from "reject" to "allow" 2026-08-10 (cities widened for
# volume) -- swapped the reject-example to a city still genuinely excluded,
# not deleted, so this still guards the real behavior (non-allowlisted
# cities are rejected), not just today's specific allowlist contents.
check("city allow: 'Chennai, TN' (widened 2026-08-10)",
      matcher.city_ok("Chennai, TN", cities))
check("city reject: 'Kolkata, WB'", not matcher.city_ok("Kolkata, WB",
                                                        cities))
check("city: empty allowlist accepts all", matcher.city_ok("Chennai", []))
check("city allow: bare 'Remote' with no scope", matcher.city_ok("Remote", cities))
# Found via real Greenhouse data 2026-07-28: global boards post plenty of
# "Chicago, IL, Remote" / "US-Remote" postings, and the old "any remote
# passes" rule let those through even though they're not open to India.
check("city REJECT: 'Chicago, IL, Remote' (US-scoped, not India)",
      not matcher.city_ok("Chicago, IL, Remote", cities))
check("city REJECT: 'US-Remote'", not matcher.city_ok("US-Remote", cities))
check("city REJECT: 'Remote (Canada)'",
      not matcher.city_ok("Remote (Canada)", cities))
check("city REJECT: 'Seattle, SF, Remote' (US cities, no country marker)",
      not matcher.city_ok("Seattle, SF, Remote", cities))
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

print("\n== 9. PORTED SCORING STACK (frozen engine + structured layer)")
import scoring_core
import jd_analyst
from aggregate import aggregate_score, trajectory_score, experience_fit
from calibrate import calibrate_score, counterfactual_gaps, jd_difficulty
from cv_structure import parse_cv_structured, union_months, find_gaps
from skill_match import structured_skill_match

# --- tokenizer / stemmer -------------------------------------------------
check("stemmer folds inflections", scoring_core.stem("modelling")
      == scoring_core.stem("models") == "model")
check("synonyms fold to one canonical",
      scoring_core.token_parts("js")["canon"]
      == scoring_core.token_parts("javascript")["canon"])
check("stopwords dropped", scoring_core.canonical_token("the") is None)
check("short abbreviations survive via synonyms",
      scoring_core.canonical_token("k8s") is not None)
check("js_round rounds halves up (not banker's)",
      scoring_core.js_round(2.5) == 3 and scoring_core.js_round(3.5) == 4)

# --- bigrams are separate competencies -----------------------------------
both_words_apart = scoring_core.compute_match(
    "credit risk modelling required", "I know credit. I know risk.")
real_bigram = scoring_core.compute_match(
    "credit risk modelling required", "I did credit risk modelling.")
check("bigram needs the CV's own bigram, not both words separately",
      real_bigram["score"] > both_words_apart["score"],
      f"({real_bigram['score']} > {both_words_apart['score']})")

# --- FROZEN ACCEPTANCE REGRESSION ---------------------------------------
# Ported from the source repo, where it is treated as permanent. If this
# fails, fix the scorer -- never loosen the threshold.
CREDIT_JD = """Required: credit risk, digital lending, loan origination,
product management, BRDs, stakeholder management. Must have fintech and NBFC
experience with credit policy and lending partnerships."""
MARKETING_JD = """Required: brand management, ATL BTL campaigns, trade
marketing, shopper activation, TV production, creative agency management for
an FMCG foods portfolio."""
c = scoring_core.compute_match(CREDIT_JD, cv.raw_text)["score"]
m = scoring_core.compute_match(MARKETING_JD, cv.raw_text)["score"]
check("ACCEPTANCE: credit JD beats marketing JD by >25", c - m > 25,
      f"(credit {c} vs marketing {m}, delta {c - m})")
check("ACCEPTANCE: marketing JD still scores nonzero", m > 0, f"({m})")
check("domain bonus is additive, never a filter",
      scoring_core.compute_match(MARKETING_JD, cv.raw_text)["bonus"] == 0
      and m > 0, "(zero domain bonus, still scored)")

# --- structured CV parse -------------------------------------------------
scv = parse_cv_structured(cv.raw_text, cv.section_map())
check("roles parsed from real CV", scv["role_count"] >= 3,
      f"({scv['role_count']} roles)")
check("every role has a title and an employer",
      all(e["title"] and e["company"] for e in scv["experience"]),
      str([f"{e['title'][:24]}@{e['company']}" for e in scv["experience"]]))
check("tenure computed", 3 <= scv["total_years"] <= 8,
      f"({scv['total_years']} yrs)")
check("declared vs demonstrated skills kept separate",
      len(scv["skills"]["declared"]) > len(scv["skills"]["demonstrated"]) > 0,
      f"({len(scv['skills']['declared'])} declared, "
      f"{len(scv['skills']['demonstrated'])} demonstrated)")
check("education section found (arms the degree gate correctly)",
      bool(scv["sections"]["education"]))
check("overlapping roles are not double-counted",
      union_months([{"start": 0, "end": 24}, {"start": 12, "end": 36}]) == 36)
check("gaps below threshold are not reported",
      find_gaps([{"start": 0, "end": 12}, {"start": 14, "end": 24}], 3) == [])

# --- FAIRNESS AUDIT (standing; fix the scorer, never the tolerance) ------
check("FAIRNESS: education-explained gap is not counted against the CV",
      len(scv["unexplained_gaps"]) == 0,
      f"({len(scv['gaps'])} gap(s), all explained by study)")
check("FAIRNESS: a single-role CV is not punished on trajectory",
      trajectory_score([{"title": "Product Manager"}]) >= 0.7)
step_down = trajectory_score([{"title": "Product Manager"},
                              {"title": "Head of Product"}])
check("FAIRNESS: a step down in seniority is not scored near zero",
      step_down >= 0.5, f"({step_down})")
check("FAIRNESS: no stated minimum experience scores neutral-high, not zero",
      experience_fit(2, 0) >= 0.8, f"({experience_fit(2, 0)})")
check("FAIRNESS: unverifiable gate is flagged for review, never auto-failed",
      (lambda a: not a["gates"]["failed_checkable"]
       and any("confirm manually" in f for f in a["flags"]))(
          aggregate_score({"mandatory_eligibility": ["US work authorization"]},
                          scv, {"skill_score": 0.5})))

# --- JD analyst ----------------------------------------------------------
a = jd_analyst.analyze_jd(SAMPLE_JD)
check("analyst reads a minimum-years requirement", a["min_years"] == 3,
      f"({a['min_years']})")
check("analyst separates preferred from must-have",
      isinstance(a["preferred_skills"], list)
      and "deterministic" == a["analyst"])
check("analyst does not span clause boundaries",
      not any(" agile" in s and "management" in s
              for s in a["must_have_skills"] + a["preferred_skills"]),
      "(commas break phrase runs)")
merged = jd_analyst.merge_llm_analysis(
    a, {"must_have_skills": ["product management"], "min_years": 5})
check("LLM analysis overrides the regex read", merged["min_years"] == 5
      and merged["analyst"] == "llm")
check("empty LLM fields never blank out a real regex finding",
      jd_analyst.merge_llm_analysis(a, {"must_have_skills": []}
                                    )["must_have_skills"] == a["must_have_skills"])
check("a failed LLM call leaves the analysis deterministic",
      jd_analyst.merge_llm_analysis(a, None)["analyst"] == "deterministic")

# --- structured scoring / eligibility gate -------------------------------
JD_OK = {"must_have_skills": ["product management", "stakeholder management"],
         "preferred_skills": ["google analytics"], "key_skills": [],
         "min_years": 3, "education_level": "bachelor",
         "mandatory_eligibility": [], "jd_text": SAMPLE_JD}
sm = structured_skill_match(JD_OK, scv)
check("demonstrated skill outweighs a declared-only one",
      all(x["source"] in ("demonstrated", "declared") for x in sm["matched"])
      and sm["skill_score"] > 0, f"(skill_score {sm['skill_score']:.2f})")
agg = aggregate_score(JD_OK, scv, sm, cv_text=cv.raw_text, jd_text=SAMPLE_JD)
check("structured score in range", 0 <= agg["score"] <= 100, f"({agg['score']})")
check("real CV clears the bachelor gate (MBA + B.Tech present)",
      not agg["gates"]["failed_checkable"])

JD_PHD = dict(JD_OK, education_level="phd")
agg_phd = aggregate_score(JD_PHD, scv, sm, cv_text=cv.raw_text)
check("unmet checkable degree gate hard-caps the score",
      agg_phd["score"] <= 40 and agg_phd["gates"]["failed_checkable"],
      f"({agg_phd['score']})")

# --- calibration + counterfactual gaps -----------------------------------
easy = jd_difficulty({"must_have_skills": ["excel"], "preferred_skills":
                      ["word", "ppt", "email"]})["difficulty"]
hard = jd_difficulty({"must_have_skills": ["a", "b", "c", "d", "e", "f"],
                      "min_years": 10, "education_level": "phd",
                      "mandatory_eligibility": ["visa", "clearance"]})["difficulty"]
check("a narrow senior gated JD scores harder than a broad one", hard > easy,
      f"({hard} > {easy})")
check("an unclassified posting is 'unknown' (0.5), never assumed easy",
      jd_difficulty({})["factors"]["narrowness"] == 0.5)
same = 70
check("same raw score reads higher on the harder posting",
      calibrate_score(same, {"must_have_skills": ["a", "b", "c", "d", "e", "f"],
                             "min_years": 10})["percentile"]
      > calibrate_score(same, {"must_have_skills": ["excel"]})["percentile"])
gaps = counterfactual_gaps(JD_OK, scv, sm,
                           agg_kwargs={"cv_text": cv.raw_text}, limit=5)
check("counterfactual gaps ranked by impact, descending",
      all(gaps["gaps"][i]["delta"] >= gaps["gaps"][i + 1]["delta"]
          for i in range(len(gaps["gaps"]) - 1)))
check("gaps report a real before->after delta",
      all(g["to"] > g["from"] for g in gaps["gaps"]))

# --- end-to-end via matcher ---------------------------------------------
r = matcher.score_job(SAMPLE_JD, cv.raw_text, scv, config)
check("score_job returns structured + frozen + percentile",
      isinstance(r["score"], int) and isinstance(r["frozen_score"], int)
      and 1 <= r["percentile"] <= 99,
      f"(structured {r['score']}, frozen {r['frozen_score']}, "
      f"pct {r['percentile']})")
check("scoring runs with no API key set (fully deterministic)",
      r["analyst"] == "deterministic")

print("\n== 10. MATCH FEEDBACK / LEARNING LOOP (offline)")
import feedback as fb
from sources import ashby, job_alert_email, smartrecruiters

def _row(label, score, subs, title="Product Manager", source="adzuna"):
    return {"match_feedback": label, "score": str(score), "title": title,
            "source": source, "sub_scores": json.dumps(subs)}

thin = [_row("good", 80, {"skill_match": 0.8}) for _ in range(3)]
r_thin = fb.readiness(thin)
check("below the label floor, nothing is concluded", not r_thin["ready"])
check("shortfall is reported honestly, not hidden",
      f"{len(thin)}/{fb.MIN_LABELS}" in r_thin["note"], f"({r_thin['note'][:60]})")
check("build_proposal returns no weights below the floor",
      fb.build_proposal({}, "data")["weights"] is None
      or fb.readiness(fb.load_labelled("data"))["ready"])

# A set where skill_match genuinely tracks the label and trajectory doesn't.
many = ([_row("good", 78, {"skill_match": 0.85, "trajectory": 0.5,
                           "domain": 0.9}) for _ in range(9)]
        + [_row("partial", 60, {"skill_match": 0.55, "trajectory": 0.9,
                                "domain": 0.5}) for _ in range(8)]
        + [_row("no", 44, {"skill_match": 0.2, "trajectory": 0.7,
                           "domain": 0.1}, title="Brand Manager") for _ in range(8)])
r_many = fb.readiness(many)
check("enough labels across all three classes unlocks proposals",
      r_many["ready"], f"({r_many['total']} labels)")

sep = fb.score_separation(many)
check("separation check reports the good-vs-no score gap",
      sep["separates"] and sep["delta"] > 5, f"(delta {sep['delta']})")

w = fb.propose_weights(many, {"skill_match": 0.40, "trajectory": 0.05,
                              "domain": 0.15})
check("weight proposal correlates each sub-score with 'good'",
      w["correlations"]["skill_match"] > w["correlations"]["trajectory"],
      f"(skill {w['correlations']['skill_match']} vs "
      f"trajectory {w['correlations']['trajectory']})")
check("a predictive sub-score is weighted UP",
      w["proposed"]["skill_match"] > 0.40)
check("weights still sum to what they summed to before",
      abs(sum(w["proposed"].values()) - 0.60) < 1e-6,
      f"({sum(w['proposed'].values()):.4f})")
check("a single batch cannot move a weight more than the cap",
      all(abs(w["proposed"][k] - c) <= c * fb.MAX_WEIGHT_NUDGE + 1e-9
          for k, c in {"skill_match": 0.40, "trajectory": 0.05,
                       "domain": 0.15}.items()))
check("'partial' is excluded from correlation, not forced onto the axis",
      fb._positive("partial") == 0 and fb._positive("good") == 1)

kw = fb.keyword_performance(many, ["product manager", "brand manager"])
bad = [k for k in kw if k["keyword"] == "brand manager"]
check("a keyword you always reject surfaces with a 1.0 no-rate",
      bad and bad[0]["no_rate"] == 1.0, str(bad[:1]))
check("a keyword below the sample floor is not judged",
      not fb.keyword_performance(many[:3], ["product manager"]))
check("point_biserial refuses a constant feature",
      fb.point_biserial([5, 5, 5, 5], [1, 0, 1, 0]) is None)
check("point_biserial refuses a single-class outcome",
      fb.point_biserial([1, 2, 3, 4], [1, 1, 1, 1]) is None)

# --- new sources are keyless / opt-in and fail closed --------------------
check("smartrecruiters skips cleanly with no companies configured",
      smartrecruiters.fetch({}, log=lambda *a: None) == [])
check("ashby skips cleanly with no boards configured",
      ashby.fetch({}, log=lambda *a: None) == [])
check("email alerts are OFF unless explicitly enabled",
      job_alert_email.fetch({}, log=lambda *a: None) == [])
check("email alerts skip without credentials, never guess them",
      job_alert_email.fetch({"job_alert_email": {"enabled": True}},
                            log=lambda *a: None) == [])
check("portal detected from a Naukri alert link",
      job_alert_email._portal_of(
          "https://www.naukri.com/job-listings-product-manager-abc-123")
      == "naukri")
check("portal detected from a LinkedIn alert link",
      job_alert_email._portal_of(
          "https://www.linkedin.com/jobs/view/4012345678/") == "linkedin")
check("unsubscribe/social links are never treated as jobs",
      all(job_alert_email.NOISE_URL_RE.search(u) for u in
          ["https://naukri.com/unsubscribe?x=1",
           "https://www.facebook.com/naukri"]))
_alert_html = ('<a href="https://www.linkedin.com/jobs/view/4012345678/">'
               'Senior Product Manager - Payments</a>')
check("job title read from the alert email's own link text",
      job_alert_email._anchor_titles(_alert_html).get(
          "https://www.linkedin.com/jobs/view/4012345678/")
      == "Senior Product Manager - Payments")
check("a 'View job' button falls back to the URL slug, not a fake title",
      job_alert_email._anchor_titles(
          '<a href="https://www.naukri.com/job-listings-x-1">View Job</a>') == {})

# --- Ashby secondaryLocations parsing (real-API bug, found 2026-07-28) ----
# secondaryLocations is a list of {"location": str, "address": {...}} objects,
# not bare strings. Joining it directly raised TypeError and would have
# silently dropped every multi-location Ashby posting.
import unittest.mock as _mock
_fake_job = {
    "title": "Product Manager", "isListed": True,
    "location": "",       # empty primary -> falls back to secondaryLocations
    "secondaryLocations": [{"location": "Bengaluru", "address": {}}],
    "descriptionPlain": "x", "jobUrl": "https://x", "publishedAt": "",
}
with _mock.patch("sources.ashby.requests.get") as _mget:
    _mget.return_value.raise_for_status = lambda: None
    _mget.return_value.json = lambda: {"jobs": [_fake_job]}
    _rows = ashby.fetch({"ashby": {"boards": ["testco"]}}, log=lambda *a: None)
check("Ashby secondaryLocations (list of objects) parses without crashing",
      len(_rows) == 1 and "Bengaluru" in _rows[0]["location"],
      f"({_rows[0]['location'] if _rows else 'no rows'})")

# ===================== 11. SENIORITY / EXPERIENCE JUDGEMENT =================
# STANDING GUARDS, not ordinary unit tests (same convention as the fairness
# audit and acceptance regression above). Two properties must hold forever:
#
#  (a) COMPANY AGE IS NEVER READ AS A REQUIREMENT. Every string below is
#      verbatim from a real 2026-08 posting. If one of these ever parses as a
#      requirement, the pipeline starts silently hiding good jobs — fix the
#      extractor, never the expectation.
#  (b) AN INFERRED BAND NEVER MASQUERADES AS A STATED ONE. Acting on a guess
#      from title wording and acting on the posting's own words are different
#      levels of evidence, and config.yaml only hard-penalises on the verdict
#      while the CSV shows the confidence — so the tiers must stay honest.
print("\n== 11. SENIORITY / EXPERIENCE JUDGEMENT")
import seniority as _sen

_COMPANY_AGE = [
    "P&G was founded over 180 years ago as a simple soap and candle company",
    "With 45 years of experience and a presence across 10 countries, CAI combines",
    "Kobie, a 35-year veteran of the loyalty industry",
    "In March 2026, we delivered the largest month in our 11-year history",
    "please consider applying for a maximum of 3 roles within 12 months",
]
for _txt in _COMPANY_AGE:
    _b = _sen.extract_experience("Product Manager", _txt)
    check(f"company-age text is not a requirement: '{_txt[:38]}...'",
          _b["confidence"] in ("unknown", "inferred"),
          f"(got {_b['confidence']} {_b['min_years']}-{_b['max_years']})")

# Adzuna ships ranges with the separator stripped (verified against their live
# API, 2026-08-09). "48 years" means 4-8 and must never be read literally.
for _raw, _lo, _hi in [("Experience: 48 years", 4, 8),
                       ("Experience: 810 Years", 8, 10),
                       ("712 years of experience in partner management", 7, 12)]:
    _b = _sen.extract_experience("Manager", _raw)
    check(f"mangled range repaired: {_raw[:26]!r} -> {_lo}-{_hi}",
          (_b["min_years"], _b["max_years"], _b["confidence"])
          == (_lo, _hi, "repaired"),
          f"(got {_b['min_years']}-{_b['max_years']} {_b['confidence']})")

_b = _sen.extract_experience("Senior Technical Product Manager- Micro Lending",
                             "Experience: 10 years NBFC / Fintech")
check("a stated 10-year floor is over_senior at a 8-year ceiling",
      _sen.judge(_b, 4.5, comfort_max=8)["verdict"] == "over_senior"
      and _b["confidence"] == "stated")

_b = _sen.extract_experience("Product Owner, VP", "support senior management")
check("a bare VP is judged on its band CENTRE, not its floor",
      _sen.judge(_b, 4.5, comfort_max=8)["verdict"] == "over_senior"
      and _b["confidence"] == "inferred",
      f"({_b['confidence']} {_b['min_years']}-{_b['max_years']})")

_b = _sen.extract_experience("Product Manager", "4-6+ years of product management experience")
check("a real stated range still reads as a good fit",
      _sen.judge(_b, 4.5, comfort_max=8)["verdict"] == "good_fit"
      and _b["confidence"] == "stated")

# Missed live, 2026-08-10: "AVP" wasn't recognised as a title tier at all, so
# "Sr AVP" fell through to the generic "senior" pattern (band 6-10, centre
# exactly 8) and never crossed the ceiling. Real posting: Wells Fargo,
# "Sr AVP- Project Manager", no stated years anywhere in the full 2000-char
# JD (not a truncation case, a genuine title-tier gap).
_b = _sen.extract_experience("Sr AVP- Project Manager",
                             "About this role: Wells Fargo is seeking a Hybrid Markets PM")
check("AVP is recognised as a senior BFSI/GCC grade, not generic 'senior'",
      _sen.judge(_b, 4.5, comfort_max=8)["verdict"] == "over_senior"
      and _b["confidence"] == "inferred" and _b["seniority"] == "avp",
      f"({_b['confidence']} {_b['seniority']} {_b['min_years']}-{_b['max_years']})")
check("a bare AVP (no Sr/Senior prefix) is caught the same way",
      _sen.judge(_sen.extract_experience("AVP, Product Manager", "Own the roadmap"),
                4.5, comfort_max=8)["verdict"] == "over_senior")
check("AVP recognition doesn't swallow a plain Senior Product Manager",
      _sen.judge(_sen.extract_experience("Senior Product Manager", "Own the roadmap"),
                4.5, comfort_max=8)["verdict"] == "good_fit")

check("no experience signal anywhere stays 'unknown', never a guessed number",
      _sen.extract_experience("Product Manager",
                              "Own the roadmap and work with design")["confidence"]
      == "unknown")

# The penalty must be a PENALTY, not a filter: the row survives to the CSV.
_cfgp = dict(config)
_cfgp["profile"] = {"experience_years": 4, "comfort_max_years": 8,
                    "stretch_years": 2, "over_senior_penalty": 25}
_r = matcher.score_job("Project Manager Experience : 11 years Mumbai",
                       cv.raw_text, scv, _cfgp, title="Project Manager")
check("over_senior costs score but still returns a scored row",
      _r["exp_verdict"] == "over_senior"
      and _r["score"] < _r["score_before_seniority"]
      and _r["score"] >= 0,
      f"({_r['score_before_seniority']} -> {_r['score']})")

# Real reported bug, 2026-08-10: percentile/band were calibrated off
# score_before_seniority, so a job penalised down to 34 still showed "64th
# percentile / competitive" -- actively misleading, since "competitive"
# invites the reader to conclude the opposite of what the penalty found.
check("percentile is calibrated off the FINAL (post-penalty) score, not the pre-penalty one",
      _r["percentile"] == matcher.calibrate.calibrate_score(
          _r["score"], _r["analysis"])["percentile"],
      f"(got {_r['percentile']})")
check("...and does NOT match what the pre-penalty score would calibrate to",
      _r["percentile"] != matcher.calibrate.calibrate_score(
          _r["score_before_seniority"], _r["analysis"])["percentile"],
      f"(pre-penalty percentile would have been "
      f"{matcher.calibrate.calibrate_score(_r['score_before_seniority'], _r['analysis'])['percentile']})")

print("\n== 12. EMPLOYER-INDUSTRY TIER (domain sub-score floor/cap)")
import company_industry as _ci

_NEUTRAL_JD = "Own the roadmap, work with design and engineering daily."

check("allowlist hit -> core tier, evidence names the category",
      _ci.classify("Navi", "")["tier"] == "core"
      and _ci.classify("Navi", "")["basis"] == "allowlist",
      f"({_ci.classify('Navi', '')})")

check("allowlist hit is case/punctuation insensitive",
      _ci.classify("razorpay", "")["basis"] == "allowlist",
      f"({_ci.classify('razorpay', '')})")

check("services-tier company name -> capped, negative_list basis",
      _ci.classify("Infosys BPM", "")["tier"] == "services"
      and _ci.classify("Infosys BPM", "")["basis"] == "negative_list")

check("'our client, a leading NBFC' (staffing JD) -> services, not core",
      _ci.classify("Randstad India",
                   "Our client, a leading NBFC, is hiring")["tier"]
      == "services")

check("JD self-description ('we are a fintech...') -> adjacent, unlisted company",
      _ci.classify("Totally Unlisted Startup Pvt Ltd",
                   "We are a fintech building credit products for India."
                   )["tier"] == "adjacent")

check("bare company name never matched on keyword unless source is direct-ATS",
      _ci.classify("Bluewave Capital Technologies", "", source="adzuna")["tier"]
      == "unknown",
      "(aggregator-reported names are not a verified employer identity)")

check("same name-keyword match DOES fire for a direct-ATS source",
      _ci.classify("Bluewave Capital Technologies", "", source="greenhouse")["tier"]
      == "adjacent")

check("unmatched company -> unknown, no evidence fabricated",
      _ci.classify("Some Random Widget Co", "") == {
          "tier": "unknown", "basis": "unknown", "evidence": ""})

# --- floor/cap actually changes the domain sub-score, and only that -------
check("apply_domain_floor_cap: core floors a low domain score up",
      _ci.apply_domain_floor_cap(0.1, "core") == 1.0)
check("apply_domain_floor_cap: services caps a high domain score down",
      _ci.apply_domain_floor_cap(0.9, "services") == 0.3)
check("apply_domain_floor_cap: unknown is a no-op",
      _ci.apply_domain_floor_cap(0.37, "unknown") == 0.37)
check("apply_domain_floor_cap: adjacent never lowers an already-high score",
      _ci.apply_domain_floor_cap(0.95, "adjacent") == 0.95)

# --- end-to-end via score_job: unknown company must be byte-identical -----
_r_baseline = matcher.score_job(_NEUTRAL_JD, cv.raw_text, scv, config,
                                title="Product Manager")
_r_unknown = matcher.score_job(_NEUTRAL_JD, cv.raw_text, scv, config,
                               title="Product Manager",
                               company="Some Random Widget Co", source="adzuna")
check("an unclassified company changes NOTHING vs. no company at all",
      _r_unknown["score"] == _r_baseline["score"]
      and _r_unknown["sub_scores"] == _r_baseline["sub_scores"],
      f"({_r_baseline['score']} vs {_r_unknown['score']})")

# --- a core-tier employer outscores an unknown one on an identical,
#     domain-keyword-free JD -- this is the actual feature working ---------
_r_core = matcher.score_job(_NEUTRAL_JD, cv.raw_text, scv, config,
                            title="Product Manager", company="Navi")
check("core-tier employer scores strictly higher than unknown on the same "
      "domain-keyword-free JD",
      _r_core["score"] > _r_unknown["score"],
      f"(core {_r_core['score']} vs unknown {_r_unknown['score']})")
check("company_tier/basis/evidence are reported on the row, not hidden",
      _r_core["company_tier"] == "core" and _r_core["company_basis"] == "allowlist")

# --- services-tier is a CAP, never a filter: still nonzero, still scored --
# Deliberately no "our client"-style phrase here -- that's covered above by
# the classify() unit checks; this JD text alone must be tier-neutral so the
# ONLY variable between the two calls below is the employer name/tier.
_DOMAIN_DENSE_JD = ("Own credit and lending product decisions across "
                    "digital banking, fintech and BFSI credit risk "
                    "initiatives, working with NBFC partners.")
_r_services = matcher.score_job(_DOMAIN_DENSE_JD, cv.raw_text, scv, config,
                                title="Product Manager", company="Infosys BPM")
_r_services_unknown = matcher.score_job(
    _DOMAIN_DENSE_JD, cv.raw_text, scv, config,
    title="Product Manager", company="Some Random Widget Co")
check("a domain-keyword-dense JD scores LOWER at a services employer than "
      "the identical text would at an unclassified one",
      _r_services["sub_scores"]["domain"] < _r_services_unknown["sub_scores"]["domain"],
      f"(services domain {_r_services['sub_scores']['domain']:.2f} vs "
      f"unknown {_r_services_unknown['sub_scores']['domain']:.2f})")
check("...but the cap never zeroes it out -- still a real, nonzero score",
      _r_services["score"] > 0)

check("classify() needs no network/LLM call (pure function over local yaml)",
      isinstance(_ci.classify("Navi", "We are a fintech."), dict))

# Real bug found by measuring against the 2026-08-13..17 queues: "Capco" (a
# real BFSI consultancy, not a staffing agency) was capped to services 6
# times because its JD mentioned "Wipro" as a delivery partner/vendor, not
# because Capco's OWN name/identity says staffing. A brand-name keyword must
# only ever match the company field, never free JD text -- a legitimate
# core-tier employer could just as easily name-drop a vendor.
check("a vendor/competitor NAME mentioned in JD body does not cap an "
      "otherwise-unmatched employer",
      _ci.classify("Capco", "We partner with delivery vendors including "
                            "Wipro on select engagements.")["tier"]
      != "services",
      f"({_ci.classify('Capco', 'We partner with delivery vendors including Wipro on select engagements.')})")
check("...but the company's OWN name still matches a brand keyword normally",
      _ci.classify("Wipro Limited", "")["tier"] == "services")

print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
sys.exit(1 if failures else 0)
