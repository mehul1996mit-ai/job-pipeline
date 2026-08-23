"""authority_discovery.py — automates the manual research pattern from the
2026-08-05 session (leadership pages / press releases / bylines, read by
hand, one company at a time) using SerpApi + DETERMINISTIC regex
extraction, not an LLM call.

WHY NOT AN LLM. This repo has three confirmed incidents of an LLM/search
summary asserting a wrong person for a real company (Fibe's CPO, a Tata
Capital appointment, a Razorpay contact email — see CLAUDE.md's 2026-08-05
entries). A name written here becomes a real authority_node a real email
might reference by name. Regex extraction can only ever surface a name-and-
title pair that is LITERALLY, VERBATIM present in a real search snippet —
it cannot invent one the way a generation call can, even a well-guarded one.
The tradeoff is real: this will miss legitimate mentions that a human or an
LLM would catch from phrasing regex can't anticipate. That's the correct
tradeoff here — see recruiter_mine.py's docstring for the same reasoning
applied to a different discovery source.

WHAT THIS DOES NOT DO: produce an email address. Public bios essentially
never publish one, and this repo's own policy forbids guessing
first.last@company.com. The bridge from a discovered name to an actual send
is careers_inbox.py's verified careers@/jobs@/hr@ address, with the name
used for personalization ("Attn: <Name>") — see outreach_shortlist.py's
_draft_subject_body(). A discovered node here never gets its own
contact_channel; it exists to make an existing careers@ draft more specific
and credible, not to create a new send target.
"""
from __future__ import annotations

import json
import os
import re

import authority_graph as ag
import outreach_store as store

SERPAPI_USAGE_FILE = os.path.join(os.path.dirname(__file__), "data", "serpapi_usage.json")
AUTHORITY_DISCOVERY_MONTHLY_CAP = 25  # separate small sub-cap, same shared-quota discipline
                                       # as company_domains.py / interview_research.py

_NAME_TOKEN = r"[A-Z][a-zA-Z'.-]{1,20}"
_NAME_RE = re.compile(rf"\b{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{1,2}}\b")
_STOPWORDS = {
    "Chief", "Head", "Vice", "President", "Officer", "Manager", "Product",
    "Business", "Marketing", "Growth", "Talent", "Acquisition", "Human",
    "Resources", "Director", "The", "Read", "More", "Learn", "About",
    "Job", "Description", "Career", "Leadership", "Team", "Teams", "Senior",
    "Engineering", "Payment", "Solutions", "Employees", "Based", "Leader",
    "Nurturing", "Building", "Scaling", "Driving", "Volume", "Hiring",
    "Ads", "Apply", "Now", "Company", "Companies",
}
# Real snippet structure observed live (2026-08-18, see CLAUDE.md): a bio
# result puts the name at the START of the line, immediately followed by a
# separator and the title — "Vikas Kumar - Talent Acquisition Lead...",
# "Meghna Sethi is a Senior Manager, Talent Acquisition at Paytm." A first
# version of this function used character-window proximity matching
# anywhere near a title keyword instead, and it was badly wrong in
# production: real SerpApi results are noisy SEO/job-board fragments, and
# loose proximity matched garbage like "Ads. Bangalore" (a business unit +
# city name) and "Job Descriptio" (a truncated window cutting mid-word).
# Anchoring to line-start + an explicit separator eliminates both failure
# modes — it can't match mid-sentence noise or a word-boundary truncation.
_LEAD_NAME_RE = re.compile(
    rf"^({_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{1,2}})\s*(?:[-–—,]|(?:\s+is\s+(?:a|the)\s+))\s*(.+)$"
)


def _all_titles_and_function() -> list[tuple[str, str | None]]:
    """Every (keyword, function) pair from authority_graph's own vocabulary,
    plus TA markers with function=None — reusing that module's taxonomy so
    a discovered node classifies identically to a manually-entered one."""
    pairs = []
    for function, keywords in ag.FUNCTION_KEYWORDS.items():
        for kw in keywords:
            pairs.append((kw, function))
    for kw in ag.TA_MARKERS:
        pairs.append((kw, None))
    return pairs


