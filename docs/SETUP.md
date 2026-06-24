# H1B Job Application Agent — Setup Guide

Complete these steps once before running `main.py`.

---

## 0. Prerequisites

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

---

## 1. Anthropic API Key (Claude)

1. Go to https://console.anthropic.com → API Keys → Create Key
2. Copy the key (starts with `sk-ant-...`)
3. In `config.py`, set:
   ```python
   ANTHROPIC_API_KEY = "sk-ant-YOUR_KEY_HERE"
   ```

---

## 2. Gmail API — OAuth Credentials

1. Go to https://console.cloud.google.com
2. Create a new project (or use existing)
3. Enable **Gmail API**: APIs & Services → Enable APIs → search "Gmail API" → Enable
4. Create credentials: APIs & Services → Credentials → Create Credentials → **OAuth client ID**
   - Application type: **Desktop app**
   - Name it anything
5. Download the JSON → save as `assets/gmail_credentials.json`
6. In `config.py`, set:
   ```python
   SENDER_EMAIL = "your.gmail@gmail.com"
   ```
7. **First run only**: the script will open a browser window asking you to authorize.
   After authorizing, a `assets/gmail_token.json` is created automatically — subsequent
   runs use this token without prompting.

> ⚠ Gmail free tier allows 500 sends/day. This agent sends at most 10/run, so you're fine.

---

## 3. Apollo.io API Key

1. Go to https://app.apollo.io → Settings → API Keys → Create API Key
2. Copy the key
3. In `config.py`, set:
   ```python
   APOLLO_API_KEY = "YOUR_APOLLO_KEY"
   ```

> **Free tier**: ~50 export credits/month. Each recruiter email reveal = 1 credit.  
> For more volume, upgrade to Basic ($49/mo = 200 credits) or Professional.

---

## 4. Google Sheets — Service Account

1. In Google Cloud Console (same project as Gmail):
   APIs & Services → Enable APIs → search "Google Sheets API" → Enable
2. Create a service account: IAM & Admin → Service Accounts → Create
   - Name: `h1b-agent`
   - Role: skip (click Continue)
3. Create a key: click the service account → Keys → Add Key → JSON → Download
4. Save as `assets/sheets_service_account.json`
5. **Create your Google Sheet**:
   - Go to https://sheets.google.com → Blank spreadsheet
   - Name it "H1B Job Applications"
   - Copy the spreadsheet ID from the URL:
     `https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`
6. **Share the sheet with the service account**:
   - Open the sheet → Share → paste the service account email
     (looks like `h1b-agent@your-project.iam.gserviceaccount.com`)
   - Give it **Editor** access
7. In `config.py`, set:
   ```python
   SHEETS_SPREADSHEET_ID = "YOUR_SPREADSHEET_ID"
   ```

---

## 5. Add Your Resume & Cover Letter

Place your files in the `assets/` folder:

```
assets/
├── resume.pdf             ← rename yours to this
├── cover_letter.pdf       ← rename yours to this
├── gmail_credentials.json
└── sheets_service_account.json
```

> The cover letter can be a general one — Claude personalizes the cold email body.
> The cover letter PDF is just an additional attachment.

---

## 6. Fill in Your Profile in config.py

```python
YOUR_NAME     = "Jane Doe"
YOUR_PHONE    = "+1 (555) 123-4567"
YOUR_LINKEDIN = "https://linkedin.com/in/janedoe"
YOUR_GITHUB   = "https://github.com/janedoe"
YOUR_BIO      = (
    "a new grad CS student from NYU with internship experience in "
    "Python and React, seeking entry-level SWE roles"
)
```

---

## 7. Test Everything (Dry Run First!)

```bash
# Preview emails without sending anything
python main.py --dry-run

# Check job discovery only
python job_discovery.py

# Check Apollo lookup
python apollo_lookup.py

# Check Sheets connection
python sheets_logger.py
```

Once dry run looks good:

```bash
# Live run — searches past 24h, sends up to 10 emails
python main.py

# Cast a wider net (past 48h, up to 20 jobs)
python main.py --hours 48 --max 20
```

---

## 8. Automate Daily (Optional)

### macOS — cron
```bash
crontab -e
# Add this line to run every day at 8am:
0 8 * * * cd /path/to/h1b-job-agent && /path/to/venv/bin/python main.py >> logs/run.log 2>&1
```

### GitHub Actions (runs in the cloud, free)
Create `.github/workflows/daily.yml`:
```yaml
name: Daily H1B Outreach
on:
  schedule:
    - cron: '0 13 * * *'   # 8am EST = 1pm UTC
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: python main.py
        env:
          # Store all secrets in GitHub repo Settings → Secrets
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          APOLLO_API_KEY: ${{ secrets.APOLLO_API_KEY }}
          # (store credentials JSON as base64 secrets and decode in workflow)
```

---

## Google Sheets — Column Reference

| Column | Field | Notes |
|--------|-------|-------|
| A | Date Sent | Auto-filled |
| B | Company | Auto-filled |
| C | Job Title | Auto-filled |
| D | Job URL | Auto-filled |
| E | Date Posted | Auto-filled |
| F | H1B Signal | "explicit mention" or "known sponsor" |
| G | Recruiter Name | Auto-filled |
| H | Recruiter Title | Auto-filled |
| I | Recruiter Email | Auto-filled |
| J | Email Subject | Auto-filled |
| K | Status | Update manually: Replied / No Response / Interview |
| L | Notes | Free text |
