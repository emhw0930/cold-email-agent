# H1B Job Outreach Agent

A tool that finds recruiters/hiring managers at companies you've applied to and sends
tailored cold emails with your resume attached — logging every send to Google Sheets.

Built for a **human-in-the-loop** workflow: it drafts and previews emails so you review
before anything is sent. High-quality, personalized outreach beats spray-and-pray.

---

## What it does

```
Find recruiters → Reveal verified emails → Generate tailored email → Preview → Send → Log
   (Prospeo)          (Prospeo)               (Claude)               (you)    (Gmail)  (Sheets)
```

- **Prospeo** — finds US-based recruiters/managers and reveals *verified* emails (avoids bounces)
- **Claude** — writes a concise, tailored email per recipient from the job description + your resume
- **Gmail API** — sends with your resume attached
- **Google Sheets** — logs each outreach and de-duplicates by recipient

There's also a `main.py` batch pipeline that auto-discovers fresh H1B-sponsoring jobs via
LinkedIn/Indeed scraping — but the **`outreach.py`** targeted flow is the recommended path.

---

## Quick start

```bash
# 1. Install
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure secrets (NEVER commit .env)
cp .env.example .env          # then edit .env with your keys/profile

# 3. Add credentials & resume to assets/  (see Setup below)
#    assets/gmail_credentials.json
#    assets/sheets_service_account.json
#    assets/resume.pdf

# 4. Preview outreach for a company (no emails sent)
python src/outreach.py --company twilio.com --title "Software Engineer (L1)" --jd jd.txt --max 5

# 5. Send for real
python src/outreach.py --company twilio.com --title "Software Engineer (L1)" --jd jd.txt --max 5 --send
```

First send opens a browser once for Gmail OAuth; the token is cached afterward.

---

## Configuration

All secrets and your profile live in `.env` (loaded by `config.py`). See `.env.example`
for the full list. Key fields:

| Variable | What it is |
|----------|-----------|
| `ANTHROPIC_API_KEY` | Claude API key (email generation) |
| `PROSPEO_API_KEY` | Prospeo key (recruiter email lookup) |
| `SENDER_EMAIL` | Gmail address you send from |
| `SHEETS_SPREADSHEET_ID` | Target Google Sheet ID |
| `YOUR_NAME` / `YOUR_PHONE` / `YOUR_LINKEDIN` | Signature fields |
| `YOUR_EMAIL_PRIMARY` / `YOUR_EMAIL_ALT` | Emails shown in the signature |
| `VERIFIED_ONLY` | `true` (default) = only send to Prospeo-verified emails |
| `DRY_RUN` | `true` = never actually send |

---

## Setup (one-time)

Detailed step-by-step (Google Cloud, Gmail OAuth, Sheets service account, Prospeo) is in
[docs/SETUP.md](docs/SETUP.md). Summary:

1. **Anthropic** — create an API key, put it in `.env`
2. **Prospeo** — create an API key (free tier ~75 credits/mo), put it in `.env`
3. **Gmail** — create a Desktop OAuth client, download JSON → `assets/gmail_credentials.json`,
   add your address as a test user on the OAuth consent screen
4. **Google Sheets** — create a service account, download JSON → `assets/sheets_service_account.json`,
   create a sheet, share it with the service-account email (Editor), put the sheet ID in `.env`
5. **Resume** — drop `assets/resume.pdf`

---

## Usage

### Targeted outreach (recommended)
```bash
python src/outreach.py --company <domain> --title "<job title>" --jd <jd.txt> [--max N] [--send]
```
- Defaults to **preview** (safe). Add `--send` to actually send.
- Only sends to **Prospeo-verified** emails by default (no bounces). Use `--allow-unverified` to override.
- De-duplicates by recipient — won't email the same person twice, but lets you reach
  multiple people for one role.

### Batch job discovery (optional)
```bash
python src/main.py --dry-run --max 5     # preview
python src/main.py --max 5               # send
```

---

## Security

- **Secrets are never committed** — `.env` and `assets/*.json` are gitignored.
- If a key is ever exposed, rotate it immediately (Anthropic / Prospeo / Google consoles).
- Gmail scope is limited to **send-only**.

---

## Project structure

| File | Purpose |
|------|---------|
| `src/config.py` | Loads config/secrets from `.env` |
| `src/outreach.py` | Targeted, human-in-the-loop outreach CLI |
| `src/main.py` | Batch job-discovery pipeline |
| `src/prospeo_lookup.py` | Recruiter search + verified-email reveal |
| `src/email_generator.py` | Claude-generated tailored emails |
| `src/gmail_sender.py` | Gmail send + resume attachment + OAuth |
| `src/sheets_logger.py` | Google Sheets logging + dedup |
| `src/job_discovery.py` | LinkedIn/Indeed scraping (batch mode) |

---

## Notes & limits

- **Prospeo free tier** ≈ 75 verified emails/month. Email formats vary, so always rely on
  *verified* status rather than guessing patterns (guessed addresses bounce).
- **Gmail free tier** allows 500 sends/day.
- This tool sends real emails to real people — keep it targeted and personalized.
