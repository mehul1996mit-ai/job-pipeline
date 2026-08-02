"""Percentile calibration, counterfactual gaps and explainability — ported
from cv-match-copilot's lib/calibrate.js.

PERCENTILE NORMALIZATION. A raw 75 against a narrow senior role and a raw 75
  against a broad generic one are not the same thing. This calibrates the score
  against that specific JD's requirement-profile difficulty and reports a
  percentile-of-fit that IS comparable across postings. The raw score is never
  overwritten — both are reported.

COUNTERFACTUAL GAP ANALYSIS. For every unmet requirement, re-run the
  aggregation with that one requirement satisfied and report the delta. Ranking
  by IMPACT (not by how often the JD repeats a word) is the whole point:
  "closing this moves you 62 -> 78" is actionable; a flat list of missing terms
  is not.

EXPLAINABILITY. The full decomposition: must/preferred hit+missed, per-sub-score
  point contributions, domain breakdown, gates, and REAL QUOTED CV lines per
  matched claim — never generated. An unevidenced claim is reported as such.
"""
from __future__ import annotations

import math

from aggregate import aggregate_score, degree_rank, reconcile_scores
from scoring_core import js_round


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def normal_cdf(z: float) -> float:
    # The JS port hand-rolled Abramowitz-Stegun 7.1.26 to stay dependency-free;
    # Python has math.erf in the stdlib, which is strictly more accurate. The
    # difference is far below the rounding to an integer percentile.
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# Difficulty factors, each normalized 0..1 and reported individually so the
# number is never a black box.
DIFFICULTY_WEIGHTS = {
    "must_count": 0.30,    # how many hard requirements there are
    "seniority": 0.25,     # minimum years demanded
    "gates": 0.20,         # mandatory eligibility gates (visa/licence/cert)
    "narrowness": 0.15,    # share of requirements framed as must vs preferred
    "education": 0.10,     # degree floor
}

# Applicant-pool model. A harder posting pushes the typical applicant's score
# DOWN, so the same raw score sits at a higher percentile. These constants are a
# deliberate, documented modelling choice, NOT measured data — the honest
# framing is "comparable across postings", never "you beat 82% of real
# applicants", and calibrate_score says so in its own note.
POOL_MEAN_EASY, POOL_MEAN_HARD, POOL_SD = 60, 30, 16

ASSUMED_MULT = 0.9   # a newly-satisfied requirement is realistically alias-layer


def jd_difficulty(jd: dict) -> dict:
    """How demanding is this posting's requirement profile, independent of any
    candidate? 0 = a broad generic listing, 1 = a narrow senior gated role."""
    jd = jd or {}
    must = len(jd.get("must_have_skills") or [])
    pref = len(jd.get("preferred_skills") or [])
    gates = len(jd.get("mandatory_eligibility") or [])
    try:
        min_y = float(jd.get("min_years") or 0)
    except (TypeError, ValueError):
        min_y = 0
    deg = degree_rank(jd.get("education_level"))

    f = {
        "must_count": clamp(must / 8, 0, 1),
        "seniority": clamp(min_y / 10, 0, 1),
        "gates": clamp(gates / 3, 0, 1),
        # A posting calling everything mandatory is stricter than one that
        # splits must/preferred. No requirements at all -> neutral 0.5, not 0:
        # an unclassified posting is unknown, not easy.
        "narrowness": (must / (must + pref)) if (must + pref) > 0 else 0.5,
        "education": clamp(deg / 5, 0, 1),
    }
    wsum = sum(DIFFICULTY_WEIGHTS.values())
    d = sum(f[k] * DIFFICULTY_WEIGHTS[k] for k in DIFFICULTY_WEIGHTS)
    return {"difficulty": round(d / wsum * 100) / 100, "factors": f,
            "weights": DIFFICULTY_WEIGHTS}


