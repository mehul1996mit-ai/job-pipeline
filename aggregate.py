"""Structured multi-sub-score aggregation — ported from cv-match-copilot's
lib/aggregate.js.

Consumes the structured CV parse (cv_structure) + the structured skill match
(skill_match) + the analyst JD, and produces the deterministic multi-sub-score
STRUCTURED score. Kept entirely separate from the frozen headline score in
scoring_core.py.

Sub-scores (each normalized 0..1), never blended into one opaque pass:
  skill_match     — layered, tier/source/recency/depth aware
  experience_fit  — years vs JD minimum on a threshold curve
  education       — degree gate (checkable mandatory) + neutral otherwise
  domain          — domain-keyword overlap, additive, never a filter
  achievement     — quantified-outcome density, lifted when the JD stresses it
  trajectory      — seniority progression across roles

WHICH SUB-SCORES ARE ACTUALLY BLENDED (rebalanced 2026-08-19). All six are
still computed and REPORTED — they are useful diagnostics and feedback.py
correlates against them — but only the three that vary with the POSTING are
blended into the score. Measured over 919 real scored jobs from the August
queues, the other three are effectively constants:

    sub-score       weight  mean  stdev   at 1.00
    education        0.10   1.00  0.033     100%     <- pinned
    trajectory       0.05   1.00  0.000     100%     <- zero information
    achievement      0.10   0.92  0.070      41%     <- near-constant

They are properties of the CANDIDATE'S CV, not of the fit between CV and
posting, so they were handing every listing on earth ~25 points of free
score (plus another ~17 from experience_fit's 0.85 "JD states no minimum"
default). The observable effect: a Registered Nurse posting scored 46 and a
Java Backend Developer 48, against a min_score_to_tailor of 50 — irrelevant
work sat one bad rounding away from consuming a tailoring call, and real
fintech roles could not separate from noise because 41 of every 100 points
were identical for all of them.

Education is NOT lost by leaving the blend: it was never doing useful work
as a gradient, and it still functions as the hard ELIGIBILITY GATE below,
which is the honest shape for it — you either meet a stated degree
requirement or you don't. `trajectory_score()`/`achievement_score()` remain
exported and tested; they just no longer dilute the fit signal.

Penalties subtract (unexplained gaps, verbatim-JD-mirror). A CHECKABLE
mandatory gate that fails (e.g. a required degree the CV lacks) hard-caps the
score — no skill overlap compensates for ineligibility. Unverifiable gates
(visa, licence) are FLAGGED for human review, never auto-failed: that would
unfairly zero good candidates on data a CV can't carry.
"""
from __future__ import annotations

import re

import company_industry
from scoring_core import compute_match, js_round

# Tunable per role-family via the `weights` argument. Only posting-responsive
# sub-scores are blended — see "WHICH SUB-SCORES ARE ACTUALLY BLENDED" above
# before adding one back. domain is weighted heavily on purpose: it is the
# only signal that distinguishes "a product role in lending/fintech" from "a
# product-shaped role in an unrelated industry", which is the single
# distinction this pipeline exists to make.
DEFAULT_WEIGHTS = {
    "skill_match": 0.55, "domain": 0.30, "experience_fit": 0.15,
}

# Computed and reported, deliberately NOT blended (constants across postings).
# Kept as a named set so a future change re-adding one is an explicit act.
DIAGNOSTIC_SUB_SCORES = ("education", "achievement", "trajectory")

DEGREE_RANK = {"highschool": 1, "diploma": 2, "associate": 2, "bachelor": 3,
               "master": 4, "mba": 4, "phd": 5, "doctorate": 5}

HARD_CAP = 40


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def degree_rank(text: str) -> int:
    t = str(text or "").lower()
    best = 0
    for k, v in DEGREE_RANK.items():
        rx = (r"\bph\.?\s?d\b|doctorate" if k == "phd" else r"\b" + k + r"\b")
        if re.search(rx, t):
            best = max(best, v)
    if re.search(r"\bb\.?tech\b|\bb\.?e\b|\bb\.?sc\b|\bbachelor", t):
        best = max(best, 3)
    if re.search(r"\bm\.?tech\b|\bm\.?sc\b|\bmba\b|\bmaster", t):
        best = max(best, 4)
    return best


