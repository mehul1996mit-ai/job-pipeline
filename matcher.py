"""Config-driven filtering + ATS-style scoring against the FULL CV text.

Filters: title keyword allowlist, city allowlist ("remote" always passes),
experience band overlap, optional min salary (enforced only when the listing
reports one). Domain keywords are a scoring BONUS only, never a filter.

SCORING (ported from cv-match-copilot, 2026-07-28). Three layers, all
deterministic, so every job gets all three at zero API cost:

  legacy_score    — the original flat word-overlap score. Kept ONLY so the two
                    formulas can be compared on real queues while the
                    min_score_to_tailor floor is recalibrated. Not used for
                    ranking.
  frozen_score    — the ported frozen engine (stemmer + synonym folding,
                    requirement lines weighted x2, bigrams as separate
                    competencies, per-term cap, sqrt curve, domain bonus).
  structured      — the multi-sub-score layer: skill match by evidence tier,
                    experience fit, education gate, domain, achievement
                    density, trajectory; minus penalties; plus a percentile
                    calibrated against how demanding the posting itself is.

`score_job()` returns all of them. The structured score is the one worth
ranking on — it is the only one that knows the difference between a must-have
you can evidence in a bullet and a preferred keyword you merely listed.
"""
from __future__ import annotations

import re
from collections import Counter

import calibrate
import company_industry
import jd_analyst
import seniority
from aggregate import aggregate_score
from cv_parser import keyword_set, tokenize, NOISE_WORDS
from scoring_core import compute_match
from skill_match import structured_skill_match

