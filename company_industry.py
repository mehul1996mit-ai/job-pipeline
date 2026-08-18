"""Employer-identity classification for scoring — see CLAUDE.md 2026-08-18.

WHY THIS EXISTS. aggregate.py's `domain` sub-score already exists (weight
0.15, config.yaml scoring.domain_keywords), but it only ever reads JD PROSE
via scoring_core.compute_match(jd_text, cv_text). Nothing anywhere reads WHO
the employer is. Two real failure modes that fixes:
  - false negative: a Razorpay JD about "merchant onboarding funnel
    ownership" never uses the word fintech -> domain score ~0 for the single
    most relevant employer category in this market.
  - false positive: an IT-services JD ("our client, a leading NBFC, is
    building...") hits several domain keywords and earns the same bonus as
    an actual NBFC employer, scored on the CLIENT's industry at a staffing
    agency.

DESIGN. A small deterministic module, same shape as seniority.py: no LLM
call, every verdict carries its own evidence/basis rather than a bare tier,
so a wrong classification is visible on the CSV row, not hidden inside a
number. Reuses policy/company_allowlist.yaml (147 companies, already bucketed
into the right categories by Career Agent's A2 work) as the company->category
source rather than duplicating a company list here; policy/industry_map.yaml
maps category -> tier plus the keyword/negative-list fallbacks.

TRUST TIERS FOR THE COMPANY NAME ITSELF. Direct-ATS sources (workday,
greenhouse, lever, ashby, smartrecruiters) report a verified employer
identity in `company` (a config-configured tenant/token). Aggregator sources
(adzuna, serpapi, job-alert emails) report whatever string the listing itself
put there, which is frequently a staffing agency, not the real employer. Name
keyword matching is therefore only applied for direct-ATS sources; aggregator
rows can still classify via the allowlist (a real company name matches
regardless of source) or via the JD's own self-description sentences.

This mirrors the standing project discipline (seniority.py, jd_analyst.py):
report evidence, never silently default to a made-up score, and a signal
that can't be verified doesn't get to make a confident claim.
"""
from __future__ import annotations

import os
import re

import yaml

POLICY_DIR = os.path.join(os.path.dirname(__file__), "policy")

DIRECT_ATS_SOURCES = {"workday", "greenhouse", "lever", "ashby", "smartrecruiters"}

TIER_DOMAIN_FLOOR = {"core": 1.0, "adjacent": 0.7}
TIER_DOMAIN_CAP = {"services": 0.3}

_SELF_DESC_RE = re.compile(
    r"\b(?:we are|we're|we build|we're building|our (?:platform|company|"
    r"mission|lending platform|credit platform))\b[^.]{0,120}", re.I)


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _load_yaml(filename: str) -> dict:
    path = os.path.join(POLICY_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_allowlist_cache = None
_industry_map_cache = None


def _allowlist() -> dict:
    global _allowlist_cache
    if _allowlist_cache is None:
        raw = _load_yaml("company_allowlist.yaml")
        lookup = {}
        for category, companies in (raw.get("categories") or {}).items():
            for c in companies or []:
                lookup[_normalize(c)] = category
        _allowlist_cache = lookup
    return _allowlist_cache


def _industry_map() -> dict:
    global _industry_map_cache
    if _industry_map_cache is None:
        _industry_map_cache = _load_yaml("industry_map.yaml")
    return _industry_map_cache


def classify(company: str, jd_text: str = "", source: str = "") -> dict:
    """Classify one job's employer into a tier, with evidence.

    Returns {"tier": "core"|"adjacent"|"services"|"unknown",
             "basis": "allowlist"|"jd_self_description"|"name_keyword"
                      |"negative_list"|"unknown",
             "evidence": <short human-readable string>}.

    Never raises, never makes a network call, never guesses past what the
    evidence actually supports — an unmatched company is "unknown", which
    applies no floor and no cap (identical behaviour to before this existed).
    """
    company = company or ""
    jd_text = jd_text or ""
    imap = _industry_map()
    norm = _normalize(company)

    # 1. Allowlist — the highest-confidence source, matches regardless of
    #    where the listing came from (a real company name is a real company
    #    name whether Adzuna or Greenhouse reported it).
    if norm:
        category = _allowlist().get(norm)
        if category:
            tier = (imap.get("category_tiers") or {}).get(category, "unknown")
            if tier != "unknown":
                return {"tier": tier, "basis": "allowlist",
                        "evidence": f"'{company}' is on the company allowlist "
                                    f"under '{category}'"}

    # 2. Negative list (services/staffing) — checked before positive
    #    fallbacks so a staffing JD mentioning a client's fintech-ness
    #    doesn't get read as the employer's own domain.
    #
    #    Brand names are matched ONLY against the company-name field, never
    #    free JD text (see industry_map.yaml's services_name_keywords
    #    comment) — a JD mentioning "Wipro" as a vendor/competitor is not
    #    evidence THIS employer is a staffing shop. Staffing-tell PHRASES
    #    ("our client", ...) are safe to match anywhere in the JD body,
    #    since they describe the listing's own relationship to the role.
    services_name_kw = [k.lower() for k in
                        (imap.get("services_name_keywords") or [])]
    haystack_name = company.lower()
    for kw in services_name_kw:
        if kw in haystack_name:
            return {"tier": "services", "basis": "negative_list",
                     "evidence": f"company name matches '{kw}'"}
    services_phrase_kw = [k.lower() for k in
                          (imap.get("services_jd_phrases") or [])]
    for kw in services_phrase_kw:
        if kw in jd_text.lower():
            return {"tier": "services", "basis": "negative_list",
                     "evidence": f"JD mentions '{kw}'"}
    m = _SELF_DESC_RE.search(jd_text)
    self_desc = m.group(0) if m else ""
    self_desc_lower = self_desc.lower()

    # 3. JD self-description — the JD's OWN first-person sentence about
    #    itself, not the whole JD body (that's what stops "our client, a
    #    leading NBFC" from being read as the employer's own domain).
    adj_desc_kw = [k.lower() for k in
                   (imap.get("adjacent_self_description_keywords") or [])]
    for kw in adj_desc_kw:
        if kw in self_desc_lower:
            return {"tier": "adjacent", "basis": "jd_self_description",
                     "evidence": f"JD describes itself: \"{self_desc.strip()}\""}

    # 4. Company-name keyword — only trusted when the name itself is a
    #    verified employer identity (a direct-ATS source's configured
    #    token/tenant), never for an aggregator's raw display string.
    if source in DIRECT_ATS_SOURCES:
        adj_name_kw = [k.lower() for k in
                       (imap.get("adjacent_name_keywords") or [])]
        for kw in adj_name_kw:
            if kw in haystack_name:
                return {"tier": "adjacent", "basis": "name_keyword",
                         "evidence": f"employer name '{company}' contains "
                                     f"'{kw}' (source: {source})"}

    return {"tier": "unknown", "basis": "unknown", "evidence": ""}


def apply_domain_floor_cap(domain_score: float, tier: str) -> float:
    """Apply the tier's floor or cap to an existing domain sub-score (0..1).
    "unknown" is a no-op by construction — identical to pre-feature scoring."""
    if tier in TIER_DOMAIN_FLOOR:
        return max(domain_score, TIER_DOMAIN_FLOOR[tier])
    if tier in TIER_DOMAIN_CAP:
        return min(domain_score, TIER_DOMAIN_CAP[tier])
    return domain_score
