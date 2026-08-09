"""A3 — Hiring Authority Graph.

The reframe from the master prompt: rank people by who OWNS the requisition,
not by who is titled "Recruiter". A TA partner with no open req has no
action available; the function head who owns the P&L does.

What this module does NOT do, on purpose: it does not scrape LinkedIn, does
not traverse any social graph, and does not go find people on its own.
Node *discovery* is restricted to public non-scraped sources (company
leadership pages, press releases, regulatory filings, conference speaker
lists, published bylines/podcasts) or Mehul's own manual entry — see
policy/authority_node_sources.yaml, enforced in outreach_store.insert_
authority_node(). Finding the actual names at each target company is
real research this module doesn't automate; add_node() below is the
write path once a name is found, by whatever allowed source found it.

warm_path_distance never comes from code walking a graph — it comes only
from policy/network.yaml, which Mehul edits by hand.
"""
import datetime
import os
import re

import yaml

import company_targeting as a2
import outreach_store as store

POLICY_DIR = os.path.join(os.path.dirname(__file__), "policy")

# --------------------------------------------------------------- function
# Same functional taxonomy job_pipeline already searches against
# (config.yaml search.titles) — kept here rather than imported so this
# module has no import-time dependency on config.yaml's exact structure.
FUNCTION_KEYWORDS = {
    "product": ["product manager", "product owner", "product lead", "head of product",
                "chief product officer", "cpo", "product management", "vp product",
                "vice president - product", "vice president, product"],
    "business_analysis": ["business analyst", "business analysis"],
    "partnerships": ["partnership", "alliance", "channel manager", "business development",
                      "business head", "chief business officer"],
    "program_project": ["program manager", "project manager", "programme manager"],
    "growth_marketing": ["growth", "performance marketing", "marketing manager",
                          "chief marketing officer", "cmo", "head of marketing"],
}


def classify_function(title):
    t = (title or "").lower()
    for function, keywords in FUNCTION_KEYWORDS.items():
        if any(k in t for k in keywords):
            return function
    return None


# ------------------------------------------------------------------ node type
# Priority order per master prompt §5.2: function/P&L owner > direct hiring
# manager > function-specific TA lead > generic TA/HR.
NODE_TYPE_BASE_LIKELIHOOD = {
    "function_owner": 0.85,
    "hiring_manager": 0.70,
    "ta_lead_function": 0.45,
    "generic_ta": 0.20,
}
OWNER_TITLE_MARKERS = ["head of", "vp ", "vice president", "chief", "director", "cxo", "cpo", "cbo",
                        "business head"]
TA_MARKERS = ["talent acquisition", "recruiter", "recruiting", "ta partner", "hr business partner"]
SIZE_BAND_HEADCOUNT_THRESHOLD = 2000


def classify_node_type(title, function):
    t = (title or "").lower()
    is_ta = any(m in t for m in TA_MARKERS)
    if is_ta:
        return "ta_lead_function" if function else "generic_ta"
    if any(m in t for m in OWNER_TITLE_MARKERS):
        return "function_owner"
    if function:
        return "hiring_manager"
    return "generic_ta"


def owns_req_likelihood(node_type, headcount_estimate, has_open_req):
    """Returns (score 0-1, explain dict). Modifiers are additive on the
    node-type base rate — see module docstring for the priority rationale.
    Not fitted against outcomes yet (needs A9 with n>=20, per master prompt
    §9); these are documented priors, not measured weights."""
    base = NODE_TYPE_BASE_LIKELIHOOD[node_type]
    score = base
    notes = [f"base({node_type})={base}"]

    if has_open_req is True:
        score += 0.10
        notes.append("+0.10 open req in function")
    elif has_open_req is False:
        score -= 0.15
        notes.append("-0.15 no open req in function")
    else:
        notes.append("open-req status unknown, no adjustment")

    if headcount_estimate is not None:
        if headcount_estimate < SIZE_BAND_HEADCOUNT_THRESHOLD and node_type == "function_owner":
            score += 0.05
            notes.append(f"+0.05 company <{SIZE_BAND_HEADCOUNT_THRESHOLD} hc: "
                          f"function owner IS assumed the hiring manager")
        elif headcount_estimate >= SIZE_BAND_HEADCOUNT_THRESHOLD and node_type == "function_owner":
            score -= 0.10
            notes.append(f"-0.10 company >={SIZE_BAND_HEADCOUNT_THRESHOLD} hc: "
                          f"assume a separate hiring-manager layer exists")
    else:
        notes.append("company headcount unknown, no size-band adjustment")

    score = max(0.0, min(1.0, score))
    return round(score, 2), notes


