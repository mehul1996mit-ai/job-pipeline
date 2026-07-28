"""Job-alert email ingestion — the legitimate route to Naukri, LinkedIn,
Indeed, Shine, foundit and any other portal that emails you alerts.

WHY THIS EXISTS. Those portals have no public API, and scraping them violates
their terms and risks the accounts Mehul actively job-hunts with — see the
design boundary in README.md and GCC_COVERAGE_GUIDE.md. But they all happily
email job alerts, and *his own inbox is his own data*. Reading it involves no
bot detection, no account risk, and no third-party terms: the portal sent the
mail deliberately.

This reads an IMAP mailbox READ-ONLY, parses job links out of alert emails, and
normalizes them into the same schema as every other source. It never sends,
never replies, never deletes, and never marks anything read.

SETUP (one-time):
  1. Create job alerts on Naukri / LinkedIn / Indeed / wherever, delivered to
     a Gmail address.
  2. Optional but recommended: a Gmail filter putting them in a label such as
     `job-alerts`, so this only ever touches those messages.
  3. Generate a Gmail App Password (Google Account -> Security -> 2-Step
     Verification -> App passwords). A normal account password will not work
     and should not be used.
  4. Set JOB_ALERT_EMAIL and JOB_ALERT_APP_PASSWORD as secrets, and enable the
     source in config.yaml under `job_alert_email`.

Credentials are read from the environment only — never from config, never
committed. With them unset the source logs that it's disabled and returns [],
exactly like the other keyless sources.
"""
from __future__ import annotations

import email
import html
import imaplib
import os
import re
from email.header import decode_header, make_header

from . import normalize

# Which portal a job link belongs to, by host. Order matters only for display.
PORTAL_HOSTS = [
    ("naukri", re.compile(r"naukri\.com", re.I)),
    ("linkedin", re.compile(r"linkedin\.com", re.I)),
    ("indeed", re.compile(r"indeed\.co", re.I)),
    ("shine", re.compile(r"shine\.com", re.I)),
    ("foundit", re.compile(r"foundit\.in|monsterindia\.com", re.I)),
    ("timesjobs", re.compile(r"timesjobs\.com", re.I)),
    ("iimjobs", re.compile(r"iimjobs\.com", re.I)),
    ("hirist", re.compile(r"hirist\.(com|tech)", re.I)),
    ("instahyre", re.compile(r"instahyre\.com", re.I)),
    ("cutshort", re.compile(r"cutshort\.io", re.I)),
    ("wellfound", re.compile(r"wellfound\.com|angel\.co", re.I)),
    ("glassdoor", re.compile(r"glassdoor\.co", re.I)),
]

# Links that are never a job posting, however they're dressed up.
NOISE_URL_RE = re.compile(
    r"unsubscribe|/settings|/preferences|privacy|help|support|/login|/signin"
    r"|facebook\.com|twitter\.com|x\.com|instagram\.com|youtube\.com"
    r"|play\.google\.com|apps\.apple\.com|\.(png|jpg|jpeg|gif|css|js)(\?|$)",
    re.I)

# A posting URL on the major portals. Kept deliberately loose — these change
# their URL shapes often, and a missed link is just a missed job.
JOB_URL_RE = re.compile(
    r"https?://[^\s\"'<>]*?(?:"
    r"naukri\.com/job-listings[^\s\"'<>]*"
    r"|linkedin\.com/jobs/view/\d+[^\s\"'<>]*"
    r"|indeed\.co[^\s\"'<>]*?(?:viewjob|/rc/clk)[^\s\"'<>]*"
    r"|shine\.com/jobs/[^\s\"'<>]*"
    r"|foundit\.in/job/[^\s\"'<>]*"
    r"|timesjobs\.com/job-detail/[^\s\"'<>]*"
    r"|iimjobs\.com/j/[^\s\"'<>]*"
    r"|hirist\.(?:com|tech)/j/[^\s\"'<>]*"
    r"|instahyre\.com/job-[^\s\"'<>]*"
    r"|cutshort\.io/job/[^\s\"'<>]*"
    r"|wellfound\.com/jobs/[^\s\"'<>]*"
    r")", re.I)

_TAG_RE = re.compile(r"<[^>]+>")


