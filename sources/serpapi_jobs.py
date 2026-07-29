"""SerpApi Google Jobs source — the paid-tier route to Naukri/LinkedIn/Indeed
coverage discussed with Mehul on 2026-07-29 (see CLAUDE.md).

WHY THIS EXISTS. Those portals are never scraped directly (ToS risk to the
accounts Mehul job-hunts with — see the design boundary in README.md).
job_alert_email.py is the free route via his own inbox. This is the paid
route: Google's own job-aggregation index, which pulls from sites (including
Naukri/LinkedIn/Indeed) that implement JobPosting structured data. Coverage is
real but partial — only what Google has indexed, not a full mirror.

QUOTA. Free tier is 250 searches/month, one search per (title, page). This
source uses config.search.serpapi_titles (a narrower subset than the main
search.titles list — see config.yaml) and tracks monthly usage in
data/serpapi_usage.json so a debug session re-running main.py repeatedly
can't silently blow the month's quota without warning. Stops calling once
within `quota_buffer` of the configured `monthly_quota` and logs it clearly
instead of eating a 429.

Env var: SERPAPI_KEY. Unset -> skips, exactly like every other keyless source.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import normalize

BASE = "https://serpapi.com/search"
USAGE_FILE = Path(__file__).resolve().parent.parent / "data" / "serpapi_usage.json"


def _load_usage() -> dict:
    if not USAGE_FILE.exists():
        return {}
    try:
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_usage(usage: dict) -> None:
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(usage, indent=2), encoding="utf-8")


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def fetch(config, log=print):
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        log("serpapi: SERPAPI_KEY not set — skipping")
        return []

    cfg = config.get("serpapi", {})
    if not cfg.get("enabled", False):
        log("serpapi: disabled in config — skipping")
        return []

    titles = config.get("search", {}).get("serpapi_titles", [])
    if not titles:
        log("serpapi: no serpapi_titles configured — skipping")
        return []

    monthly_quota = int(cfg.get("monthly_quota", 250))
    quota_buffer = int(cfg.get("quota_buffer", 20))
    max_pages = int(cfg.get("max_pages_per_title", 1))

    month = _current_month()
    usage = _load_usage()
    if usage.get("month") != month:
        usage = {"month": month, "count": 0}
    count = usage["count"]

    rows, seen_urls = [], set()
    for title in titles:
        next_token = None
        for page in range(max_pages):
            if count >= monthly_quota - quota_buffer:
                log(f"serpapi: within {quota_buffer} of monthly quota "
                    f"({count}/{monthly_quota} used this month) — stopping early")
                _save_usage({"month": month, "count": count})
                log(f"serpapi: {len(rows)} listings")
                return rows
            params = {
                "engine": "google_jobs",
                "q": title,
                "google_domain": "google.com",
                "gl": "in",
                "hl": "en",
                "location": "India",
                "api_key": api_key,
            }
            if next_token:
                params["next_page_token"] = next_token
            try:
                r = requests.get(BASE, params=params, timeout=30)
                count += 1
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                count += 1
                log(f"serpapi: '{title}' page {page + 1} failed ({e}) — continuing")
                break
            for j in data.get("jobs_results", []):
                apply_options = j.get("apply_options") or []
                url = apply_options[0].get("link", "") if apply_options else ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                ext = j.get("detected_extensions") or {}
                rows.append(normalize(
                    source="serpapi",
                    company=j.get("company_name", ""),
                    title=j.get("title", ""),
                    location=j.get("location", ""),
                    description=j.get("description", ""),
                    url=url,
                    updated_at=ext.get("posted_at", ""),
                ))
            next_token = (data.get("serpapi_pagination") or {}).get("next_page_token")
            if not next_token:
                break
    _save_usage({"month": month, "count": count})
    log(f"serpapi: {len(rows)} listings ({count}/{monthly_quota} calls used this month)")
    return rows
