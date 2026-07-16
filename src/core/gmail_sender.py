# ============================================================
#  gmail_sender.py
#  Sends emails via Gmail API with resume + cover letter
#  attachments. Handles OAuth2 token creation / refresh.
# ============================================================

from __future__ import annotations

import base64
import os
import random
import re
import smtplib
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

from src.core import config

# Only send permission — minimal OAuth scope
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

# Gmail's httplib2 transport isn't thread-safe, so each thread gets its own
# service. Credential loading/refresh is serialized with a lock.
_thread_local = threading.local()
_token_lock = threading.Lock()


def _load_credentials() -> Credentials:
    """Load or refresh OAuth credentials (serialized across threads)."""
    with _token_lock:
        creds: Optional[Credentials] = None
        token_path = config.GMAIL_TOKEN_PATH
        creds_path = config.GMAIL_CREDENTIALS_PATH

        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(creds_path):
                    raise FileNotFoundError(
                        f"Gmail credentials not found at '{creds_path}'.\n"
                        "See docs/SETUP.md → Step 3 for instructions."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
                creds = flow.run_local_server(port=0)  # one-time browser consent

            Path(token_path).parent.mkdir(parents=True, exist_ok=True)
            with open(token_path, "w") as f:
                f.write(creds.to_json())
        return creds


def _get_gmail_service():
    """Return an authenticated Gmail API service, cached per thread."""
    svc = getattr(_thread_local, "service", None)
    if svc is not None:
        return svc
    creds = _load_credentials()
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    _thread_local.service = svc
    return svc


def send_mime(msg) -> None:
    """Low-level send of a built MIME message; raises on failure.

    Routes via SMTP with the Gmail App Password when configured (headless —
    no OAuth browser flow, so it works in CI), else via the Gmail API.
    """
    if config.GMAIL_APP_PASSWORD:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(config.SENDER_EMAIL, config.GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        return
    service = _get_gmail_service()
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


# ── Pre-send guard ───────────────────────────────────────────
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_domain_cache: dict[str, bool] = {}


def validate_recipient(email: str) -> tuple[bool, str]:
    """Cheap sanity check before spending a send.

    Catches malformed addresses and domains that don't resolve (typos like
    @microsft.com). It CANNOT catch a wrong-but-plausible local part
    (jsmith@ vs john.smith@) — only a Prospeo-verified email guarantees that.
    """
    if not email or not _EMAIL_RE.match(email):
        return False, "invalid email syntax"
    domain = email.split("@")[1].lower()
    if domain not in _domain_cache:
        try:
            socket.getaddrinfo(domain, None)
            _domain_cache[domain] = True
        except OSError:
            _domain_cache[domain] = False
    return (_domain_cache[domain], "ok" if _domain_cache[domain] else "domain does not resolve")


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
    guard: bool = True,
) -> bool:
    """
    Send an email via Gmail API.
    Returns True on success, False on failure.
    If dry_run=True, prints the email instead of sending.
    If guard=True, skips addresses that fail a cheap syntax/domain check.
    """
    if dry_run:
        print(f"\n{'─'*60}")
        print(f"[DRY RUN] Would send to: {to_name} <{to_email}>")
        print(f"Subject : {subject}")
        print(f"Body:\n{body}")
        print(f"Attachments: {config.RESUME_PATH}, {config.COVER_LETTER_PATH}")
        print(f"{'─'*60}")
        return True

    if guard:
        ok, reason = validate_recipient(to_email)
        if not ok:
            print(f"  ⛔ Skipped {to_email}: {reason}")
            return False

    try:
        send_mime(_build_message(to_email, to_name, subject, body))
        print(f"  📧 Sent to {to_email}")
        return True

    except HttpError as e:
        print(f"  ❌ Gmail API error: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Send error: {e}")
        return False


def send_batch(
    outreach_list: list[dict],
    dry_run: bool = False,
    parallel: bool = False,
    workers: int = 4,
    jitter: float = 0.5,
) -> list[dict]:
    """
    Send a list of outreach emails. Each item needs: to_email, to_name, subject, body.
    Returns the list annotated with 'sent': True/False (in the original order).

    parallel=False (default): sequential, one send every EMAIL_SEND_DELAY_SECONDS.
    parallel=True: bounded thread pool of `workers` (I/O-bound → threads help).
        Keep `workers` small (3–4) and `jitter` > 0 so the burst stays under
        Gmail's ~2–3 sends/sec/user limit and doesn't look like a blast. Faster
        sending does NOT reduce spam risk — bounces and volume do (see docs/AGENT.md).
    """
    def _send(item: dict) -> bool:
        return send_email(item["to_email"], item["to_name"],
                          item["subject"], item["body"], dry_run=dry_run)

    if not parallel:
        results = []
        for i, item in enumerate(outreach_list, 1):
            print(f"\n[{i}/{len(outreach_list)}] → {item['to_name']} <{item['to_email']}>")
            results.append({**item, "sent": _send(item)})
            if i < len(outreach_list):
                time.sleep(config.EMAIL_SEND_DELAY_SECONDS)
        return results

    # Bounded-parallel path
    results: list[Optional[dict]] = [None] * len(outreach_list)

    def _worker(idx: int, item: dict) -> tuple[int, dict]:
        if jitter:
            time.sleep(random.uniform(0, jitter))  # de-synchronize the burst
        ok = _send(item)
        print(f"  {'✅' if ok else '❌'} {item['to_name']} <{item['to_email']}>")
        return idx, {**item, "sent": ok}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_worker, i, it) for i, it in enumerate(outreach_list)]
        for fut in as_completed(futures):
            idx, res = fut.result()
            results[idx] = res

    return [r for r in results if r is not None]


# ── Quick test (dry run only) ─────────────────────────────────
if __name__ == "__main__":
    send_email(
        to_email="test@example.com",
        to_name="Test Recruiter",
        subject="Test subject",
        body="This is a test email body.",
        dry_run=True,
    )
