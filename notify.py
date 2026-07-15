"""Telegram digest. Skips silently when TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID are unset."""
import os

import requests

MAX_LEN = 3900  # Telegram hard limit is 4096; leave headroom


def send_digest(jobs: list, config: dict, log=print):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("telegram: token/chat id not set — skipping digest")
        return

    top_n = int(config.get("digest", {}).get("top_n", 15))
    top = jobs[:top_n]
    if not top:
        _send(token, chat_id, "Job scan: no new matching jobs today.", log)
        return

    lines = [f"🎯 Job scan — {len(jobs)} new matches, top {len(top)}:", ""]
    for i, j in enumerate(top, 1):
        lines.append(f"{i}. [{j['score']}] {j['title']} — {j['company']} "
                     f"({j.get('location', '')})\n{j['url']}")
    text = "\n".join(lines)

    # chunk politely under the message limit
    chunk, chunks = "", []
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > MAX_LEN:
            chunks.append(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        chunks.append(chunk)
    for c in chunks:
        _send(token, chat_id, c, log)


def _send(token, chat_id, text, log):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text,
                  "disable_web_page_preview": True},
            timeout=30)
        r.raise_for_status()
    except Exception as e:
        log(f"telegram: send failed ({e})")