def experience_fit(years: float, min_years: float) -> float:
    """Sharp drop below the minimum, plateau at/above, slight decline far above
    (over-qualification). Neutral-high when the JD states no minimum."""
    if not min_years or min_years <= 0:
        return 0.85
    r = years / min_years
    if r < 1:
        return clamp(0.3 + 0.6 * r, 0, 0.9)      # below min: steep but not zero
    if r <= 2.5:
        return 1.0                                # plateau
    return clamp(1 - (r - 2.5) * 0.1, 0.75, 1)    # far above: mild decline


QUANT_RE = re.compile(
    r"\d+(\.\d+)?\s?%|[₹$€£]\s?\d|\b\d+(\.\d+)?\s?(k|m|bn|mn|cr|lakh|crore)\b"
    r"|\b(reduced|increased|grew|improved|cut|saved|drove|boosted|scaled)\b", re.I)
JD_IMPACT_RE = re.compile(
    r"\b(impact|outcome|metric|kpi|revenue|growth|conversion|retention|cost"
    r"|roi|efficiency|reduce|increase)\b", re.I)


def achievement_score(experience: list[dict], jd_text: str) -> float:
    """Quantified-outcome density, lifted when the JD itself stresses
    measurable impact."""
    total = quant = 0
    for role in (experience or []):
        for b in (role.get("bullets") or []):
            total += 1
            if QUANT_RE.search(b):
                quant += 1
    if not total:
        return 0.5
    density = quant / total
    jd_stress = 1.25 if JD_IMPACT_RE.search(str(jd_text or "")) else 1
    return clamp(density * 2 * jd_stress, 0, 1)   # ~50% quantified -> full credit


SENIORITY = [
    (re.compile(r"\b(intern|trainee)\b", re.I), 1),
    (re.compile(r"\b(associate|junior|analyst|coordinator)\b", re.I), 2),
    (re.compile(r"\b(manager|lead|specialist|senior|sr\.?)\b", re.I), 3),
    (re.compile(r"\b(principal|staff|head)\b", re.I), 4),
    (re.compile(r"\b(director|vp|vice president|chief|c[toe]o|founder)\b", re.I), 5),
]


def seniority_rank(title: str) -> int:
    best = 2
    t = str(title or "")
    for rx, rank in SENIORITY:
        if rx.search(t):
            best = max(best, rank)
    return best


def trajectory_score(experience: list[dict]) -> float:
    """Seniority progression across roles (CV order is reverse-chronological).
    Deliberately the smallest-weighted sub-score — a non-linear career is not a
    defect, and the fairness checks in smoke_test.py guard that."""
    roles = [e for e in (experience or []) if e.get("title")]
    if len(roles) < 2:
        return 0.7                                  # too little to judge
    ranks = [seniority_rank(e["title"]) for e in reversed(roles)]  # oldest first
    ups = downs = 0
    for i in range(1, len(ranks)):
        if ranks[i] > ranks[i - 1]:
            ups += 1
        elif ranks[i] < ranks[i - 1]:
            downs += 1
    if ups > 0 and downs == 0:
        return 1.0                                  # monotonic growth
    if ups >= downs:
        return 0.8
    return 0.55                                     # net regression


