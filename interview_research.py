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


# ============================================================ grounded call

def resolve_grounded_call_fn(config: dict | None = None):
    """Same provider resolution as interview_llm.resolve_free_call_fn, but
    adds the google_search tool and raises ResearchUnavailable on a 429/403
    quota error instead of retrying-then-succeeding -- grounding quota is
    documented separately/much smaller than plain generation quota (confirmed
    live 2026-08-12: plain calls succeeded, grounded calls returned
    RESOURCE_EXHAUSTED), so the existing 20s-retry-then-succeed pattern used
    elsewhere in this repo would just burn time before failing anyway."""
    import os
    import time
    import requests
    from interview_llm import resolve_free_call_fn  # provider/model selection only

    icfg = (config or {}).get("interview", {})
    provider = icfg.get("llm_provider", "gemini")
    if provider != "gemini":
        # google_search grounding is Gemini-specific; Groq has no equivalent.
        raise ResearchUnavailable(
            f"Grounded search is only implemented for gemini, not '{provider}'")
    model = icfg.get("gemini_model", "gemini-flash-lite-latest")

    def _call(prompt: str) -> str:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ResearchUnavailable("GEMINI_API_KEY not set")
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent",
                headers={"x-goog-api-key": key},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "tools": [{"google_search": {}}]},
                timeout=60)
            if r.status_code in (429, 403):
                raise ResearchUnavailable(
                    f"Grounded search quota unavailable (HTTP {r.status_code}). "
                    "Plain generation may still work; grounding does not right now.")
            r.raise_for_status()
            candidates = r.json().get("candidates", [])
            if not candidates:
                raise ResearchUnavailable("Grounded call returned no candidates")
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            if not text.strip():
                raise ResearchUnavailable("Grounded call returned empty text")
            return text
        except requests.exceptions.RequestException as e:
            raise ResearchUnavailable(f"Grounded search request failed: {e}")
    return _call


# ============================================================ §7: research

_RESEARCH_PROMPT = """Research the company "{company_name}" using live web
search. Answer ONLY with information you can find via search -- if search
does not surface something, omit that field rather than guessing.

Return ONLY a JSON object, no markdown fences, no commentary:
{{
  "corporate": {{"founded": "...", "ceo": "...", "headquarters": "...", "employee_count": "...", "parent_company": "..."}},
  "business": {{"business_model": "...", "key_products": ["..."], "customer_segments": ["..."]}},
  "strategy": {{"recent_priorities": ["..."], "recent_news": ["..."]}},
  "each_field_fact_type": "fact"
}}

Any field you cannot find via search: omit the key entirely. Do not fabricate
a plausible-sounding value."""


def research_company(conn, company_id: int, company_name: str, call_fn=None) -> list[dict]:
    """§7/§11: corporate/business/strategy facts. Raises ResearchUnavailable
    if grounded search cannot run -- caller must surface this, never
    substitute an ungrounded guess (see module docstring)."""
    call_fn = call_fn or resolve_grounded_call_fn()
    raw = call_fn(_RESEARCH_PROMPT.format(company_name=company_name))
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
                "period": None, "fact_type": "fact",
            })
    _insert_facts(conn, company_id, facts)
    return facts


_METRICS_PROMPT = """Using live web search, find the most recent available
values for these financial/business metrics for "{company_name}":
{metric_list}

Return ONLY a JSON array, no markdown fences, no commentary. One object per
metric you can actually find -- omit any metric search does not surface:
[{{"metric": "<one of the metric names above, verbatim>", "value": "...",
   "period": "e.g. FY2025 or Q1 FY26", "fact_type": "fact or calculated or estimate"}}]"""


def collect_financial_metrics(conn, company_id: int, industry: str, company_name: str,
                              call_fn=None) -> list[dict]:
    """§8/§9: industry-specific metric collection. `industry` selects which
    metric list to ask for -- never the same list for every company (§8's
    explicit requirement)."""
    call_fn = call_fn or resolve_grounded_call_fn()
    metrics = INDUSTRY_METRIC_LIBRARY.get(industry, INDUSTRY_METRIC_LIBRARY["general"])
    raw = call_fn(_METRICS_PROMPT.format(
        company_name=company_name, metric_list=", ".join(metrics)))
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
    current = conn.execute(
        "SELECT * FROM company WHERE process_id=? AND role='current'", (process_id,)).fetchone()
    target = conn.execute(
        "SELECT * FROM company WHERE process_id=? AND role='target'", (process_id,)).fetchone()
    if not current or not target:
        return []

    current_facts = get_company_facts(conn, current["id"])
    target_facts = get_company_facts(conn, target["id"])
    same_industry = current["industry"] == target["industry"]
    general_metrics = set(INDUSTRY_METRIC_LIBRARY["general"])

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