def calibrate_score(score, jd: dict) -> dict:
    """Map a raw 0-100 score onto a percentile through the applicant-pool
    model, so scores are comparable across postings of different difficulty."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return {"percentile": None, "difficulty": None, "raw": None,
                "note": "No score to calibrate."}
    if not math.isfinite(s):
        return {"percentile": None, "difficulty": None, "raw": None,
                "note": "No score to calibrate."}

    d = jd_difficulty(jd)
    mu = POOL_MEAN_EASY - (POOL_MEAN_EASY - POOL_MEAN_HARD) * d["difficulty"]
    pct = clamp(js_round(normal_cdf((s - mu) / POOL_SD) * 100), 1, 99)
    band = ("strong" if pct >= 85 else "competitive" if pct >= 60
            else "borderline" if pct >= 35 else "weak")
    harder = d["difficulty"] >= 0.55
    return {
        "raw": js_round(s),
        "percentile": pct,
        "difficulty": d["difficulty"],
        "factors": d["factors"],
        "pool_mean": js_round(mu),
        "band": band,
        "note": (
            f"Calibrated against this posting's requirement profile (difficulty "
            f"{d['difficulty']}{', demanding' if harder else ', broad'}). A raw "
            f"{js_round(s)} here reads as {band} — the same raw score on a "
            f"{'broader' if harder else 'narrower'} posting would read "
            f"{'weaker' if harder else 'stronger'}. Modelled comparability, "
            f"not measured applicant data."),
    }


def _patch_sm_for_skill(sm: dict, gap: dict) -> dict:
    """What would the skill match look like with this one gap closed? Not a
    perfect match — a skill you just acquired is realistically alias-layer with
    shallow evidence, so ASSUMED_MULT mirrors the alias confidence."""
    tier_weight = 3 if gap["tier"] == "must" else 1 if gap["tier"] == "preferred" else 1.5
    total = sm.get("total_weight") or 0
    hit = (sm.get("hit_weight") or 0) + tier_weight * ASSUMED_MULT
    patched = dict(sm)
    patched["hit_weight"] = hit
    patched["skill_score"] = (clamp(hit / total, 0, 1) if total > 0
                              else sm.get("skill_score") or 0)
    patched["must_coverage"] = dict(sm.get("must_coverage") or {})
    patched["preferred_coverage"] = dict(sm.get("preferred_coverage") or {})
    if gap["tier"] == "must":
        patched["must_coverage"]["hit"] = (patched["must_coverage"].get("hit") or 0) + 1
    if gap["tier"] == "preferred":
        patched["preferred_coverage"]["hit"] = (patched["preferred_coverage"].get("hit") or 0) + 1
    return patched


def counterfactual_gaps(jd: dict, parsed_cv: dict, sm: dict, agg_kwargs=None,
                        limit: int = 8) -> dict:
    """For every unmet requirement, what would the score become if it were met?
    Covers skill, experience and education gaps — all real blockers the
    aggregation already models. Ranked by impact. Never invents a requirement
    the JD didn't state."""
    jd = jd or {}
    parsed_cv = parsed_cv or {}
    sm = sm or {}
    agg_kwargs = agg_kwargs or {}

    base = aggregate_score(jd, parsed_cv, sm, **agg_kwargs)
    baseline = base["score"]
    out = []

    for gap in (sm.get("missing") or []):
        r = aggregate_score(jd, parsed_cv, _patch_sm_for_skill(sm, gap), **agg_kwargs)
        out.append({"kind": "skill", "requirement": gap["skill"],
                    "tier": gap["tier"], "from": baseline, "to": r["score"],
                    "delta": r["score"] - baseline})

    # Experience shortfall — worth however many points the threshold curve
    # gives back at exactly the stated minimum, not more. Kept OUT of `out`
    # (the ranked "close these first" list) — mirrors lib/calibrate.js, fixed
    # there 2026-08-01 on owner report: phrased identically to a skill gap
    # ("+6 ... would move you 56 -> 62"), this reads as an actionable to-do,
    # but tenure isn't something you can add before applying. Reported
    # separately in `not_actionable` instead.
    not_actionable = []
    try:
        min_y = float(jd.get("min_years") or 0)
    except (TypeError, ValueError):
        min_y = 0
    have = float(parsed_cv.get("total_years") or 0)
    if min_y > 0 and have < min_y:
        cv_exp = dict(parsed_cv)
        cv_exp["total_years"] = min_y
        re_ = aggregate_score(jd, cv_exp, sm, **agg_kwargs)
        gap = re_["score"] - baseline
        if gap > 0:
            not_actionable.append({
                "kind": "experience",
                "requirement": f"{min_y:g} years of experience (you show "
                               f"{round(have * 10) / 10:g})",
                "tier": "must", "from": baseline, "to": re_["score"],
                "delta": gap})

    # Checkable education gate — this also lifts the hard cap, so its delta is
    # usually the largest single number. That is correct: an ineligibility is
    # not a keyword gap.
    if base.get("gates", {}).get("failed_checkable"):
        sections = dict(parsed_cv.get("sections") or {})
        sections["education"] = ((sections.get("education") or "") + "\n"
                                 + str(jd.get("education_level") or "degree"))
        cv_edu = dict(parsed_cv)
        cv_edu["sections"] = sections
        red = aggregate_score(jd, cv_edu, sm, **agg_kwargs)
        out.append({
            "kind": "education",
            "requirement": str(jd.get("education_level") or "the required degree"),
            "tier": "must", "from": baseline, "to": red["score"],
            "delta": red["score"] - baseline})

    out.sort(key=lambda g: (-g["delta"], 0 if g["tier"] == "must" else 1))
    ranked = [g for g in out if g["delta"] > 0][:limit]
    return {
        "baseline": baseline,
        "gaps": ranked,
        "not_actionable": not_actionable,
        "zero_impact": [g["requirement"] for g in out if g["delta"] <= 0],
        "note": ("Ranked by score impact, not by how often the posting repeats "
                 "the word." if ranked else
                 "No unmet requirement would measurably move the score."),
    }


