"""Ashby public job-board API. No auth, no scraping — the published feed.

GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true

Ships with an EMPTY board list — add only confirmed board names to
config.yaml (ashby.boards). The board name is the slug in the company's
jobs.ashbyhq.com/<board> URL.
"""
import html
import re
import time

import requests

from . import normalize

BASE = "https://api.ashbyhq.com/posting-api/job-board"


def _strip_html(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch(config, log=print):
    boards = (config.get("ashby", {}) or {}).get("boards", []) or []
    if not boards:
        log("ashby: no boards configured — skipping")
        return []
    rows = []
    for board in boards:
        try:
            r = requests.get(f"{BASE}/{board}",
                             params={"includeCompensation": "true"},
                             timeout=30)
            r.raise_for_status()
            jobs = r.json().get("jobs", [])
        except Exception as e:
            log(f"ashby: '{board}' failed ({e}) — skipping")
            continue
        for j in jobs:
            if j.get("isListed") is False:
                continue
            rows.append(normalize(
                source="ashby",
                company=board,
                title=j.get("title", ""),
                location=j.get("location", "") or ", ".join(
                    j.get("secondaryLocations", []) or []),
                # descriptionPlain is provided directly — no HTML round-trip.
                description=(j.get("descriptionPlain")
                             or _strip_html(j.get("descriptionHtml", ""))),
                url=j.get("jobUrl", "") or j.get("applyUrl", ""),
                updated_at=j.get("publishedAt", ""),
            ))
        time.sleep(1.0)
    log(f"ashby: {len(rows)} listings")
    return rows
