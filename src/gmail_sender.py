# ============================================================
#  gmail_sender.py
#  Sends emails via Gmail API with resume + cover letter
#  attachments. Handles OAuth2 token creation / refresh.
# ============================================================

from __future__ import annotations

import base64
import os
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

# Only send permission — minimal OAuth scope
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _get_gmail_service():
    """Return an authenticated Gmail API service."""
    creds: Optional[Credentials] = None

    token_path = config.GMAIL_TOKEN_PATH
    creds_path = config.GMAIL_CREDENTIALS_PATH

    # Load existing token
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # Refresh or run OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"Gmail credentials not found at '{creds_path}'.\n"
                    "See SETUP.md → Step 2 for instructions."
                )
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            # Opens browser for one-time OAuth consent
            creds = flow.run_local_server(port=0)

        # Save token for next run
        Path(token_path).parent.mkdir(parents=True, exist_ok=True)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _build_message(
    to_email: str,
    to_name: str,
    subject: str,
    body: str,
) -> MIMEMultipart:
    """Build a MIME email with resume + cover letter as attachments."""
    msg = MIMEMultipart()
    msg["To"] = f"{to_name} <{to_email}>"
    msg["From"] = config.SENDER_EMAIL
    msg["Subject"] = subject

    # Plain-text body
    msg.attach(MIMEText(body, "plain"))

    # Attach resume
    resume_path = Path(config.RESUME_PATH)
    if resume_path.exists():
        with open(resume_path, "rb") as f:
            part = MIMEApplication(f.read(), _subtype="pdf")
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=resume_path.name,
            )
            msg.attach(part)
    else:
        print(f"  ⚠ Resume not found at {config.RESUME_PATH} — sending without attachment")

    # Attach cover letter
    cl_path = Path(config.COVER_LETTER_PATH)
    if cl_path.exists():
        with open(cl_path, "rb") as f:
            part = MIMEApplication(f.read(), _subtype="pdf")
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=cl_path.name,
            )
            msg.attach(part)
    else:
        print(f"  ⚠ Cover letter not found at {config.COVER_LETTER_PATH} — sending without")

    return msg


def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    body: str,
    dry_run: bool = False,
) -> bool:
    """
    Send an email via Gmail API.
    Returns True on success, False on failure.
    If dry_run=True, prints the email instead of sending.
    """
    if dry_run:
        print(f"\n{'─'*60}")
        print(f"[DRY RUN] Would send to: {to_name} <{to_email}>")
        print(f"Subject : {subject}")
        print(f"Body:\n{body}")
        print(f"Attachments: {config.RESUME_PATH}, {config.COVER_LETTER_PATH}")
        print(f"{'─'*60}")
        return True

    try:
        service = _get_gmail_service()
        mime_msg = _build_message(to_email, to_name, subject, body)

        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()
        result = service.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        print(f"  📧 Sent! Message ID: {result.get('id')}")
        return True

    except HttpError as e:
        print(f"  ❌ Gmail API error: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Send error: {e}")
        return False


def send_batch(outreach_list: list[dict], dry_run: bool = False) -> list[dict]:
    """
    Send a list of outreach emails with rate-limiting.
    Each item must have: to_email, to_name, subject, body.
    Returns the list annotated with 'sent': True/False.
    """
    results = []
    for i, item in enumerate(outreach_list, 1):
        print(f"\n[{i}/{len(outreach_list)}] → {item['to_name']} <{item['to_email']}>")
        success = send_email(
            to_email=item["to_email"],
            to_name=item["to_name"],
            subject=item["subject"],
            body=item["body"],
            dry_run=dry_run,
        )
        results.append({**item, "sent": success})

        if i < len(outreach_list):
            time.sleep(config.EMAIL_SEND_DELAY_SECONDS)

    return results


# ── Quick test (dry run only) ─────────────────────────────────
if __name__ == "__main__":
    send_email(
        to_email="test@example.com",
        to_name="Test Recruiter",
        subject="Test subject",
        body="This is a test email body.",
        dry_run=True,
    )