def _is_plausible_name(candidate: str, company_name: str) -> bool:
    words = candidate.split()
    if not (2 <= len(words) <= 3):
        return False
    if any(w in _STOPWORDS for w in words):
        return False
    if candidate.lower() in company_name.lower() or company_name.lower() in candidate.lower():
        return False
    return True


def find_name_title_candidates(text: str, company_name: str) -> list[dict]:
    """Deterministic, line-anchored extraction — see the _LEAD_NAME_RE
    comment above for why this structure (not proximity windowing) is what
    real search-result text actually looks like. Each line/sentence of the
    input is checked independently for "Name <separator> rest-of-line",
    and the rest-of-line is only accepted as a real title if it contains
    one of authority_graph's own known title keywords — an arbitrary
    "Name - some words" line without a recognized title is not a match."""
    # SerpApi results are joined one-per-line by _search(); a single
    # multi-sentence snippet can still smuggle a second "Name - Title"
    # inside it, so split on sentence-ish boundaries too.
    lines = re.split(r"\n|(?<=[.!?])\s+(?=[A-Z])", text)

    seen = set()
    candidates = []
    for line in lines:
        line = line.strip()
        m = _LEAD_NAME_RE.match(line)
        if not m:
            continue
        candidate, rest = m.group(1).strip(), m.group(2).strip()
        if not _is_plausible_name(candidate, company_name):
            continue

        rest_lower = rest.lower()
        matched_keyword = matched_function = None
        for keyword, function in _all_titles_and_function():
            if keyword in rest_lower:
                matched_keyword, matched_function = keyword, function
                break
        if matched_keyword is None:
            continue

        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)

        # Title text is the rest-of-line up to the first sentence-ending
        # punctuation or an "at <Company>" mention, not the whole tail
        # (which can run into unrelated trailing sentences).
        cutoff_re = re.compile(r"[.|]|(?=\bat\s+" + re.escape(company_name) + r"\b)")
        title_text = cutoff_re.split(rest)[0].strip()
        node_type = ag.classify_node_type(title_text, matched_function)
        candidates.append({
            "name": candidate, "title": title_text, "function": matched_function,
            "node_type": node_type, "context": line[:200],
        })
    return candidates


