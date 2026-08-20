"""Company research + comparison engine (§7-13, §20-22 of the interview-
intelligence master prompt).

Two halves with very different cost/risk profiles, kept structurally
separate:

1. RESEARCH (industry classification, company facts, financial metrics) --
   needs live grounded search. Per the 2026-08-12 session: the free Gemini
   key's search-grounding quota was confirmed exhausted (429
   RESOURCE_EXHAUSTED on the grounded call specifically; plain generation
   calls succeed) -- so this module NEVER silently falls back to an
   ungrounded LLM guess when grounding is unavailable. `research_company()`
   raises ResearchUnavailable instead. Ungrounded model output about a real
   company's financials is a fabrication risk this repo has hit for real
   three times already (Fibe, Tata Capital, Razorpay contact -- see
   CLAUDE.md); surfacing "not available right now" is safer than a
   plausible-sounding wrong number.

2. COMPARISON (company vs company, role vs role) -- pure data reduction over
   already-stored facts, zero LLM calls, zero quota dependency. §13 frames
   the comparison table as mechanical (value/difference/interpretation from
   two known numbers), and §22's role comparison only ever needs the CV
   (already structured) and the JD (already parsed) -- both exist before
   this module is ever called.

Per explicit user override (2026-08-12): company_fact rows carry NO source
URL and NO confirmation gate -- they are usable immediately, at the same
trust level as any other generated content. This is a deliberate departure
from I3's candidate-fact discipline, and it is scoped ONLY to company/
industry research; resume_claim/fact_candidate (the candidate's own facts)
keep the full I3 gate untouched.
"""
from __future__ import annotations

import json
import os
import re

_now = None  # set lazily to avoid a hard import cycle; see _now_str()


def _now_str() -> str:
    from interview_prep import _now
    return _now()


class ResearchUnavailable(RuntimeError):
    """Raised when grounded search cannot run (quota, no key, or an error).
    Never caught silently and never triggers an ungrounded-fallback guess --
    the caller must surface this to the user, not paper over it."""


# ============================================================ §8: metric lib

INDUSTRY_METRIC_LIBRARY = {
    "banking_nbfc_housing_finance": [
        "Revenue", "AUM", "AUM growth", "Disbursement", "PAT", "PAT growth",
        "NIM", "ROA", "ROE", "GNPA", "NNPA", "Credit cost", "Cost of funds",
        "Capital adequacy", "Collection efficiency", "Cost-to-income",
        "Branch count", "Digital contribution",
    ],
    "insurance": [
        "GWP", "APE", "VNB", "VNB margin", "Persistency", "Solvency ratio",
        "Claims ratio", "Combined ratio",
    ],
    "saas": [
        "ARR", "MRR", "NRR", "GRR", "CAC", "LTV", "Churn", "Burn", "EBITDA",
        "Rule of 40",
    ],
    "ecommerce_consumer_internet": [
        "GMV", "Orders", "AOV", "Take rate", "CAC", "Repeat rate",
        "Conversion", "Contribution margin",
    ],
    "manufacturing_automotive_fmcg": [
        "Revenue", "EBITDA", "EBITDA margin", "Capacity utilization", "ROCE",
        "Working capital", "Inventory", "Debt",
    ],
    # Generic fallback for industries with no dedicated set (IT services,
    # consulting, healthcare, logistics, telecom, media, edtech, travel,
    # real estate, etc.) -- deliberately narrow rather than guessing a
    # bespoke list per industry with no verified basis for it.
    "general": [
        "Revenue", "Revenue growth", "EBITDA", "EBITDA margin", "PAT",
        "Employee count", "Market share",
    ],
}

# Keyword -> industry, used by the deterministic classifier below. Ordered
# so a more specific match (e.g. "housing finance") is checked before a
# broader one (e.g. "nbfc").
_INDUSTRY_KEYWORDS = [
    ("housing finance", "banking_nbfc_housing_finance"),
    ("home loan", "banking_nbfc_housing_finance"),
    ("nbfc", "banking_nbfc_housing_finance"),
    ("bank", "banking_nbfc_housing_finance"),
    ("insurance", "insurance"),
    ("insurer", "insurance"),
    ("saas", "saas"),
    ("software as a service", "saas"),
    ("subscription software", "saas"),
    ("e-commerce", "ecommerce_consumer_internet"),
    ("ecommerce", "ecommerce_consumer_internet"),
    ("marketplace", "ecommerce_consumer_internet"),
    ("consumer internet", "ecommerce_consumer_internet"),
    ("manufacturing", "manufacturing_automotive_fmcg"),
    ("automotive", "manufacturing_automotive_fmcg"),
    ("fmcg", "manufacturing_automotive_fmcg"),
]


