"""Gmail OAuth for A8/A9 (outreach.py/outreach_crm.py/outreach_send.py) —
F1/F7 boundary lives here.

F1, renegotiated 2026-08-10 (see CLAUDE.md's entry that day for the full
reasoning): the send scope is now requested, because Mehul asked for a
one-click batch-review flow (outreach_send.py + streamlit_app.py's
"Outreach review" tab) instead of full unattended auto-send, which was
explicitly considered and declined — a bad cold email to a real hiring
contact can't be unsent, unlike a bad row in a CSV. The guard that used to
be "this scope must never appear anywhere" is now a narrower, still-real
one: send() is only ever CALLED from outreach_send.py's single function,
which refuses to run without an explicit confirmed=True set by a real
human clicking Approve — never a default, never reachable from anywhere
else. career_agent_smoke_test.py's F1 check enforces this as a whitelist.
SCOPES below is the only place scopes are defined.

If this scope's presence here is ever a problem again (e.g. a compose-only
mode is wanted back), the token needs re-consent either way — deleting
~/.career_agent/token.json and re-running get_credentials() is what
picks up a SCOPES change; Google does not silently upgrade or downgrade a
cached token's scope on refresh.

F7: the token and OAuth client secret live ONLY on this machine
(~/.career_agent/), mode 600 where the OS supports it (Windows NTFS ACLs
don't map cleanly onto POSIX chmod — os.chmod is called best-effort and a
warning is printed if it can't verify the restriction actually took, rather
than silently claiming a guarantee that isn't there). Never read from an
environment variable, never committed, never available to the CI workflow
— get_service() raises immediately if CI is set, matching outreach.py's own
guard.
"""
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

CAREER_AGENT_DIR = os.path.join(os.path.expanduser("~"), ".career_agent")
CREDENTIALS_PATH = os.path.join(CAREER_AGENT_DIR, "credentials.json")
TOKEN_PATH = os.path.join(CAREER_AGENT_DIR, "token.json")


def _restrict_permissions(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if os.name == "nt":
        print(f"  (note: {os.path.basename(path)} permissions are best-effort on Windows — "
              f"NTFS ACLs don't map to chmod 600. Keep this machine's user account private.)")


def get_credentials():
    """Interactive on first run (opens your default browser for you to sign
    in and consent — this script never sees or handles your Google
    password). Silent, cached refresh on every run after that."""
    if os.environ.get("CI"):
        raise RuntimeError("Gmail auth refused: CI env var is set. Outreach is local-only (F7).")

    if not os.path.exists(CREDENTIALS_PATH):
        raise FileNotFoundError(
            f"{CREDENTIALS_PATH} not found. Download an OAuth 'Desktop app' client from "
            f"Google Cloud Console (APIs & Services > Credentials) and save it there."
        )

    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"  Token refresh failed ({e}); re-running the consent flow.")
                creds = None

        if not creds or not creds.valid:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                msg = str(e)
                if "access_denied" in msg or "admin_policy_enforced" in msg:
                    print(
                        "\nGoogle refused this consent (access_denied / admin_policy_enforced).\n"
                        "If this Gmail account is on a Google Workspace domain, the domain admin\n"
                        "may have blocked this app's scopes — falling back to writing outreach as\n"
                        ".eml files under out/drafts/ instead. The pipeline stays fully functional\n"
                        "with zero Gmail access; import those .eml files into any mail client by hand.\n"
                    )
                raise

        os.makedirs(CAREER_AGENT_DIR, exist_ok=True)
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        _restrict_permissions(TOKEN_PATH)

    return creds


def get_service():
    return build("gmail", "v1", credentials=get_credentials())


if __name__ == "__main__":
    print("Running the one-time Gmail consent flow...")
    try:
        get_credentials()
    except FileNotFoundError as e:
        print(f"\n{e}", file=sys.stderr)
        sys.exit(1)
    print(f"Done. Token cached at {TOKEN_PATH} — future runs won't need the browser again.")
