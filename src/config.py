# ============================================================
#  config.py — central configuration
#
#  Secrets are loaded from a .env file (gitignored), NOT hardcoded.
#  Copy .env.example -> .env and fill in your values.
# ============================================================

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Repo root is the parent of src/ (this file lives in src/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _abspath(path: str) -> str:
    """Resolve a possibly-relative path against the repo root so the app
    works regardless of which directory it's launched from."""
    p = Path(path)
    return str(p if p.is_absolute() else PROJECT_ROOT / p)


def _require(name: str) -> str:
    """Fetch a required env var or raise a clear error."""
    val = os.getenv(name, "").strip()
    if not val or val.upper().startswith("REPLACE"):
        raise RuntimeError(
            f"Missing required environment variable '{name}'. "
            f"Copy .env.example to .env and set it. See README.md."
        )
    return val


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# ── Secrets (from .env) ───────────────────────────────────────
PROSPEO_API_KEY = _require("PROSPEO_API_KEY")

# Google Gemini key (aistudio.google.com) — the project's only LLM, on the
# FREE tier. Powers both the daily fit-ranking and the cold-email writer.
# Strongly recommended, but optional: without it, ranking falls back to the
# free deterministic keyword scorer and outreach uses a plain template.
GEMINI_API_KEY = _optional("GEMINI_API_KEY")
GEMINI_MODEL = _optional("GEMINI_MODEL", "gemini-2.5-flash-lite")

# ── Gmail OAuth ──────────────────────────────────────────────
GMAIL_CREDENTIALS_PATH = _abspath(_optional("GMAIL_CREDENTIALS_PATH", "assets/gmail_credentials.json"))
GMAIL_TOKEN_PATH = _abspath(_optional("GMAIL_TOKEN_PATH", "assets/gmail_token.json"))
SENDER_EMAIL = _require("SENDER_EMAIL")
# Optional Gmail App Password (myaccount.google.com → Security → App passwords).
# When set, mail goes out via SMTP (smtp.gmail.com:465) instead of the Gmail
# API — no OAuth browser flow, so it works headless (e.g. GitHub Actions).
GMAIL_APP_PASSWORD = _optional("GMAIL_APP_PASSWORD").replace(" ", "")

# ── Google Sheets ────────────────────────────────────────────
SHEETS_SERVICE_ACCOUNT_PATH = _abspath(_optional("SHEETS_SERVICE_ACCOUNT_PATH", "assets/sheets_service_account.json"))
SHEETS_SPREADSHEET_ID = _require("SHEETS_SPREADSHEET_ID")
SHEETS_WORKSHEET_NAME = _optional("SHEETS_WORKSHEET_NAME", "Applications")

# Optional Apps Script web-app URL that logs a card click to the "Job" sheet
# then redirects to the real posting. If empty, card links go straight to the job.
# See docs/CLICK_TRACKER.md for the 5-minute setup.
CLICK_TRACKER_URL = _optional("CLICK_TRACKER_URL", "")

# ── Resume & Cover Letter ────────────────────────────────────
RESUME_PATH = _abspath(_optional("RESUME_PATH", "assets/resume.pdf"))
COVER_LETTER_PATH = _abspath(_optional("COVER_LETTER_PATH", "assets/cover_letter.pdf"))

# ── Job Search Settings ──────────────────────────────────────
JOB_MAX_AGE_HOURS = int(_optional("JOB_MAX_AGE_HOURS", "24"))
MAX_JOBS_PER_RUN = int(_optional("MAX_JOBS_PER_RUN", "10"))
JOB_SEARCH_TERMS = [
    "software engineer",
    "software developer",
    "backend engineer",
    "full stack engineer",
    "frontend engineer",
]
JOB_LOCATIONS = ["United States"]

# ── Email Settings ───────────────────────────────────────────
# Cold emails are written by Gemini (see GEMINI_MODEL above); no separate
# model setting is needed.
EMAIL_SEND_DELAY_SECONDS = int(_optional("EMAIL_SEND_DELAY_SECONDS", "5"))
DRY_RUN = _optional("DRY_RUN", "false").lower() in ("1", "true", "yes")
# Only send to Prospeo-VERIFIED emails (avoids bounces from stale/guessed addresses)
VERIFIED_ONLY = _optional("VERIFIED_ONLY", "true").lower() in ("1", "true", "yes")

# ── Your profile (used in email generation) ──────────────────
YOUR_NAME = _optional("YOUR_NAME", "Your Name")
YOUR_PHONE = _optional("YOUR_PHONE", "")
YOUR_LINKEDIN = _optional("YOUR_LINKEDIN", "")
YOUR_GITHUB = _optional("YOUR_GITHUB", "")
YOUR_EMAIL_PRIMARY = _optional("YOUR_EMAIL_PRIMARY", SENDER_EMAIL)
YOUR_EMAIL_ALT = _optional("YOUR_EMAIL_ALT", "")
YOUR_BIO = _optional(
    "YOUR_BIO",
    "a new grad software engineer with experience in Python, JavaScript, "
    "and cloud technologies, seeking entry-level SWE roles",
)
