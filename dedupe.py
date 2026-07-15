"""Cross-source dedupe + persistent seen-store.

Dedupe key: sha1(normalized company|title|location). The seen-store at
data/seen_jobs.json persists across runs (committed back by the workflow) so
each run reports only NEW jobs.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

SEEN_PATH = Path("data/seen_jobs.json")


def job_hash(job: dict) -> str:
    def norm(s):
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    key = f"{norm(job.get('company'))}|{norm(job.get('title'))}|" \
          f"{norm(job.get('location'))}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def dedupe_cross_source(jobs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for job in jobs:
        h = job_hash(job)
        if h in seen:
            continue
        seen.add(h)
        job["hash"] = h
        out.append(job)
    return out


def load_seen(path: Path = SEEN_PATH) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def filter_new(jobs: list[dict], seen: dict) -> list[dict]:
    return [j for j in jobs if j.get("hash", job_hash(j)) not in seen]


def mark_seen(jobs: list[dict], seen: dict) -> dict:
    today = date.today().isoformat()
    for j in jobs:
        h = j.get("hash", job_hash(j))
        if h not in seen:
            seen[h] = {"first_seen": today, "url": j.get("url", ""),
                       "title": j.get("title", ""),
                       "company": j.get("company", "")}
    return seen


def save_seen(seen: dict, path: Path = SEEN_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen, indent=1), encoding="utf-8")
