"""Lever public postings API. Ships with an EMPTY token list — add only
confirmed company tokens to config.yaml (lever.tokens).

GET https://api.lever.co/v0/postings/{token}?mode=json
"""
import time

import requests

from . import normalize


def fetch(config, log=print):
    tokens = config.get("lever", {}).get("tokens", []) or []
    if not tokens:
        log("lever: no company tokens configured — skipping")
        return []
    rows = []
    for token in tokens:
        try:
            r = requests.get(
                f"https://api.lever.co/v0/postings/{token}",
                params={"mode": "json"}, timeout=30)
            r.raise_for_status()
            jobs = r.json()
        except Exception as e:
            log(f"lever: '{token}' failed ({e}) — skipping")
            continue
        for j in jobs:
            cats = j.get("categories") or {}
            rows.append(normalize(
                source="lever",
                company=token,
                title=j.get("text", ""),
                location=cats.get("location", ""),
                description=j.get("descriptionPlain", ""),
                url=j.get("hostedUrl", ""),
                updated_at=str(j.get("createdAt", "")),
            ))
        time.sleep(1.0)
    log(f"lever: {len(rows)} listings")
    return rows
