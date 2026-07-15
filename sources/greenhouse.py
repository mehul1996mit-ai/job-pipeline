"""Greenhouse public board API. Ships with an EMPTY token list — add only
confirmed company board tokens to config.yaml (greenhouse.tokens).

GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
"""
import html
import re
import time

import requests

from . import normalize


def _strip_html(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch(config, log=print):
    tokens = config.get("greenhouse", {}).get("tokens", []) or []
    if not tokens:
        log("greenhouse: no board tokens configured — skipping")
        return []
    rows = []
    for token in tokens:
        try:
            r = requests.get(
                f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                params={"content": "true"}, timeout=30)
            r.raise_for_status()
            jobs = r.json().get("jobs", [])
        except Exception as e:
            log(f"greenhouse: '{token}' failed ({e}) — skipping")
            continue
        for j in jobs:
            rows.append(normalize(
                source="greenhouse",
                company=token,
                title=j.get("title", ""),
                location=(j.get("location") or {}).get("name", ""),
                description=_strip_html(j.get("content", "")),
                url=j.get("absolute_url", ""),
                updated_at=j.get("updated_at", ""),
            ))
        time.sleep(1.0)
    log(f"greenhouse: {len(rows)} listings")
    return rows
