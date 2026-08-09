"""F4 volume caps for outreach (A8, not yet built) — clamped here so a
config.yaml value can never raise them. Import MAX_* from this module rather
than reading caps out of config.yaml directly.
"""
import datetime

MAX_DRAFTS_PER_DAY = 20
MAX_DAYS_BETWEEN_OUTREACH_SAME_COMPANY = 21
MAX_FOLLOWUPS_PER_THREAD = 2
SUPPRESSION_DAYS_AFTER_CLOSE = 180


def clamp(configured_value, ceiling):
    """A config.yaml value can only ever lower a cap, never raise it."""
    if configured_value is None:
        return ceiling
    return min(configured_value, ceiling)


def drafts_created_today(conn, today=None):
    today = today or datetime.date.today().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM outreach WHERE substr(created_at, 1, 10) = ?",
        (today,),
    ).fetchone()
    return row["n"]


def can_draft_today(conn, configured_daily_cap=None, today=None):
    cap = clamp(configured_daily_cap, MAX_DRAFTS_PER_DAY)
    return drafts_created_today(conn, today) < cap


def days_since_last_outreach(conn, company_id, now=None):
    now = now or datetime.datetime.utcnow()
    row = conn.execute(
        "SELECT MAX(created_at) AS last FROM outreach WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    if not row or not row["last"]:
        return None
    last = datetime.datetime.fromisoformat(row["last"])
    return (now - last).days


def can_draft_for_company(conn, company_id, configured_cooldown_days=None, now=None):
    cooldown = clamp(configured_cooldown_days, MAX_DAYS_BETWEEN_OUTREACH_SAME_COMPANY)
    days_since = days_since_last_outreach(conn, company_id, now)
    return days_since is None or days_since >= cooldown


def can_followup(followup_count, configured_max=None):
    cap = clamp(configured_max, MAX_FOLLOWUPS_PER_THREAD)
    return followup_count < cap
