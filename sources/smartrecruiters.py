"""SmartRecruiters public postings API. No auth, no scraping — this is the
feed employers publish for exactly this purpose.

GET https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100
GET https://api.smartrecruiters.com/v1/companies/{company}/postings/{id}

Ships with an EMPTY company list — add only confirmed identifiers to
config.yaml (smartrecruiters.companies).
"""
import html
import re
import time

import requests

from . import normalize

BASE = "https://api.smartrecruiters.com/v1/companies"


def _strip_html(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _detail_text(company, posting_id, log):
    """Full JD. One extra call per posting, so callers bound how many."""
    try:
        r = requests.get(f"{BASE}/{company}/postings/{posting_id}", timeout=30)
        r.raise_for_status()
        ad = (r.json().get("jobAd") or {}).get("sections") or {}
        parts = [(ad.get(k) or {}).get("text", "")
                 for k in ("companyDescription", "jobDescription",
                           "qualifications", "additionalInformation")]
        return _strip_html(" ".join(p for p in parts if p))
    except Exception as e:
        log(f"smartrecruiters: detail for {company}/{posting_id} failed ({e})")
        return ""


def fetch(config, log=print):
    cfg = config.get("smartrecruiters", {}) or {}
    companies = cfg.get("companies", []) or []
    if not companies:
        log("smartrecruiters: no companies configured — skipping")
        return []
    limit = int(cfg.get("limit", 100))
    detail_n = int(cfg.get("detail_top_n", 5))

    rows = []
    for company in companies:
        try:
            r = requests.get(f"{BASE}/{company}/postings",
                             params={"limit": limit}, timeout=30)
            r.raise_for_status()
            postings = r.json().get("content", [])
        except Exception as e:
            log(f"smartrecruiters: '{company}' failed ({e}) — skipping")
            continue

        for i, p in enumerate(postings):
            loc = p.get("location") or {}
            location = ", ".join(
                x for x in (loc.get("city"), loc.get("region"),
                            loc.get("country")) if x)
            # Politeness: full JD for the first few only; the rest score on
            # title + location until they reach the top of a queue.
            desc = (_detail_text(company, p.get("id"), log)
                    if i < detail_n else "")
            if i < detail_n:
                time.sleep(0.5)
            rows.append(normalize(
                source="smartrecruiters",
                company=(p.get("company") or {}).get("name") or company,
                title=p.get("name", ""),
                location=location,
                description=desc or p.get("name", ""),
                url=(f"https://jobs.smartrecruiters.com/{company}/"
                     f"{p.get('id', '')}"),
                updated_at=p.get("releasedDate", ""),
            ))
        time.sleep(1.0)
    log(f"smartrecruiters: {len(rows)} listings")
    return rows