def _load_usage() -> dict:
    if not os.path.exists(SERPAPI_USAGE_FILE):
        return {}
    try:
        with open(SERPAPI_USAGE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_usage(usage: dict) -> None:
    os.makedirs(os.path.dirname(SERPAPI_USAGE_FILE), exist_ok=True)
    with open(SERPAPI_USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(usage, f, indent=2)


def _current_month() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m")


def _search(company_name: str, log=print) -> tuple[str, str] | None:
    """One SerpApi call for a company; returns (concatenated snippet text,
    the query used) or None if quota-blocked/unavailable/no results —
    never raises, matching this module's other "absence is a valid
    outcome" siblings (A5's resolve_contact, etc)."""
    import requests

    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        log("authority_discovery: SERPAPI_KEY not set — skipping")
        return None

    month = _current_month()
    usage = _load_usage()
    if usage.get("month") != month:
        usage = {"month": month, "count": 0, "company_research_count": 0,
                  "domain_backfill_count": 0, "authority_discovery_count": 0}
    for k in ("count", "company_research_count", "domain_backfill_count",
              "authority_discovery_count"):
        usage.setdefault(k, 0)

    if usage["authority_discovery_count"] >= AUTHORITY_DISCOVERY_MONTHLY_CAP:
        log(f"authority_discovery: monthly cap reached "
            f"({usage['authority_discovery_count']}/{AUTHORITY_DISCOVERY_MONTHLY_CAP}) — skipping")
        return None
    combined = (usage["count"] + usage["company_research_count"]
                + usage["domain_backfill_count"] + usage["authority_discovery_count"])
    if combined >= 250 - 20:
        log("authority_discovery: shared SerpApi account quota nearly exhausted — skipping")
        return None

    query = f'"{company_name}" (product OR "talent acquisition") leadership team'
    try:
        r = requests.get("https://serpapi.com/search", params={
            "engine": "google", "q": query, "google_domain": "google.com",
            "gl": "in", "hl": "en", "api_key": api_key,
        }, timeout=30)
        usage["authority_discovery_count"] += 1
        _save_usage(usage)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log(f"authority_discovery: search failed for {company_name!r} ({e})")
        return None

    results = data.get("organic_results") or []
    if not results:
        return None
    parts = []
    for r_ in results[:8]:
        title, snippet = r_.get("title", ""), r_.get("snippet", "")
        if title or snippet:
            parts.append(f"{title}. {snippet}")
    return ("\n".join(parts), query) if parts else None


def _infer_source(text_context: str, company_domain: str | None) -> str:
    """Best-effort classification of where a mention came from — company's
    own domain implies company_leadership_page, otherwise press_release.
    This is a heuristic, not a certainty; logged, not silently assumed."""
    if company_domain and company_domain.lower() in text_context.lower():
        return "company_leadership_page"
    return "press_release"


def discover_for_company(conn, company_id: int, company_name: str, company_domain: str | None,
                          now: str, log=print) -> list[int]:
    """Runs one SerpApi search, extracts candidates, writes a new
    authority_node for each one not already known for this company
    (deduped by normalized name). Never writes a contact_channel — see
    module docstring. Returns the new authority_node ids."""
    result = _search(company_name, log)
    if result is None:
        return []
    text, query = result

    candidates = find_name_title_candidates(text, company_name)
    if not candidates:
        log(f"authority_discovery: no verifiable name+title found for {company_name!r}")
        return []

    existing_names = {
        row["person_name"].strip().lower()
        for row in conn.execute(
            "SELECT person_name FROM authority_node WHERE company_id = ?", (company_id,))
    }

    new_ids = []
    for c in candidates:
        if c["name"].strip().lower() in existing_names:
            continue
        existing_names.add(c["name"].strip().lower())
        source = _infer_source(c["context"], company_domain)
        node_id = store.insert_authority_node(
            conn, company_id, c["name"], source=source, created_at=now,
            title=c["title"], function=c["function"], node_type=c["node_type"],
            confidence=0.55,  # automated pattern-extraction — lower than a human-verified read
        )
        store.log_event(conn, "authority_node", node_id, "AUTHORITY_DISCOVERY_MATCH",
                         json.dumps({"query": query, "matched_title": c["title"],
                                     "context": c["context"][:300]}), now)
        new_ids.append(node_id)
        log(f"authority_discovery: {company_name!r} -> {c['name']} ({c['title']}, "
            f"{c['node_type']}, source={source})")

    return new_ids


def discover_for_allowlist(conn, limit=None, log=print, now=None):
    """Runs discover_for_company() for every company that has no
    non-derived (i.e. not careers_inbox.py's generic node) authority_node
    yet. Returns {"companies_processed": n, "nodes_found": n}."""
    import datetime
    now = now or datetime.datetime.utcnow().isoformat()

    rows = conn.execute(
        """SELECT id, name, domain FROM company
           WHERE NOT EXISTS (
             SELECT 1 FROM authority_node an
             WHERE an.company_id = company.id
               AND an.source != 'derived_role_inbox'
           )
           ORDER BY id"""
    ).fetchall()
    if limit:
        rows = rows[:limit]

    processed = 0
    total_found = 0
    for row in rows:
        new_ids = discover_for_company(conn, row["id"], row["name"], row["domain"], now, log)
        processed += 1
        total_found += len(new_ids)

    log(f"authority_discovery: processed {processed} companies, found {total_found} new node(s)")
    return {"companies_processed": processed, "nodes_found": total_found}
