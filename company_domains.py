"""company_domains.py — backfills company.domain, needed before careers@
addresses can be derived at all (careers_inbox.py) or before a company can
be looked up by domain (recruiter_mine.py).

Two tiers, cheapest first:
  1. FREE guess + verify: slugify the company name, try it against .com/.in,
     confirm the domain actually resolves (DNS) AND its homepage plausibly
     mentions the company name (HTTP GET, checks <title>/meta description).
     Zero API cost, no rate limit — this is expected to resolve the large
     majority of real companies.
  2. SerpApi fallback (quota-tracked, separate small cap from
     interview_research.py's own company-research budget so this backfill
     can't crowd out either job search or interview prep): only used when
     tier 1 fails to find a plausible match.

A domain is only written when the homepage check passes — a DNS-resolving
domain alone proves nothing (parked domains, squatters, unrelated
businesses with a similar name all resolve fine). Getting this wrong has
real consequences: careers_inbox.py sends real emails to whatever domain
sits here. `domain_source` is logged per company (not stored — company has
no column for it) so a backfill run's output is auditable.
"""
from __future__ import annotations

import json
import os
import re
import socket

import requests

import outreach_store as store

# Corporate suffixes that would break a naive domain guess if left in —
# "Razorpay Software Private Limited" needs to become "razorpay", not
# "razorpaysoftwareprivatelimited".
_SUFFIX_RE = re.compile(
    r"\b(pvt\.?|private|ltd\.?|limited|inc\.?|llc|llp|corp\.?|corporation|"
    r"technologies|technology|solutions|india|group|holdings|co\.?)\b",
    re.I,
)

SERPAPI_USAGE_FILE = os.path.join(os.path.dirname(__file__), "data", "serpapi_usage.json")
DOMAIN_BACKFILL_MONTHLY_CAP = 30  # separate, small — same shared-quota discipline as interview_research.py


class DomainLookupUnavailable(Exception):
    pass


def slugify_company_name(name: str) -> str:
    cleaned = _SUFFIX_RE.sub(" ", name)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "", cleaned)
    return cleaned.lower()


def _domain_resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, None)
        return True
    except socket.gaierror:
        return False


def _homepage_mentions_company(domain: str, company_name: str, log=print) -> bool:
    """Cheap, free sanity check — fetches the homepage and looks for a
    significant word from the company name in the title/body. Not proof of
    identity, just a filter against parked domains and obviously-wrong
    matches (a resolving domain with unrelated content)."""
    significant_words = [w for w in re.split(r"\s+", company_name.lower())
                          if len(w) >= 4 and w not in ("private", "limited", "group")]
    if not significant_words:
        return False
    try:
        r = requests.get(f"https://{domain}", timeout=8,
                          headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        text = r.text.lower()[:20000]
    except Exception as e:
        log(f"company_domains: homepage check failed for {domain} ({e})")
        return False
    return any(w in text for w in significant_words)


def _free_tier_guess(company_name: str, log=print) -> str | None:
    slug = slugify_company_name(company_name)
    if not slug:
        return None
    for tld in ("com", "in"):
        domain = f"{slug}.{tld}"
        if _domain_resolves(domain) and _homepage_mentions_company(domain, company_name, log):
            log(f"company_domains: {company_name!r} -> {domain} (free guess, verified)")
            return domain
    return None


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


def _serpapi_domain_lookup(company_name: str, log=print) -> str | None:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        return None

    month = _current_month()
    usage = _load_usage()
    if usage.get("month") != month:
        usage = {"month": month, "count": 0, "company_research_count": 0,
                  "domain_backfill_count": 0}
    usage.setdefault("count", 0)
    usage.setdefault("company_research_count", 0)
    usage.setdefault("domain_backfill_count", 0)

    if usage["domain_backfill_count"] >= DOMAIN_BACKFILL_MONTHLY_CAP:
        log(f"company_domains: monthly SerpApi cap for domain backfill reached "
            f"({usage['domain_backfill_count']}/{DOMAIN_BACKFILL_MONTHLY_CAP}) — skipping")
        return None
    combined = usage["count"] + usage["company_research_count"] + usage["domain_backfill_count"]
    if combined >= 250 - 20:
        log("company_domains: shared SerpApi account quota nearly exhausted — skipping")
        return None

    try:
        r = requests.get("https://serpapi.com/search", params={
            "engine": "google", "q": f"{company_name} official website",
            "google_domain": "google.com", "gl": "in", "hl": "en", "api_key": api_key,
        }, timeout=30)
        usage["domain_backfill_count"] += 1
        _save_usage(usage)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log(f"company_domains: SerpApi lookup failed for {company_name!r} ({e})")
        return None

    for result in (data.get("organic_results") or [])[:3]:
        link = result.get("link", "")
        m = re.match(r"https?://(?:www\.)?([^/]+)", link)
        if not m:
            continue
        domain = m.group(1)
        if _homepage_mentions_company(domain, company_name, log):
            log(f"company_domains: {company_name!r} -> {domain} (SerpApi, verified)")
            return domain
    return None


def backfill_company_domains(conn, limit=None, use_serpapi=True, log=print):
    """Fills company.domain for every row where it's currently NULL. Returns
    {"filled": n, "unresolved": n} — unresolved companies are left alone,
    not written with a guess that failed verification."""
    rows = conn.execute(
        "SELECT id, name FROM company WHERE domain IS NULL ORDER BY id").fetchall()
    if limit:
        rows = rows[:limit]

    filled = unresolved = 0
    for row in rows:
        domain = _free_tier_guess(row["name"], log)
        if not domain and use_serpapi:
            domain = _serpapi_domain_lookup(row["name"], log)
        if domain:
            conn.execute("UPDATE company SET domain = ? WHERE id = ?", (domain, row["id"]))
            filled += 1
        else:
            unresolved += 1
            log(f"company_domains: no verified domain found for {row['name']!r}")

    log(f"company_domains: backfill complete — {filled} filled, {unresolved} unresolved")
    return {"filled": filled, "unresolved": unresolved}
