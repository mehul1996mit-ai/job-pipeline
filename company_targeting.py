"""A2 — Company Targeting.

Maintains the `company` table: force-includes every entry in
policy/company_allowlist.yaml as a guaranteed-include floor (source_floor =
'user_allowlist', exempt from DORMANT demotion), then scores every company
(floor + anything discovered elsewhere) on the signals that are actually
computable today.

Honest about what's NOT built yet: cluster-business-fit (needs A1's skill-
cluster embeddings), size band, geography and growth-signal scoring all need
data sources (RBI/NHB/SEBI registers, company metadata) this session didn't
build. Rather than fabricate a number for them, their weight is redistributed
onto the one signal this repo can already measure honestly — hiring activity,
read straight from job_pipeline's own seen_jobs.json — and every company's
relevance_explain_json records which signals actually fired vs which are
pending. Extend SIGNAL_WEIGHTS as later phases add real sources; the pending
signals must never be silently defaulted to a made-up midpoint score.
"""
import datetime
import json
import os
import re

import yaml

import outreach_store as store

POLICY_DIR = os.path.join(os.path.dirname(__file__), "policy")
SEEN_JOBS_PATH = os.path.join(os.path.dirname(__file__), "data", "seen_jobs.json")

# Full spec weights (§5.1 of the master prompt). Only "hiring_activity" has a
# real data source wired up today — the rest are tracked here so scoring
# doesn't silently drift when they're implemented, but they contribute 0
# until then (see relevance_explain_json's "pending_signals").
SIGNAL_WEIGHTS = {
    "cluster_business_fit": 0.30,
    "sub_sector_match": 0.20,
    "hiring_activity": 0.20,
    "size_band_fit": 0.10,
    "geography": 0.10,
    "growth_signal": 0.10,
}
IMPLEMENTED_SIGNALS = {"hiring_activity"}

HIRING_ACTIVITY_WINDOW_DAYS = 90
DORMANT_SCORE_CAP = 40


def _normalize(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def load_allowlist(path=None):
    path = path or os.path.join(POLICY_DIR, "company_allowlist.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    companies = []
    for category, names in data.get("categories", {}).items():
        for raw_name in names:
            # strip trailing "(...)" parenthetical aliases like "Axio (Capital Float)"
            name = re.sub(r"\s*\(.*\)\s*$", "", raw_name).strip()
            companies.append({"name": name, "category": category})
    conflict_names = {_normalize(n) for n in data.get("conflict_of_interest_companies", [])}
    for c in companies:
        c["is_conflict_of_interest"] = _normalize(c["name"]) in conflict_names
    return companies


def _load_hiring_counts(window_days=HIRING_ACTIVITY_WINDOW_DAYS, seen_jobs_path=SEEN_JOBS_PATH):
    """company_name(normalized) -> count of distinct jobs first seen in the
    trailing window. Reads job_pipeline's existing seen-store directly rather
    than re-discovering jobs — A4 already owns discovery."""
    if not os.path.exists(seen_jobs_path):
        return {}
    with open(seen_jobs_path, "r", encoding="utf-8") as f:
        seen = json.load(f)
    cutoff = datetime.date.today() - datetime.timedelta(days=window_days)
    counts = {}
    for row in seen.values():
        first_seen = row.get("first_seen")
        if not first_seen:
            continue
        try:
            d = datetime.date.fromisoformat(first_seen)
        except ValueError:
            continue
        if d < cutoff:
            continue
        key = _normalize(row.get("company", ""))
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def score_hiring_activity(company_name, hiring_counts):
    """0-100. 0 reqs -> 0 (drives DORMANT); scaled up to 100 at 5+ reqs in
    the window — a small, un-tuned curve until A9 has outcome data to fit
    against (see master prompt §5.1/§9)."""
    count = hiring_counts.get(_normalize(company_name), 0)
    return min(100, count * 20), count


def score_company(name, hiring_counts, source_floor):
    hiring_score, req_count = score_hiring_activity(name, hiring_counts)
    contributions = {"hiring_activity": round(SIGNAL_WEIGHTS["hiring_activity"] * hiring_score, 2)}
    pending = sorted(w for w in SIGNAL_WEIGHTS if w not in IMPLEMENTED_SIGNALS)
    total = sum(contributions.values())
    is_dormant = req_count == 0 and source_floor != "user_allowlist"
    if is_dormant:
        total = min(total, DORMANT_SCORE_CAP)
    explain = {
        "signals": contributions,
        "pending_signals": pending,
        "pending_signal_note": (
            "these signals need data sources not yet built (RBI/NHB/SEBI "
            "registers, size/geo metadata, growth-news feed) and contribute "
            "0 rather than a fabricated midpoint score"
        ),
        "req_count_90d": req_count,
        "dormant_exempt": source_floor == "user_allowlist",
    }
    status = "DORMANT" if is_dormant else "ACTIVE"
    return round(total, 2), status, explain


def run(db_path=None, allowlist_path=None, seen_jobs_path=SEEN_JOBS_PATH):
    """A2 entry point: force-include the allowlist floor, score every company
    (floor + previously-discovered), return the ranked list."""
    db_path = db_path or store.DB_PATH
    store.init_db(db_path)
    allowlist = load_allowlist(allowlist_path)
    hiring_counts = _load_hiring_counts(seen_jobs_path=seen_jobs_path)
    now = datetime.datetime.utcnow().isoformat()

    with store.connect(db_path) as conn:
        existing = {row["name"]: row for row in conn.execute("SELECT * FROM company")}

        for c in allowlist:
            if c["name"] in existing:
                # Floor re-affirms membership but never overwrites a company's
                # discovered history — see README's "editing the floor" note.
                conn.execute(
                    "UPDATE company SET source_floor = 'user_allowlist', "
                    "is_conflict_of_interest = ?, updated_at = ? WHERE name = ?",
                    (int(c["is_conflict_of_interest"]), now, c["name"]),
                )
            else:
                conn.execute(
                    """INSERT INTO company
                       (name, category, source_floor, is_conflict_of_interest,
                        created_at, updated_at)
                       VALUES (?, ?, 'user_allowlist', ?, ?, ?)""",
                    (c["name"], c["category"], int(c["is_conflict_of_interest"]), now, now),
                )

        results = []
        for row in conn.execute("SELECT * FROM company"):
            score, status, explain = score_company(row["name"], hiring_counts, row["source_floor"])
            conn.execute(
                """UPDATE company SET relevance_score = ?, relevance_explain_json = ?,
                   hiring_signal_score = ?, status = ?, updated_at = ? WHERE id = ?""",
                (score, json.dumps(explain), explain["signals"]["hiring_activity"] /
                 SIGNAL_WEIGHTS["hiring_activity"] if SIGNAL_WEIGHTS["hiring_activity"] else 0,
                 status, now, row["id"]),
            )
            results.append({
                "name": row["name"],
                "relevance_score": score,
                "status": status,
                "source_floor": row["source_floor"],
                "is_conflict_of_interest": bool(row["is_conflict_of_interest"]),
                "explain": explain,
            })

    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    return results


if __name__ == "__main__":
    ranked = run()
    print(f"{len(ranked)} companies in target list "
          f"({sum(1 for r in ranked if r['source_floor'] == 'user_allowlist')} from the allowlist floor)")
    for r in ranked[:15]:
        flag = " [CONFLICT]" if r["is_conflict_of_interest"] else ""
        print(f"  {r['relevance_score']:>5.1f}  {r['status']:8s}  {r['name']}{flag}")