def _portal_of(url: str) -> str:
    for name, rx in PORTAL_HOSTS:
        if rx.search(url):
            return name
    return "email-alert"


def _decode(raw) -> str:
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return str(raw or "")


def _body_text(msg) -> str:
    """Concatenate every text/plain and text/html part, HTML stripped."""
    chunks = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        if "attachment" in str(part.get("Content-Disposition") or ""):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8",
                                  errors="replace")
        except Exception:
            continue
        chunks.append(text)
    return "\n".join(chunks)


def _anchor_titles(raw_html: str) -> dict:
    """Map job URL -> the anchor text that linked it, which on every one of
    these portals is the job title. Falls back to the URL slug when the anchor
    is an image or a bare 'View job' button."""
    out = {}
    for m in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                         raw_html, re.I | re.S):
        url, inner = m.group(1), m.group(2)
        if not JOB_URL_RE.match(url):
            continue
        text = html.unescape(_TAG_RE.sub(" ", inner))
        text = re.sub(r"\s+", " ", text).strip()
        if text and len(text) > 3 and not re.fullmatch(
                r"(view|apply|see|click)[\w\s]*", text, re.I):
            out.setdefault(url, text)
    return out


def _title_from_url(url: str) -> str:
    slug = re.sub(r"[?#].*$", "", url).rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"-\d{5,}$", "", slug)            # trailing posting id
    slug = re.sub(r"[-_]+", " ", slug).strip()
    return slug.title()[:120] if len(slug) > 3 else "Job from alert email"


def fetch(config, log=print):
    cfg = config.get("job_alert_email") or {}
    if not cfg.get("enabled"):
        log("job_alert_email: disabled in config — skipping")
        return []

    user = os.environ.get("JOB_ALERT_EMAIL", "")
    password = os.environ.get("JOB_ALERT_APP_PASSWORD", "")
    if not user or not password:
        log("job_alert_email: JOB_ALERT_EMAIL / JOB_ALERT_APP_PASSWORD not "
            "set — skipping")
        return []

    host = cfg.get("imap_host", "imap.gmail.com")
    folder = cfg.get("folder", "INBOX")
    days = int(cfg.get("lookback_days", 2))
    max_msgs = int(cfg.get("max_messages", 60))

    rows, seen_urls = [], set()
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(host)
        conn.login(user, password)
        # READONLY: never marks anything read, never mutates the mailbox.
        conn.select(f'"{folder}"', readonly=True)

        from datetime import date, timedelta
        since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = conn.search(None, f'(SINCE {since})')
        if typ != "OK":
            log(f"job_alert_email: search failed ({typ}) — skipping")
            return []
        ids = (data[0] or b"").split()[-max_msgs:]
        log(f"job_alert_email: scanning {len(ids)} message(s) in "
            f"'{folder}' since {since}")

        for mid in ids:
            typ, msg_data = conn.fetch(mid, "(BODY.PEEK[])")   # PEEK = no \Seen
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode(msg.get("Subject"))
            sent = _decode(msg.get("Date"))
            body = _body_text(msg)
            titles = _anchor_titles(body)

            for m in JOB_URL_RE.finditer(body):
                url = html.unescape(m.group(0)).rstrip(").,;'\"")
                if NOISE_URL_RE.search(url):
                    continue
                key = re.sub(r"[?#].*$", "", url)
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                portal = _portal_of(url)
                rows.append(normalize(
                    source=f"email:{portal}",
                    company="",                 # alert emails rarely state it
                    title=titles.get(url) or _title_from_url(url),
                    location="",
                    # No JD text: the email only carries a link, and following
                    # it would be the scraping this source exists to avoid.
                    # These score on title alone and rank accordingly.
                    description=f"From job alert email: {subject}",
                    url=url,
                    updated_at=sent,
                ))
    except imaplib.IMAP4.error as e:
        log(f"job_alert_email: IMAP error ({e}) — skipping. Check the app "
            f"password and that IMAP is enabled.")
        return []
    except Exception as e:
        log(f"job_alert_email: failed ({e}) — skipping")
        return []
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass

    log(f"job_alert_email: {len(rows)} listing(s) from alert emails")
    return rows