# "3-5 years", "3 to 5 yrs", "3–5 years"
RANGE_RE = re.compile(
    r"(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
# "5+ years", "5 + yrs", "minimum 5 years", "at least 5 years"
PLUS_RE = re.compile(
    r"(?:minimum|min\.?|at least)?\s*(\d{1,2})\s*\+\s*(?:years?|yrs?)"
    r"|(?:minimum|min\.?|at least)\s+(\d{1,2})\s*(?:years?|yrs?)", re.I)


def clamp01(x: float) -> float:
    """Clamp a configured 0..1 factor. A typo'd 25 in config.yaml (the old
    flat-penalty value) must not multiply a score 25x -- it clamps to 1.0,
    i.e. no penalty, which is the safe direction to fail."""
    return max(0.0, min(1.0, float(x)))


def parse_experience_band(jd_text: str):
    """Return (min_years, max_years) required by the JD, or None if the JD
    states no requirement."""
    m = RANGE_RE.search(jd_text or "")
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return (min(lo, hi), max(lo, hi))
    m = PLUS_RE.search(jd_text or "")
    if m:
        lo = int(m.group(1) or m.group(2))
        return (lo, 99)
    return None


def experience_ok(jd_text: str, my_years: float) -> bool:
    """Pass when the JD states no requirement, or when my experience is
    within/overlapping the stated band (small tolerance of 1 year below)."""
    band = parse_experience_band(jd_text)
    if band is None:
        return True
    lo, hi = band
    return (lo - 1) <= my_years <= (hi + 2)


def title_ok(title: str, allowlist: list[str]) -> bool:
    t = (title or "").lower()
    return any(kw.lower() in t for kw in allowlist)


_REMOTE_FILLER_RE = re.compile(
    r"\bremote\b|\bwork from home\b|\bwfh\b|[,\-–—/;()]", re.I)


def city_ok(location: str, cities: list[str]) -> bool:
    """"Remote" only passes when it's genuinely open. A global ATS board
    (Greenhouse/Lever/SmartRecruiters/Ashby, added 2026-07-28) posts plenty of
    "Chicago, IL, Remote" or "US-Remote" roles, and treating every "remote"
    string as open let those through even though they're scoped to a specific
    country or city Mehul can't work from.

    Rather than list every country/region name (a losing game — it missed
    bare US city names like "Chicago" and "Seattle" entirely), strip the
    filler words and separators and see what's left. "Remote" or "Remote,
    India" reduces to nothing (or "india") — genuinely open. "Chicago, IL,
    Remote" reduces to "chicago il" — that leftover location detail means the
    remote scope is restricted elsewhere, so it's excluded.
    """
    if not cities:
        return True
    loc = (location or "").lower()
    if "remote" in loc:
        if "india" in loc:
            return True
        residual = _REMOTE_FILLER_RE.sub(" ", loc)
        residual = re.sub(r"\s+", " ", residual).strip()
        return not residual
    return any(c.lower() in loc for c in cities)


def salary_ok(job: dict, min_salary_annual) -> bool:
    """Enforce ONLY when the listing reports a salary."""
    if not min_salary_annual:
        return True
    reported = job.get("salary_max") or job.get("salary_min")
    if not reported:
        return True
    return float(reported) >= float(min_salary_annual)


def passes_filters(job: dict, config: dict) -> bool:
    """Experience deliberately is NOT checked here. seniority.judge() (wired
    into matcher.score_job(), called downstream of this filter) applies
    over-seniority as a configurable penalty to the FINAL score instead —
    per the 2026-08-09 redesign documented in CLAUDE.md, so an affected job
    still reaches the CSV with its verdict rather than vanishing before
    scoring ever runs. experience_ok()/parse_experience_band() are kept
    (and still smoke-tested directly) as the band-parsing primitives
    seniority.py builds on, just no longer used as a hard pre-filter here."""
    f = config.get("filters", {})
    if f.get("remote_only"):
        loc = (job.get("location", "") + " " + job.get("title", "")).lower()
        if "remote" not in loc and "work from home" not in loc:
            return False
    return (
        title_ok(job.get("title", ""), f.get("title_keywords", []))
        and city_ok(job.get("location", ""), f.get("cities", []))
        and salary_ok(job, f.get("min_salary_annual"))
    )


def ats_score(jd_text: str, cv_keywords: set[str], config: dict) -> int:
    """LEGACY 0-100 scorer: flat JD-vs-CV word overlap (scaled to 80) + up to
    20 domain bonus.

    Superseded by frozen_score/score_job — it has no stemming, no synonym
    folding, treats every JD word as equally important, and counts a bigram
    competency as two unrelated words. Retained so the old and new formulas can
    be compared on real job queues while the tailoring floor is recalibrated;
    remove once that's settled.
    """
    jd_kw = keyword_set(jd_text)
    if not jd_kw:
        return 0
    overlap = len(jd_kw & cv_keywords) / len(jd_kw)
    base = min(80.0, overlap * 160)  # 50% overlap already maxes the base

    domain = [k.lower() for k in
              config.get("scoring", {}).get("domain_keywords", [])]
    jd_lower = (jd_text or "").lower()
    hits = sum(1 for k in domain if k in jd_lower)
    bonus = min(20.0, hits * (20.0 / max(len(domain) * 0.5, 1)))
    return int(round(min(100.0, base + bonus)))


def _domain_keywords(config: dict):
    return config.get("scoring", {}).get("domain_keywords") or None


def frozen_score(jd_text: str, cv_text: str, config: dict, cv_index=None) -> dict:
    """The ported frozen engine. Full result dict, not just the number — the
    matched/missing term lists explain the score."""
    return compute_match(jd_text, cv_text,
                         domain_keywords=_domain_keywords(config), cv_index=cv_index)


def _experience_verdict(title: str, jd_text: str, config: dict) -> dict:
    """Seniority read for one posting (see seniority.py for the why).

    Kept here rather than inside aggregate.py on purpose: this is a FILTERING
    concern about whether the role is aimed at someone of your level, not a
    measure of how well your CV matches its content. Folding it into the six
    sub-scores would let a genuinely strong skill match quietly cancel out
    "this role wants 12+ years", which is exactly the failure being fixed.
    """
    prof = config.get("profile", {}) or {}
    band = seniority.extract_experience(title, jd_text)
    verdict = seniority.judge(
        band,
        my_years=float(prof.get("experience_years", 0) or 0),
        comfort_max=float(prof.get("comfort_max_years", 8) or 8),
        stretch=float(prof.get("stretch_years", 2) or 2),
    )
    return {
        "exp_min_years": band["min_years"],
        "exp_max_years": band["max_years"],
        "exp_confidence": band["confidence"],
        "exp_evidence": band["evidence"],
        "seniority_tier": band["seniority"],
        "exp_verdict": verdict["verdict"],
        "exp_why": verdict["why"],
    }


def score_job(jd_text: str, cv_text: str, structured_cv: dict, config: dict,
              llm_analysis: dict | None = None, title: str = "",
              cv_index: dict | None = None, skill_cv_index: dict | None = None,
              skill_cv_lower: str | None = None, company: str = "",
              source: str = "") -> dict:
    """Score one posting through every layer. Fully deterministic unless an
    `llm_analysis` is supplied (from the tailoring call, which already happens
    for the top-N jobs) — so this is safe to run on every listing.

    `cv_index`/`skill_cv_index`/`skill_cv_lower`: pre-computed CV indices
    (scoring_core.index_text / skill_match.index_layers + lowercased text)
    for the two scoring layers below. The candidate's CV is constant for the
    whole run, but this function is called up to 3x per job listing (initial
    pass, Workday full-JD rescore, post-tailoring rescore) — main.py computes
    these once and threads them through instead of re-tokenizing the same CV
    hundreds/thousands of times a day. Optional: falls back to recomputing
    when not supplied, so existing callers are unaffected.

    `company`/`source`: optional employer identity (see company_industry.py,
    2026-08-18) — floors/caps the `domain` sub-score by employer industry
    tier (fintech/NBFC core vs. IT-services cap), on top of the existing
    JD-keyword domain bonus. Both optional: an unclassified/blank company is
    "unknown", which applies no floor and no cap — identical to scoring
    before this feature existed.

    Returns a flat dict suitable for stashing on the job record and writing to
    the CSV, plus the nested structures the dashboard can expand.
    """
    analysis = jd_analyst.analyze_jd(jd_text)
    if llm_analysis:
        analysis = jd_analyst.merge_llm_analysis(analysis, llm_analysis)

    frozen = frozen_score(jd_text, cv_text, config, cv_index=cv_index)
    sm = structured_skill_match(analysis, structured_cv,
                                cv_index=skill_cv_index, cv_lower=skill_cv_lower)

    company_verdict = company_industry.classify(company, jd_text, source)

    agg_kwargs = {
        "jd_text": jd_text,
        "cv_text": cv_text,
        "domain_keywords": _domain_keywords(config),
        "weights": config.get("scoring", {}).get("weights") or None,
        "company_tier": company_verdict["tier"],
    }
    agg = aggregate_score(analysis, structured_cv, sm, **agg_kwargs)
    gaps = calibrate.counterfactual_gaps(analysis, structured_cv, sm,
                                         agg_kwargs=agg_kwargs, limit=5)

    # Seniority penalty. Applied to the FINAL score rather than as a seventh
    # sub-score so it cannot be diluted by the weighting: a role aimed two
    # levels above you is not partially disqualifying. Deliberately a penalty
    # and not a filter — the row still reaches the CSV with its verdict, but
    # sinks below the digest top-N and below min_score_to_tailor, so it stops
    # consuming tailoring calls. See config.yaml profile.over_senior_factor.
    # The penalty is SCALED BY CONFIDENCE (2026-08-19). Measured over 747 real
    # August rows, 49% of over_senior verdicts came from an `inferred` band —
    # a guess from title wording with no number stated anywhere in the posting.
    # Applying the full penalty to a guess was actively harmful: on the
    # 2026-07-28 queue it knocked "Senior Manager FI Partnerships" 55 -> 30,
    # "KAM | Senior Manager | Banking Alliances" 48 -> 23 and CRED's
    # "Business Development & Partnerships" 58 -> 33 — i.e. precisely the
    # BFSI partnership roles this search exists to find, where "Senior
    # Manager" is routinely a 5-8y grade well within reach. A stated number
    # still earns the full penalty; a guess only nudges.
    # MULTIPLICATIVE, not a flat subtraction (changed 2026-08-19). The old
    # flat -25 was calibrated against the pre-rebalance 42..100 score range;
    # once the CV-constant sub-scores left the blend and the real range became
    # ~0..61, that same -25 stopped demoting over-senior jobs and started
    # ANNIHILATING them — 6 rows in a 323-row replay landed on exactly 0,
    # losing the ordering among them entirely. A proportional factor demotes
    # identically at any scale, so a future reweighting cannot silently turn
    # this back into a delete. It also can never produce a negative score,
    # which is why the max(0, ...) clamp is no longer load-bearing.
    exp = _experience_verdict(title or jd_text[:120], jd_text, config)
    score = agg["score"]
    if exp["exp_verdict"] == "over_senior":
        prof = config.get("profile", {}) or {}
        if exp["exp_confidence"] == "inferred":
            factor = float(prof.get("over_senior_factor_inferred", 0.85) or 0.85)
        else:
            factor = float(prof.get("over_senior_factor", 0.5) or 0.5)
        score = max(0, round(score * clamp01(factor)))

    # Calibrated AFTER the penalty (2026-08-10 fix), not before. This was a
    # real, reported bug: a job penalised to 34 for being over-senior still
    # showed "64th percentile / competitive" because percentile was computed
    # off the pre-penalty score — actively misleading, since "competitive"
    # invites exactly the wrong conclusion about a role you're structurally
    # not positioned for. percentile/band/note now all describe the SAME
    # number as `score`, so the calibration block can never contradict it.
    cal = calibrate.calibrate_score(score, analysis)

    return {
        "score": score,                        # structured — rank on this
        "score_before_seniority": agg["score"],
        "company_tier": company_verdict["tier"],
        "company_basis": company_verdict["basis"],
        "company_evidence": company_verdict["evidence"],
        **exp,
        "frozen_score": frozen["score"],
        "percentile": cal.get("percentile"),
        "band": cal.get("band"),
        "jd_difficulty": cal.get("difficulty"),
        "analyst": analysis.get("analyst"),
        "sub_scores": agg["sub_scores"],
        "must_coverage": agg["must_coverage"],
        "preferred_coverage": agg["preferred_coverage"],
        "penalties": agg["penalties"],
        "gates": agg["gates"],
        "flags": agg["flags"],
        "missing_must": [m["skill"] for m in sm["missing"] if m["tier"] == "must"],
        "top_gaps": gaps["gaps"],
        "matched_skills": [m["skill"] for m in sm["matched"][:12]],
        "analysis": analysis,
        "skill_match": sm,
        "aggregate": agg,
        "calibration": cal,
    }


def matched_keywords(jd_text: str, cv_keywords: set[str], top_n: int = 12):
    """Top JD keywords MISSING from the CV (noise-filtered, freq-ranked) —
    input for the tailoring step."""
    counts = Counter(t.strip("./-") for t in tokenize(jd_text)
                     if t.strip("./-") not in NOISE_WORDS
                     and len(t.strip("./-")) > 3)
    missing = [(kw, n) for kw, n in counts.most_common(100)
               if kw not in cv_keywords]
    return [kw for kw, _ in missing[:top_n]]
