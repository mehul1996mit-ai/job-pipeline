"""Config-driven filtering + ATS-style scoring against the FULL CV text.

Filters: title keyword allowlist, city allowlist ("remote" always passes),
experience band overlap, optional min salary (enforced only when the listing
reports one). Domain keywords are a scoring BONUS only, never a filter.
"""
from __future__ import annotations

import re
from collections import Counter

from cv_parser import keyword_set, tokenize, NOISE_WORDS

# "3-5 years", "3 to 5 yrs", "3–5 years"
RANGE_RE = re.compile(
    r"(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
# "5+ years", "5 + yrs", "minimum 5 years", "at least 5 years"
PLUS_RE = re.compile(
    r"(?:minimum|min\.?|at least)?\s*(\d{1,2})\s*\+\s*(?:years?|yrs?)"
    r"|(?:minimum|min\.?|at least)\s+(\d{1,2})\s*(?:years?|yrs?)", re.I)


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


def city_ok(location: str, cities: list[str]) -> bool:
    if not cities:
        return True
    loc = (location or "").lower()
    if "remote" in loc:
        return True
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
    f = config.get("filters", {})
    my_years = float(config.get("profile", {}).get("experience_years", 0))
    return (
        title_ok(job.get("title", ""), f.get("title_keywords", []))
        and city_ok(job.get("location", ""), f.get("cities", []))
        and experience_ok(job.get("description", ""), my_years)
        and salary_ok(job, f.get("min_salary_annual"))
    )


def ats_score(jd_text: str, cv_keywords: set[str], config: dict) -> int:
    """0-100: JD-vs-CV word overlap (scaled to 80) + up to 20 domain bonus."""
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


def matched_keywords(jd_text: str, cv_keywords: set[str], top_n: int = 12):
    """Top JD keywords MISSING from the CV (noise-filtered, freq-ranked) —
    input for the tailoring step."""
    counts = Counter(t.strip("./-") for t in tokenize(jd_text)
                     if t.strip("./-") not in NOISE_WORDS
                     and len(t.strip("./-")) > 3)
    missing = [(kw, n) for kw, n in counts.most_common(100)
               if kw not in cv_keywords]
    return [kw for kw, _ in missing[:top_n]]