def classify_industry(company_name: str, jd_text: str = "") -> dict:
    """§7: classify BEFORE researching, never assume. Deterministic keyword
    match over the JD text + company name -- zero LLM cost, zero
    hallucination risk, same "regex first" discipline as jd_analyst.py.

    Returns {"industry": <key into INDUSTRY_METRIC_LIBRARY>, "basis": "fact"|"inference"}.
    "fact" when the JD/company text states the industry in its own words;
    "general" + "inference" when nothing matched -- §36 requires this be
    labeled, not silently defaulted.
    """
    haystack = f"{company_name} {jd_text}".lower()
    for keyword, industry in _INDUSTRY_KEYWORDS:
        if keyword in haystack:
            return {"industry": industry, "basis": "fact"}
    return {"industry": "general", "basis": "inference"}


def get_or_create_company(conn, process_id: int, role: str, company_name: str,
                          jd_text: str = "") -> int:
    if role not in ("current", "target"):
        raise ValueError("role must be 'current' or 'target'")
    if role == "current":
        # The candidate's current employer doesn't change per interview
        # process, so its research is a reusable, shared fact -- not scoped
        # to whichever process happened to trigger the research. Look up ANY
        # existing 'current' row for this company name (across every
        # process), not just this one, so research done from one interview
        # process is immediately visible from every other. 'target' stays
        # process-scoped below (per §45 isolation -- two processes targeting
        # the same real company must NOT share a row).
        row = conn.execute(
            "SELECT id, industry FROM company WHERE role='current' AND company_name=?",
            (company_name,)).fetchone()
        if row:
            return row["id"]
    else:
        row = conn.execute(
            "SELECT id, industry FROM company WHERE process_id=? AND role=?",
            (process_id, role)).fetchone()
        if row:
            return row["id"]
    industry_info = classify_industry(company_name, jd_text)
    cur = conn.execute(
        """INSERT INTO company (process_id, role, company_name, industry,
             industry_basis, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (process_id, role, company_name, industry_info["industry"],
         industry_info["basis"], _now_str(), _now_str()))
    return cur.lastrowid


# ================================================================ raw search
#
# Grounded Gemini search (google_search tool) was the original design here.
# Confirmed dead 2026-08-12/13: 429 RESOURCE_EXHAUSTED on the grounded call
# specifically, persisting across a full day boundary in both UTC and IST --
# not a daily quota, effectively unavailable on the free tier without
# billing. Rather than keep the whole research feature hostage to one
# quota, RAW TEXT GATHERING and FACT STRUCTURING are separate steps:
# SerpApi (plain web search, already has a working key + quota tracker in
# this repo for job search) gathers snippets, and a PLAIN Gemini call
# (confirmed working) structures them into facts -- same discipline as
# before, just sourced differently. Per explicit request (2026-08-13):
# fully automated, no paste-in/manual step anywhere in this path.

SERPAPI_BASE = "https://serpapi.com/search"
SERPAPI_USAGE_FILE = os.path.join(os.path.dirname(__file__), "data", "serpapi_usage.json")
# Company research shares the real SerpApi account (and its real 250/month
# cap) with job search's own usage in sources/serpapi_jobs.py -- tracked in
# the SAME usage file, under a separate counter, so the true combined total
# is always visible. This cap additionally self-limits company research to
# a small footprint even when the shared budget has room, so a research-heavy
# session can't quietly crowd out job search's much larger daily need.
COMPANY_RESEARCH_MONTHLY_CAP = 20


def _load_serpapi_usage() -> dict:
    if not os.path.exists(SERPAPI_USAGE_FILE):
        return {}
    try:
        with open(SERPAPI_USAGE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_serpapi_usage(usage: dict) -> None:
    os.makedirs(os.path.dirname(SERPAPI_USAGE_FILE), exist_ok=True)
    with open(SERPAPI_USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(usage, f, indent=2)


def _current_month() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m")


def _check_and_reserve_serpapi_call(monthly_quota: int = 250, quota_buffer: int = 20) -> dict:
    """Raises ResearchUnavailable if either the company-research sub-cap or
    the real combined account quota would be exceeded. Returns the usage
    dict to update after a real call is made (see _record_serpapi_call)."""
    month = _current_month()
    usage = _load_serpapi_usage()
    if usage.get("month") != month:
        usage = {"month": month, "count": 0, "company_research_count": 0}
    usage.setdefault("count", 0)
    usage.setdefault("company_research_count", 0)

    if usage["company_research_count"] >= COMPANY_RESEARCH_MONTHLY_CAP:
        raise ResearchUnavailable(
            f"Company research SerpApi cap reached this month "
            f"({usage['company_research_count']}/{COMPANY_RESEARCH_MONTHLY_CAP}) -- "
            "resets next month. This cap is separate from and smaller than the "
            "shared account quota, kept deliberately low so research can't crowd "
            "out job search's own SerpApi usage.")
    combined = usage["count"] + usage["company_research_count"]
    if combined >= monthly_quota - quota_buffer:
        raise ResearchUnavailable(
            f"Shared SerpApi account quota nearly exhausted this month "
            f"({combined}/{monthly_quota} used across job search + company research) "
            "-- refusing to spend more of it on research.")
    return usage


def _record_serpapi_call(usage: dict) -> None:
    usage["company_research_count"] += 1
    _save_serpapi_usage(usage)


def _serpapi_search(query: str, log=print) -> str:
    """One SerpApi google-engine search, quota-checked and quota-recorded.
    Returns concatenated title+snippet text from organic results -- raw
    material for the structuring LLM call, not itself a source of facts.
    Raises ResearchUnavailable on missing key, exhausted quota, or a failed
    request; never returns a silently-empty string that would let the
    structuring step hallucinate from nothing."""
    import requests

    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        raise ResearchUnavailable("SERPAPI_KEY not set")
    usage = _check_and_reserve_serpapi_call()
    try:
        r = requests.get(SERPAPI_BASE, params={
            "engine": "google", "q": query, "google_domain": "google.com",
            "gl": "in", "hl": "en", "api_key": api_key,
        }, timeout=30)
        _record_serpapi_call(usage)  # count the call whether or not it errors -- it still spent quota
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise ResearchUnavailable(f"SerpApi search failed: {e}")

    results = data.get("organic_results", []) or []
    if not results:
        raise ResearchUnavailable(f"SerpApi returned no results for {query!r}")
    log(f"interview_research: serpapi '{query}' -> {len(results)} result(s)")
    parts = []
    for r in results[:8]:
        title = r.get("title", "")
        snippet = r.get("snippet", "")
        if title or snippet:
            parts.append(f"- {title}: {snippet}")
    return "\n".join(parts)


def resolve_plain_call_fn(config: dict | None = None):
    """The structuring half -- plain (non-grounded) Gemini generation, which
    works today (confirmed live, unlike the grounded call). Reuses
    interview_llm's own provider resolution/retry so this stays one
    implementation of "call the free-tier LLM", not a second one."""
    from interview_llm import resolve_free_call_fn
    return resolve_free_call_fn(config)


# ============================================================ §7: research

_RESEARCH_PROMPT = """Structure the following web search results about the
company "{company_name}" into facts. Use ONLY information present in the
search results below -- if the results don't cover a field, omit that key
entirely rather than guessing or using prior knowledge.

SEARCH RESULTS:
{search_results}

Return ONLY a JSON object, no markdown fences, no commentary:
{{
  "corporate": {{"founded": "...", "ceo": "...", "headquarters": "...", "employee_count": "...", "parent_company": "..."}},
  "business": {{"business_model": "...", "key_products": ["..."], "customer_segments": ["..."]}},
  "strategy": {{"recent_priorities": ["..."], "recent_news": ["..."]}}
}}

Any field the search results don't cover: omit the key entirely. Do not
fabricate a plausible-sounding value."""


def research_company(conn, company_id: int, company_name: str, call_fn=None,
                     search_fn=None, log=print) -> list[dict]:
    """§7/§11: corporate/business/strategy facts, fully automated -- SerpApi
    gathers raw text, a plain Gemini call structures it. Raises
    ResearchUnavailable if the search step can't run (no key, quota, no
    results); never substitutes an ungrounded guess with no search backing
    it at all (see module docstring)."""
    search_fn = search_fn or (lambda q: _serpapi_search(q, log=log))
    call_fn = call_fn or resolve_plain_call_fn()
    snippets = search_fn(f"{company_name} company overview CEO founded headquarters business model")
    raw = call_fn(_RESEARCH_PROMPT.format(company_name=company_name, search_results=snippets))
    data = _extract_json(raw)
    facts = []
    for category in ("corporate", "business", "strategy"):
        section = data.get(category, {})
        if not isinstance(section, dict):
            continue
        for label, value in section.items():
            if value in (None, "", []):
                continue
            facts.append({
                "category": category, "label": label,
                "value": json.dumps(value) if isinstance(value, list) else str(value),
                "period": None, "fact_type": "estimate",  # derived from search snippets, not a primary source
            })
    _insert_facts(conn, company_id, facts)
    return facts


_METRICS_PROMPT = """Structure the following web search results into values
for these financial/business metrics of "{company_name}":
{metric_list}

