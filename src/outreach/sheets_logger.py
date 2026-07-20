# ============================================================
#  sheets_logger.py
#  Logs every outreach attempt to Google Sheets.
#  Also checks for duplicates before sending.
# ============================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from src.core import config

# Sheets columns (1-indexed header row)
HEADERS = [
    "Date Sent",          # A
    "Company",            # B
    "Job Title",          # C
    "Job URL",            # D
    "Date Posted",        # E
    "H1B Signal",         # F  "explicit" or "known sponsor"
    "Recruiter Name",     # G
    "Recruiter Title",    # H
    "Recruiter Email",    # I
    "Email Subject",      # J
    "Status",             # K  Sent / Replied / No Response / Bounced
    "Notes",              # L
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

_sheet: Optional[gspread.Worksheet] = None
_warned = False


def sheets_available() -> bool:
    """Sheets logging is configured only when both the spreadsheet ID and the
    service-account file exist. Optional: without them, sends still work — the
    log and its recipient-dedup are skipped (with a one-time warning)."""
    global _warned
    from pathlib import Path
    ok = bool(config.SHEETS_SPREADSHEET_ID) and Path(config.SHEETS_SERVICE_ACCOUNT_PATH).exists()
    if not ok and not _warned:
        print("  ⚠ Google Sheets not configured — skipping outreach log & sheet dedup "
              "(set SHEETS_SPREADSHEET_ID + service account JSON to enable)")
        _warned = True
    return ok


def _get_sheet() -> gspread.Worksheet:
    global _sheet
    if _sheet is not None:
        return _sheet

    creds = Credentials.from_service_account_file(
        config.SHEETS_SERVICE_ACCOUNT_PATH, scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(config.SHEETS_SPREADSHEET_ID)

    # Open or create the worksheet
    try:
        ws = spreadsheet.worksheet(config.SHEETS_WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(
            title=config.SHEETS_WORKSHEET_NAME, rows=1000, cols=len(HEADERS)
        )

    # Ensure header row exists
    first_row = ws.row_values(1)
    if not first_row or first_row[0] != HEADERS[0]:
        ws.insert_row(HEADERS, index=1)
        # Freeze header row
        spreadsheet.batch_update({
            "requests": [{
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": ws.id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            }]
        })

    _sheet = ws
    return _sheet


def already_contacted(company: str, job_title: str) -> bool:
    """
    Return True if we've already sent an email for this
    (company, job_title) combination — avoids duplicate sends.
    """
    if not sheets_available():
        return False
    ws = _get_sheet()
    all_rows = ws.get_all_values()

    if len(all_rows) <= 1:   # header only
        return False

    company_lower = company.lower().strip()
    title_lower = job_title.lower().strip()

    for row in all_rows[1:]:   # skip header
        if len(row) < 3:
            continue
        row_company = row[1].lower().strip()   # column B
        row_title = row[2].lower().strip()     # column C
        if row_company == company_lower and row_title == title_lower:
            return True

    return False


def already_emailed(recruiter_email: str) -> bool:
    """
    Return True if we've already emailed this exact recipient (column I).
    Recipient-based dedup — lets you contact multiple people for the same role
    without being blocked, while still preventing emailing the same person twice.
    """
    if not recruiter_email or not sheets_available():
        return False
    ws = _get_sheet()
    all_rows = ws.get_all_values()
    if len(all_rows) <= 1:
        return False
    target = recruiter_email.lower().strip()
    for row in all_rows[1:]:
        if len(row) >= 9 and row[8].lower().strip() == target:   # column I = Recruiter Email
            return True
    return False


def log_outreach(
    job: dict,
    recruiter: dict,
    outreach: dict,
    status: str = "Sent",
) -> None:
    """
    Append a row to the Google Sheet logging this outreach.

    job      — job dict (company, title, url, …)
    recruiter — from prospeo_lookup.py
    outreach  — from email_generator.py (subject, body)
    status    — default "Sent"
    """
    if not sheets_available():
        return
    ws = _get_sheet()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    h1b_label = "explicit mention" if job.get("h1b_signal") == 2 else "known sponsor"

    row = [
        now,                                    # A Date Sent
        job.get("company", ""),                 # B Company
        job.get("title", ""),                   # C Job Title
        job.get("job_url", ""),                 # D Job URL
        job.get("date_posted", ""),             # E Date Posted
        h1b_label,                              # F H1B Signal
        recruiter.get("name", ""),              # G Recruiter Name
        recruiter.get("title", ""),             # H Recruiter Title
        recruiter.get("email", ""),             # I Recruiter Email
        outreach.get("subject", ""),            # J Email Subject
        status,                                 # K Status
        "",                                     # L Notes (fill manually)
    ]

    ws.append_row(row, value_input_option="USER_ENTERED")
    print(f"  📋 Logged to Sheets: {job['company']} — {job['title']}")


def update_status(recruiter_email: str, new_status: str) -> bool:
    """
    Find a row by recruiter email and update its status column.
    Useful for marking replies manually or via webhook.
    """
    if not sheets_available():
        return False
    ws = _get_sheet()
    all_rows = ws.get_all_values()

    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) >= 9 and row[8].lower() == recruiter_email.lower():
            ws.update_cell(i, 11, new_status)   # column K = Status
            print(f"  📋 Updated status → '{new_status}' for {recruiter_email}")
            return True

    print(f"  ⚠ Row not found for {recruiter_email}")
    return False


def get_all_applications() -> list[dict]:
    """Return all logged applications as a list of dicts."""
    if not sheets_available():
        return []
    ws = _get_sheet()
    all_rows = ws.get_all_values()
    if len(all_rows) <= 1:
        return []

    header = all_rows[0]
    return [dict(zip(header, row)) for row in all_rows[1:]]


# ── Quick test ───────────────────────────────────────────────
if __name__ == "__main__":
    print("Testing Sheets connection...")
    ws = _get_sheet()
    print(f"Connected to worksheet: '{ws.title}'")
    apps = get_all_applications()
    print(f"Total logged applications: {len(apps)}")