def aggregate_score(jd: dict, parsed_cv: dict, sm: dict, weights=None,
                    domain_score=None, jd_text=None, cv_text=None,
                    domain_keywords=None, company_tier=None) -> dict:
    """Blend the sub-scores into a structured 0-100 score with penalties,
    eligibility gates and flags — every component reported, never opaque."""
    jd = jd or {}
    parsed_cv = parsed_cv or {}
    sm = sm or {}
    w = dict(DEFAULT_WEIGHTS)
    w.update(weights or {})
    jd_text = jd_text if jd_text is not None else (jd.get("jd_text") or "")

    # domain sub-score: reuse the frozen engine's additive bonus if not supplied.
    if domain_score is None:
        if cv_text:
            mm = compute_match(jd_text, cv_text, domain_keywords=domain_keywords)
            domain_score = clamp(mm["bonus"] / 20, 0, 1)
        else:
            domain_score = 0

    # Employer-identity floor/cap (2026-08-18, see company_industry.py). The
    # JD-prose bonus above only catches domain KEYWORDS in the posting text;
    # it misses a genuinely core-domain employer whose JD never happens to
    # say "fintech", and it over-credits a staffing JD that quotes a client's
    # industry. `company_tier=None`/"unknown" is a no-op — identical to
    # pre-feature scoring for every company not classified.
    if company_tier:
        domain_score = company_industry.apply_domain_floor_cap(
            domain_score, company_tier)

    sub = {
        "skill_match": clamp(sm.get("skill_score") or 0, 0, 1),
        "experience_fit": experience_fit(parsed_cv.get("total_years") or 0,
                                         jd.get("min_years") or 0),
        "domain": clamp(domain_score, 0, 1),
        "education": 1.0,                 # adjusted by the gate below
        "achievement": achievement_score(parsed_cv.get("experience"), jd_text),
        "trajectory": trajectory_score(parsed_cv.get("experience")),
    }

    # Education: neutral unless the JD makes a degree mandatory AND we can
    # check it. A checkable shortfall both lowers the sub-score and arms the
    # hard gate.
    gates = {"eligibility": [], "failed_checkable": False}
    flags = []
    req_degree = degree_rank(jd.get("education_level"))
    cv_degree = degree_rank((parsed_cv.get("sections") or {}).get("education", ""))
    if req_degree > 0:
        if cv_degree >= req_degree:
            sub["education"] = 1.0
        else:
            sub["education"] = 0.3
            gates["failed_checkable"] = True
            gates["eligibility"].append(
                "degree: JD requires a higher level than the CV shows")

    # Other mandatory eligibility (visa/licence/clearance) can't be verified
    # from a CV — flag for human review, never auto-fail.
    for e in (jd.get("mandatory_eligibility") or []):
        if re.search(r"degree|bachelor|master|mba|diploma", str(e), re.I):
            continue                                  # handled above
        gates["eligibility"].append(e)
        flags.append(f"Unverifiable mandatory gate — confirm manually: {e}")

    raw = sum(sub.get(k, 0) * w[k] for k in w)
    wsum = sum(w.values())
    base = raw / wsum if wsum > 0 else 0

    penalties = []
    penalty = 0.0
    unexplained = parsed_cv.get("unexplained_gaps") or []
    if unexplained:
        gap_months = sum(g["months"] for g in unexplained)
        p = clamp(gap_months / 12 * 0.03, 0, 0.1)
        penalty += p
        penalties.append({
            "reason": f"{len(unexplained)} unexplained gap(s), {gap_months} months",
            "amount": round(p * 100) / 100})
    vm = sm.get("verbatim_mirror") or {}
    if vm.get("flagged"):
        penalty += 0.05
        penalties.append({
            "reason": "CV mirrors the JD's phrasing verbatim (possible stuffing)",
            "amount": 0.05})

    score01 = clamp(base - penalty, 0, 1)
    score = js_round(score01 * 100)

    # Hard cap on a failed CHECKABLE mandatory gate — ineligibility isn't
    # outscored by skill overlap.
    if gates["failed_checkable"] and score > HARD_CAP:
        score = HARD_CAP
        flags.append("Hard-capped: a checkable mandatory requirement is unmet")

    return {
        "score": score,
        "sub_scores": sub,
        "weights": w,
        "penalties": penalties,
        "penalty_total": round(penalty * 100) / 100,
        "gates": gates,
        "flags": flags,
        "must_coverage": sm.get("must_coverage") or {"hit": 0, "total": 0},
        "preferred_coverage": sm.get("preferred_coverage") or {"hit": 0, "total": 0},
    }


def reconcile_scores(structured_score, holistic_score, tolerance=10) -> dict:
    """Reconcile the structured score with a blind holistic read. Agreement
    within tolerance -> report the blend. Sharp divergence -> surface BOTH
    numbers with the flag, never hide it: divergence usually means either the
    structured layer was gamed by a keyword-dense-but-hollow CV, or the
    holistic read over-credited a narrative lacking concrete evidence. The
    disagreement itself is the most valuable signal in the system."""
    try:
        s, h = float(structured_score), float(holistic_score)
    except (TypeError, ValueError):
        return {"mode": "invalid", "blended": None, "delta": None}
    delta = js_round(abs(s - h))
    if delta <= tolerance:
        return {"mode": "agree", "blended": js_round((s + h) / 2), "delta": delta,
                "note": "Structured and holistic reads agree — high confidence."}
    return {
        "mode": "diverge", "blended": None, "delta": delta,
        "structured": js_round(s), "holistic": js_round(h),
        "note": ("Structured scores higher than the holistic read — keyword "
                 "coverage may be flattering a thinner reality. Trust the "
                 "holistic concerns.") if s > h else
                ("Holistic reads higher than the structured score — the "
                 "narrative is stronger than the keyword overlap. The "
                 "structured gaps may be closable by wording."),
    }
