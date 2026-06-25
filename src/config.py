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
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")
PROSPEO_API_KEY = _require("PROSPEO_API_KEY")

# ── Gmail OAuth ──────────────────────────────────────────────
GMAIL_CREDENTIALS_PATH = _abspath(_optional("GMAIL_CREDENTIALS_PATH", "assets/gmail_credentials.json"))
GMAIL_TOKEN_PATH = _abspath(_optional("GMAIL_TOKEN_PATH", "assets/gmail_token.json"))
SENDER_EMAIL = _require("SENDER_EMAIL")

# ── Google Sheets ────────────────────────────────────────────
SHEETS_SERVICE_ACCOUNT_PATH = _abspath(_optional("SHEETS_SERVICE_ACCOUNT_PATH", "assets/sheets_service_account.json"))
SHEETS_SPREADSHEET_ID = _require("SHEETS_SPREADSHEET_ID")
SHEETS_WORKSHEET_NAME = _optional("SHEETS_WORKSHEET_NAME", "Applications")

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
# Model used for email generation (bulk → Haiku by default; override in .env)
EMAIL_MODEL = _optional("EMAIL_MODEL", "claude-haiku-4-5")
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
