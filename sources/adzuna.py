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

# NOT pursued (tried and reverted 2026-08-01): Adzuna's API field named
# "redirect_url" is actually a static, 200-OK details page
# (adzuna.in/details/{id}), not an HTTP redirect -- the real outbound click
# lives at a separate path (adzuna.in/land/ad/{id}), discoverable only by
# scraping the details page's HTML. Tested live: a single cold request to
# that path returned 429. That endpoint is bot-protected, and resolving it
# programmatically from ~50 jobs/day, from a GitHub Actions IP, risks
# tripping abuse detection on the account this pipeline's highest-volume
# source (84% of a typical day's queue) depends on -- the same class of risk
# already declined for Naukri/LinkedIn scraping elsewhere in this project.
# Not worth it for a one-click convenience. The apply-bridge link still works
# fine on the details page itself (the extension already auto-loads there
# per manifest.json's content_scripts, tailors normally, and reports "no
# form here" rather than erroring) -- Apply just costs one extra manual click
# through to the real employer page, same as it does without any of this.


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
