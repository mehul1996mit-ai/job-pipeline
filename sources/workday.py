"""Workday CXS public JSON feeds for GCC employers.

POST https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
body: {"appliedFacets": {}, "limit": N, "offset": 0, "searchText": "..."}

Small pages (20), one poll per scheduled run, skip failures gracefully —
some instances are bot-protected and will 4xx/timeout; that's expected.

fetch_job_detail() GETs /wday/cxs/{tenant}/{site}/job/{externalPath} for the
full job description (used for the top-N matches only, politeness cap).
"""
import re
import time

import requests

from . import normalize

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (job-pipeline; personal job search tool)",
}

COMPANY_NAMES = {"citi": "Citi", "db": "Deutsche Bank", "wf": "Wells Fargo"}


def _base(tenant_cfg):
    return (f"https://{tenant_cfg['tenant']}.{tenant_cfg['server']}"
            f".myworkdayjobs.com")


def _strip_html(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def fetch(config, log=print):
    cfg = config.get("workday", {})
    page_size = int(cfg.get("page_size", 20))
    rows = []
    for tenant_cfg in cfg.get("tenants", []):
        tenant, site = tenant_cfg["tenant"], tenant_cfg["site"]
        endpoint = (f"{_base(tenant_cfg)}/wday/cxs/{tenant}/{site}/jobs")
        company = COMPANY_NAMES.get(tenant, tenant)
        for search_text in tenant_cfg.get("searches", []):
            try:
                r = requests.post(
                    endpoint,
                    json={"appliedFacets": {}, "limit": page_size,
                          "offset": 0, "searchText": search_text},
                    headers=HEADERS, timeout=30)
                r.raise_for_status()
                postings = r.json().get("jobPostings", [])
            except Exception as e:
                log(f"workday: {tenant}/'{search_text}' failed ({e}) — "
                    "skipping (likely protected instance)")
                continue
            for p in postings:
                ext = p.get("externalPath", "")
                if not ext:
                    continue
                rows.append(normalize(
                    source="workday",
                    company=company,
                    title=p.get("title", ""),
                    location=p.get("locationsText", ""),
                    description=" ".join(p.get("bulletFields", [])),
                    url=f"{_base(tenant_cfg)}/en-US/{site}{ext}",
                    updated_at=p.get("postedOn", ""),
                    workday_tenant=tenant_cfg,
                    workday_external_path=ext,
                ))
            time.sleep(1.5)  # politeness between searches
    log(f"workday: {len(rows)} listings")
    return rows


def fetch_job_detail(tenant_cfg, external_path, log=print):
    """GET the full JD for one posting. Returns plain text or ''. """
    tenant, site = tenant_cfg["tenant"], tenant_cfg["site"]
    path = external_path if external_path.startswith("/") else "/" + external_path
    url = f"{_base(tenant_cfg)}/wday/cxs/{tenant}/{site}{path}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        info = r.json().get("jobPostingInfo", {})
        return _strip_html(info.get("jobDescription", ""))
    except Exception as e:
        log(f"workday detail: {url} failed ({e})")
        return ""
