"""Adzuna API source (free tier). https://developer.adzuna.com/

Broad generic title searches across ALL industries — queries are the title
strings from config, never restricted to fintech/NBFC terms. Domain fit is
handled later as a scoring bonus, not a search filter.

Env vars: ADZUNA_APP_ID, ADZUNA_APP_KEY.
"""
import os
import time

import requests

from . import normalize

BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


def fetch(config, log=print):
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        log("adzuna: ADZUNA_APP_ID/ADZUNA_APP_KEY not set — skipping")
        return []

    cfg = config.get("adzuna", {})
    country = cfg.get("country", "in")
    per_page = int(cfg.get("results_per_page", 50))
    max_pages = int(cfg.get("max_pages_per_title", 2))
    titles = config.get("search", {}).get("titles", [])

    rows, seen_urls = [], set()
    for title in titles:
        for page in range(1, max_pages + 1):
            try:
                r = requests.get(
                    BASE.format(country=country, page=page),
                    params={
                        "app_id": app_id, "app_key": app_key,
                        "what": title, "results_per_page": per_page,
                        "content-type": "application/json",
                    },
                    timeout=30,
                )
                r.raise_for_status()
                results = r.json().get("results", [])
            except Exception as e:
                log(f"adzuna: '{title}' page {page} failed ({e}) — continuing")
                break
            for j in results:
                url = j.get("redirect_url") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                rows.append(normalize(
                    source="adzuna",
                    company=(j.get("company") or {}).get("display_name", ""),
                    title=j.get("title", ""),
                    location=(j.get("location") or {}).get("display_name", ""),
                    description=j.get("description", ""),
                    url=url,
                    updated_at=j.get("created", ""),
                    salary_min=j.get("salary_min"),
                    salary_max=j.get("salary_max"),
                ))
            if len(results) < per_page:
                break  # no more pages for this title
            time.sleep(1.5)  # politeness between pages
        time.sleep(1.0)  # politeness between title queries
    log(f"adzuna: {len(rows)} listings")
    return rows
