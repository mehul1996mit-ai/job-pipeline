"""Follow-up nudges + weekly stats, computed from the queue CSVs.

Both are read-only over data/job_queue_*.csv and deliver via Telegram
(skipped silently when unset). Follow-up state persists in
data/followups.json so each application is nudged exactly once.

Status vocabulary in the `applied` column (edited via the UI):
  no | yes | skip | response | interview | rejected | offer
Follow-up nudges target plain "yes" — once you record any outcome
(response/interview/rejected/offer) the nudge is no longer relevant.
"""
from __future__ import annotations

import csv
import glob
import json
from datetime import date
from pathlib import Path

from notify import _send
import os

FOLLOWUP_STORE = Path("data/followups.json")

APPLIED_STATUSES = ["no", "yes", "skip", "response", "interview",
                    "rejected", "offer"]


def load_queue_rows(data_dir: str = "data") -> list[dict]:
    rows = []
    for path in sorted(glob.glob(f"{data_dir}/job_queue_*.csv")):
        qdate = Path(path).stem.replace("job_queue_", "")
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    r["_queue_date"] = qdate
                    rows.append(r)
        except Exception:
            continue
    return rows


# ------------------------------------------------------------- follow-ups
def followups_due(rows: list[dict], days: int = 7, today: date | None = None,
                  already: dict | None = None) -> list[dict]:
    """Applications marked plain 'yes' >= `days` ago with no recorded
    outcome and no prior nudge."""
    today = today or date.today()
    already = already or {}
    due = []
    for r in rows:
        if (r.get("applied") or "").strip().lower() != "yes":
            continue
        url = (r.get("url") or "").strip()
        if not url or url in already:
            continue
        basis = (r.get("applied_on") or "").strip() or r["_queue_date"]
        try:
            d0 = date.fromisoformat(basis[:10])
        except ValueError:
            continue
        age = (today - d0).days
        if age >= days:
            r["_age_days"] = age
            due.append(r)
    return due


def send_followups(config: dict, log=print):
    days = int(config.get("digest", {}).get("followup_days", 7))
    already = {}
    if FOLLOWUP_STORE.exists():
        try:
            already = json.loads(FOLLOWUP_STORE.read_text(encoding="utf-8"))
        except Exception:
            already = {}
    due = followups_due(load_queue_rows(), days=days, already=already)
    if not due:
        log("followups: none due")
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    lines = [f"⏰ Follow-up nudges — {len(due)} application(s) with no "
             f"recorded response after {days}+ days:", ""]
    for r in due[:15]:
        lines.append(f"• {r['title']} @ {r['company']} "
                     f"({r['_age_days']}d ago)\n{r['url']}")
    lines.append("")
    lines.append("Send a short follow-up (the outreach draft in the "
                 "dashboard is a good base), then set the row's status to "
                 "'response'/'rejected' as replies land.")
    if token and chat_id:
        _send(token, chat_id, "\n".join(lines), log)
    else:
        log("followups: telegram unset — printing instead")
        log("\n".join(lines))
    for r in due:
        already[r["url"]] = date.today().isoformat()
    FOLLOWUP_STORE.parent.mkdir(parents=True, exist_ok=True)
    FOLLOWUP_STORE.write_text(json.dumps(already, indent=1),
                              encoding="utf-8")
    log(f"followups: nudged {len(due)}")


# ----------------------------------------------------------- weekly stats
def _band(score) -> str:
    try:
        s = int(float(score))
    except (TypeError, ValueError):
        return "?"
    if s >= 80:
        return "80+"
    if s >= 65:
        return "65-79"
    if s >= 55:
        return "55-64"
    return "<55"


def weekly_stats(rows: list[dict]) -> str:
    total = len(rows)
    tailored = sum(1 for r in rows if (r.get("tailored_summary") or ""))
    status_counts: dict[str, int] = {}
    applied_rows = []
    for r in rows:
        s = (r.get("applied") or "no").strip().lower() or "no"
        status_counts[s] = status_counts.get(s, 0) + 1
        if s not in ("no", "skip"):
            applied_rows.append(r)

    lines = ["📊 Weekly job-search stats", "",
             f"Jobs surfaced: {total} | tailored: {tailored} | "
             f"applied: {len(applied_rows)}"]

    outcomes = {k: v for k, v in status_counts.items()
                if k in ("response", "interview", "rejected", "offer")}
    if applied_rows:
        responded = sum(v for k, v in outcomes.items()
                        if k in ("response", "interview", "offer"))
        lines.append(f"Outcomes: " + ", ".join(
            f"{k}: {v}" for k, v in sorted(outcomes.items()))
            if outcomes else "Outcomes: none recorded yet")
        lines.append(f"Positive-response rate: "
                     f"{responded}/{len(applied_rows)}")
        by_band: dict[str, list[int]] = {}
        by_source: dict[str, list[int]] = {}
        for r in applied_rows:
            pos = 1 if (r.get("applied") or "").lower() in (
                "response", "interview", "offer") else 0
            by_band.setdefault(_band(r.get("score")), []).append(pos)
            by_source.setdefault(r.get("source") or "?", []).append(pos)
        lines.append("Applied by score band: " + ", ".join(
            f"{b}: {sum(v)}/{len(v)} positive"
            for b, v in sorted(by_band.items(), reverse=True)))
        lines.append("Applied by source: " + ", ".join(
            f"{s}: {sum(v)}/{len(v)} positive"
            for s, v in sorted(by_source.items())))
        lines.append("")
        lines.append("Reading: if one band/source converts and another "
                     "doesn't, raise the tailor floor or shift effort "
                     "accordingly.")
    else:
        lines.append("No applications marked yet — flip `applied` in the "
                     "dashboard as you submit.")
    return "\n".join(lines)


def send_weekly_stats(config: dict, log=print):
    rows = load_queue_rows()
    if not rows:
        log("stats: no queue data yet")
        return
    text = weekly_stats(rows)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        _send(token, chat_id, text, log)
        log("stats: weekly digest sent")
    else:
        log("stats: telegram unset — printing instead")
        log(text)