def evidence_for(skill: str, parsed_cv: dict, max_lines: int = 2) -> list[dict]:
    """Real quoted CV lines evidencing a matched skill. Never generated — if
    the skill matched at stem/alias layer with no line containing the surface
    form, the caller reports that honestly instead of showing empty evidence
    as if it were proof."""
    kl = str(skill or "").lower()
    lines = []
    for role in (parsed_cv.get("experience") or []):
        for b in (role.get("bullets") or []):
            if len(lines) >= max_lines:
                break
            if kl in str(b).lower():
                where = " @ ".join(x for x in [role.get("title"),
                                               role.get("company")] if x)
                lines.append({"where": where or "experience",
                              "line": str(b)[:220]})
    if len(lines) < max_lines:
        decl = (parsed_cv.get("skills") or {}).get("declared") or []
        if any(str(d).lower() == kl for d in decl):
            lines.append({"where": "skills section",
                          "line": f"{skill} (declared, no bullet evidence)"})
    return lines


def explain_score(agg: dict, sm: dict, parsed_cv: dict,
                  holistic_score=None) -> dict:
    """Full decomposition — every number traces back to a listed component."""
    agg = agg or {}
    sm = sm or {}
    parsed_cv = parsed_cv or {}

    def by_tier(lst, tier, want_matched):
        rows = []
        for x in (lst or []):
            if x.get("tier") != tier:
                continue
            row = {"skill": x["skill"], "tier": tier}
            if want_matched:
                row["layer"] = x.get("layer")
                row["source"] = x.get("source")
                row["weight"] = x.get("weight")
                row["evidence"] = evidence_for(x["skill"], parsed_cv)
                if not row["evidence"]:
                    row["evidence_note"] = (
                        f"matched by {x.get('layer')}, no verbatim line in the CV")
            rows.append(row)
        return rows

    contributions = sorted(
        ({"key": k,
          "value": js_round((agg.get("sub_scores") or {}).get(k, 0) * 100),
          "weight": w,
          "points": js_round((agg.get("sub_scores") or {}).get(k, 0) * w * 100)}
         for k, w in (agg.get("weights") or {}).items()),
        key=lambda c: -c["points"])

    agreement = (reconcile_scores(agg.get("score"), holistic_score)
                 if holistic_score is not None else None)

    return {
        "score": agg.get("score"),
        "must_hit": by_tier(sm.get("matched"), "must", True),
        "must_missed": by_tier(sm.get("missing"), "must", False),
        "preferred_hit": by_tier(sm.get("matched"), "preferred", True),
        "preferred_missed": by_tier(sm.get("missing"), "preferred", False),
        "other_hit": by_tier(sm.get("matched"), "unknown", True),
        "contributions": contributions,
        "domain": {
            "value": js_round((agg.get("sub_scores") or {}).get("domain", 0) * 100),
            "weight": (agg.get("weights") or {}).get("domain"),
            "note": "Domain-keyword overlap. Additive bonus only — never a filter.",
        },
        "penalties": agg.get("penalties") or [],
        "gates": agg.get("gates") or {"eligibility": [], "failed_checkable": False},
        "flags": agg.get("flags") or [],
        "agreement": agreement,
    }