SEARCH RESULTS:
{search_results}

Return ONLY a JSON array, no markdown fences, no commentary. One object per
metric the search results actually support -- omit any metric not covered:
[{{"metric": "<one of the metric names above, verbatim>", "value": "...",
   "period": "e.g. FY2025 or Q1 FY26", "fact_type": "fact or calculated or estimate"}}]"""


def collect_financial_metrics(conn, company_id: int, industry: str, company_name: str,
                              call_fn=None, search_fn=None, log=print) -> list[dict]:
    """§8/§9: industry-specific metric collection, fully automated. `industry`
    selects which metric list to ask for -- never the same list for every
    company (§8's explicit requirement)."""
    search_fn = search_fn or (lambda q: _serpapi_search(q, log=log))
    call_fn = call_fn or resolve_plain_call_fn()
    metrics = INDUSTRY_METRIC_LIBRARY.get(industry, INDUSTRY_METRIC_LIBRARY["general"])
    snippets = search_fn(f"{company_name} financial results {' '.join(metrics[:4])}")
    raw = call_fn(_METRICS_PROMPT.format(
        company_name=company_name, metric_list=", ".join(metrics), search_results=snippets))
    data = _extract_json(raw)
    if not isinstance(data, list):
        return []
    facts = []
    for item in data:
        if not isinstance(item, dict) or not item.get("metric") or not item.get("value"):
            continue
        if item["metric"] not in metrics:
            continue  # don't accept a metric the prompt didn't ask for
        facts.append({
            "category": "financial", "label": item["metric"], "value": str(item["value"]),
            "period": item.get("period"), "fact_type": item.get("fact_type", "estimate"),
        })
    _insert_facts(conn, company_id, facts)
    return facts


def _insert_facts(conn, company_id: int, facts: list[dict]) -> None:
    for f in facts:
        conn.execute(
            """INSERT INTO company_fact
               (company_id, category, label, value, period, fact_type, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (company_id, f["category"], f["label"], f["value"], f.get("period"),
             f.get("fact_type", "fact"), _now_str()))


def _extract_json(raw: str):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(json)?", "", raw).rstrip("`").strip()
    return json.loads(raw)


# ============================================================ §12/13: compare

def get_company_facts(conn, company_id: int) -> dict:
    rows = conn.execute(
        "SELECT category, label, value, period, fact_type FROM company_fact WHERE company_id=?",
        (company_id,)).fetchall()
    out = {}
    for r in rows:
        out[r["label"]] = dict(r)
    return out


def compare_companies(conn, process_id: int) -> list[dict]:
    """§12/13: pure reduction over already-stored company_fact rows. No LLM
    call -- the numbers are already known, so an interpretation is a
    template, not a judgment. Comparability is determined by whether BOTH
    companies share the same industry (comparable), share a metric that
    exists in the general library regardless of industry (partially
    comparable), or neither (not comparable, per §12's explicit ban on
    forcing an artificial comparison)."""
    # 'current' is a shared singleton (see get_or_create_company) -- not
    # scoped to this process_id, since the candidate's current employer is
    # the same fact regardless of which interview process asks about it.
    # Most-recently-created row wins on the rare case the candidate's actual
    # current employer changes and a second 'current' row gets created.
    current = conn.execute(
        "SELECT * FROM company WHERE role='current' ORDER BY id DESC LIMIT 1").fetchone()
    target = conn.execute(
        "SELECT * FROM company WHERE process_id=? AND role='target'", (process_id,)).fetchone()
    if not current or not target:
        return []

    current_facts = get_company_facts(conn, current["id"])
    target_facts = get_company_facts(conn, target["id"])
    same_industry = current["industry"] == target["industry"]
    general_metrics = set(INDUSTRY_METRIC_LIBRARY["general"])

    # Replace, don't append -- re-running this (e.g. after new facts arrive)
    # must not accumulate duplicate rows for the same dimension.
    conn.execute("DELETE FROM company_comparison WHERE process_id=?", (process_id,))

    rows = []
    for label in sorted(set(current_facts) | set(target_facts)):
        cur_fact = current_facts.get(label)
        tgt_fact = target_facts.get(label)
        if cur_fact is None or tgt_fact is None:
            continue  # can't compare a metric only one side has
        if same_industry:
            comparability = "comparable"
        elif label in general_metrics:
            comparability = "partially_comparable"
        else:
            comparability = "not_comparable"

        interpretation = _interpret(label, cur_fact["value"], tgt_fact["value"], comparability)
        rows.append({
            "dimension": label, "comparability": comparability,
            "current_value": cur_fact["value"], "target_value": tgt_fact["value"],
            "interpretation": interpretation,
        })
        conn.execute(
            """INSERT INTO company_comparison
               (process_id, dimension, comparability, current_value, target_value,
                interpretation, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (process_id, label, comparability, cur_fact["value"], tgt_fact["value"],
             interpretation, _now_str()))
    return rows


def _interpret(label: str, current_value: str, target_value: str, comparability: str) -> str:
    if comparability == "not_comparable":
        return (f"{label} is not meaningfully comparable across these two companies "
                f"(different industries, different definitions likely apply).")
    prefix = "" if comparability == "comparable" else "Partially comparable — "
    cur_num = _to_number(current_value)
    tgt_num = _to_number(target_value)
    if cur_num is not None and tgt_num is not None:
        if tgt_num > cur_num:
            direction = f"target company's {label} ({target_value}) is higher than current ({current_value})"
        elif tgt_num < cur_num:
            direction = f"target company's {label} ({target_value}) is lower than current ({current_value})"
        else:
            direction = f"{label} is roughly the same at both companies"
        return f"{prefix}{direction}."
    return f"{prefix}Current: {current_value}. Target: {target_value}."


def _to_number(value: str):
    m = re.search(r"-?\d+(\.\d+)?", value.replace(",", ""))
    return float(m.group(0)) if m else None


# ============================================================ §22: role compare

_ROLE_DIMENSIONS = {
    "leadership": ["led", "managed", "mentored", "directed", "leadership", "manager", "head of"],
    "analytics": ["analytics", "data-driven", "cohort", "funnel", "a/b test", "dashboard", "metrics"],
    "stakeholder_management": ["stakeholder", "cross-functional", "partnered with", "collaborated"],
    "domain": ["lending", "credit", "underwriting", "risk", "fintech", "banking", "nbfc", "insurance"],
    "product_ownership": ["owned", "roadmap", "prd", "product strategy", "launched", "shipped"],
    "technology": ["api", "integration", "architecture", "platform", "technical"],
}


def compare_roles(conn, process_id: int, master_resume: dict, jd_text: str) -> list[dict]:
    """§22: deterministic, no research/LLM dependency. Current role text is
    every bullet in the CV; target role text is the raw JD. A dimension is
    'transferable' if its keywords appear on both sides, a 'gap' if only the
    JD side has them -- silently skipped (not stored) if neither side
    mentions it, since that dimension isn't relevant to this comparison."""
    cv_bullets = []
    for company in master_resume.get("experience", []):
        for role in company.get("roles", []):
            cv_bullets.extend(role.get("bullets", []))
    current_text = " ".join(cv_bullets).lower()
    target_text = jd_text.lower()

    # Replace, don't append -- re-running this must not accumulate duplicate
    # rows for the same dimension (found live: clicking Compare more than
    # once tripled every row).
    conn.execute("DELETE FROM role_comparison WHERE process_id=?", (process_id,))

    rows = []
    for dimension, keywords in _ROLE_DIMENSIONS.items():
        in_current = any(kw in current_text for kw in keywords)
        in_target = any(kw in target_text for kw in keywords)
        if not in_current and not in_target:
            continue
        if in_current and in_target:
            status, recommendation = "transferable", "emphasize"
        elif in_target and not in_current:
            status, recommendation = "gap", "learn"
        else:
            status, recommendation = "transferable", "dont_overemphasize"
        matched_current = next((b for b in cv_bullets if any(kw in b.lower() for kw in keywords)), "")
        rows.append({
            "dimension": dimension, "status": status, "recommendation": recommendation,
            "current_role_text": matched_current or "(not evidenced in CV)",
            "target_role_text": f"JD emphasizes: {dimension.replace('_', ' ')}",
        })
        conn.execute(
            """INSERT INTO role_comparison
               (process_id, dimension, current_role_text, target_role_text,
                status, recommendation, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (process_id, dimension, rows[-1]["current_role_text"],
             rows[-1]["target_role_text"], status, recommendation, _now_str()))
    return rows