# --------------------------------------------------------------- warm path
def _normalize_name(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def load_network(path=None):
    path = path or os.path.join(POLICY_DIR, "network.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    entries = {}
    for p in data.get("people") or []:
        entries[_normalize_name(p["person"])] = {
            "distance": p["distance"],
            "via": p.get("via"),
            "company": p.get("company"),
        }
    return entries


def warm_path_distance(person_name, network_entries):
    """Never derived by traversing anything — only a lookup against Mehul's
    own network.yaml. Default is 2 ('same sub-sector, plausible cold-but-
    relevant'), not 3 ('no path'): every company this graph runs against
    came through A2's BFSI/fintech-targeted floor or discovery, so sub-sector
    adjacency already holds by construction unless network.yaml says
    otherwise (a real known/unknown person)."""
    entry = network_entries.get(_normalize_name(person_name))
    if entry is None:
        return 2, None
    return entry["distance"], entry.get("via")


# --------------------------------------------------------------- write path
def add_node(conn, company_name, person_name, title, source,
             public_profile_url=None, network_entries=None,
             hiring_counts=None, confidence=None):
    """Look up the company (must already exist via A2 — a node can't attach
    to a company outside the target list), classify function/node type,
    score owns_req_likelihood, resolve warm_path_distance from network.yaml,
    and write through outreach_store's single insert path."""
    row = conn.execute("SELECT id, headcount_estimate FROM company WHERE name = ?",
                        (company_name,)).fetchone()
    if row is None:
        raise ValueError(f"{company_name!r} is not in the company table — run A2 (company_targeting.run()) first")

    function = classify_function(title)
    node_type = classify_node_type(title, function)

    hiring_counts = hiring_counts if hiring_counts is not None else a2._load_hiring_counts()
    _, req_count = a2.score_hiring_activity(company_name, hiring_counts)
    has_open_req = req_count > 0 if function else None

    likelihood, notes = owns_req_likelihood(node_type, row["headcount_estimate"], has_open_req)

    network_entries = network_entries if network_entries is not None else load_network()
    distance, via = warm_path_distance(person_name, network_entries)

    node_id = store.insert_authority_node(
        conn, row["id"], person_name, source, datetime.datetime.utcnow().isoformat(),
        title=title, function=function, seniority_band=node_type,
        owns_req_likelihood=likelihood, warm_path_distance=distance,
        warm_path_via=via, public_profile_url=public_profile_url,
        confidence=confidence,
    )
    return {
        "id": node_id, "company": company_name, "person_name": person_name,
        "title": title, "function": function, "node_type": node_type,
        "owns_req_likelihood": likelihood, "likelihood_explain": notes,
        "warm_path_distance": distance, "warm_path_via": via,
    }


def add_manual_node(conn, company_name, person_name, title, public_profile_url=None,
                     hiring_counts=None):
    """Convenience wrapper for the one source Mehul can use directly himself
    right now — the other 5 allowed sources (leadership pages, press
    releases, filings, speaker lists, bylines) need real per-company
    research this module doesn't automate (see module docstring)."""
    return add_node(conn, company_name, person_name, title,
                     source="user_manual_entry", public_profile_url=public_profile_url,
                     hiring_counts=hiring_counts)


def rank_nodes_for_company(conn, company_id):
    """The actual reframe: sort by who owns the req, not by title alphabet
    or recency. Ties broken by warm path (closer wins)."""
    rows = conn.execute(
        "SELECT * FROM authority_node WHERE company_id = ? "
        "ORDER BY owns_req_likelihood DESC, warm_path_distance ASC",
        (company_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def run(db_path=None):
    """Recompute owns_req_likelihood and warm_path_distance for every
    existing node against the latest company/hiring-activity/network.yaml
    state — does not discover new nodes (see module docstring)."""
    db_path = db_path or store.DB_PATH
    store.init_db(db_path)
    network_entries = load_network()
    hiring_counts = a2._load_hiring_counts()

    with store.connect(db_path) as conn:
        nodes = conn.execute(
            "SELECT authority_node.*, company.name AS company_name, "
            "company.headcount_estimate AS headcount_estimate "
            "FROM authority_node JOIN company ON company.id = authority_node.company_id"
        ).fetchall()
        for n in nodes:
            _, req_count = a2.score_hiring_activity(n["company_name"], hiring_counts)
            has_open_req = req_count > 0 if n["function"] else None
            likelihood, _ = owns_req_likelihood(n["seniority_band"], n["headcount_estimate"], has_open_req)
            distance, via = warm_path_distance(n["person_name"], network_entries)
            conn.execute(
                "UPDATE authority_node SET owns_req_likelihood = ?, "
                "warm_path_distance = ?, warm_path_via = ? WHERE id = ?",
                (likelihood, distance, via, n["id"]),
            )
        companies = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM company")}
        report = {}
        for company_id, name in companies.items():
            ranked = rank_nodes_for_company(conn, company_id)
            if ranked:
                report[name] = ranked
    return report


if __name__ == "__main__":
    report = run()
    if not report:
        print("0 authority nodes yet. Add one with authority_graph.add_manual_node(), "
              "or via a research pass using the sources in policy/authority_node_sources.yaml.")
    for company, nodes in report.items():
        print(f"\n{company}:")
        for n in nodes:
            print(f"  {n['owns_req_likelihood']:.2f}  wp={n['warm_path_distance']}  "
                  f"{n['person_name']} — {n['title']} ({n['seniority_band']})")
