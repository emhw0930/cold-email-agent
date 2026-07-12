# Setup Guide

Do these steps once. All secrets go in a **`.env`** file (gitignored) — you never edit code.

---

## 0. Install

```bash
python3 -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in the values below
```

Open `.env` in any editor and fill it in as you complete each step.

---

## 1. Gemini API key (free — writes outreach emails + ranks daily fit)

Gemini is the project's only LLM, and it's on Google's **free tier**.

1. Go to https://aistudio.google.com → **Get API key** → **Create API key**
2. Copy the key
3. In `.env`:
   ```
   GEMINI_API_KEY=...
   ```

> Without a key the tool still runs — ranking falls back to a free keyword
> scorer and cold emails use a plain template — but the key is worth it and free.

---

## 2. Prospeo API key (finds recruiter emails)

1. Go to https://prospeo.io → sign up → **API** → copy your key
2. In `.env`:
   ```
   PROSPEO_API_KEY=...
   ```

> **Free tier ≈ 75 credits/month.** Search (finding names) is cheap; revealing a
> *verified* email costs 1 credit. Strategy: reveal **one** email to learn a company's
> format, then construct the rest from that pattern. See [AGENT.md](AGENT.md).

---

## 3. Gmail sending — pick ONE of two paths

Either path sends from your Gmail. In `.env`, always set:
```
SENDER_EMAIL=your.email@gmail.com
```

### Path A: App Password (simplest — and required for the GitHub Actions automation)

1. Go to https://myaccount.google.com → **Security** → turn on **2-Step Verification**
2. Search the settings page for **"App passwords"** → create one (name it anything)
3. Put the 16-character password in `.env`:
   ```
   GMAIL_APP_PASSWORD=abcdefghijklmnop
   ```

Mail goes out via SMTP — no browser, no Google Cloud project, works headless (this is
what the daily GitHub Action uses). Revoke it anytime from the same settings page.

### Path B: Gmail API OAuth (local use only)

1. Go to https://console.cloud.google.com → create/select a project
2. **APIs & Services → Enable APIs** → enable **Gmail API**
3. **Credentials → Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
4. Download the JSON → save it as **`assets/gmail_credentials.json`**
5. **OAuth consent screen** → add your own Gmail address as a **Test user**
   (otherwise Google blocks the login)

On the **first send**, a browser opens for one-time consent; a token is then cached at
`assets/gmail_token.json` and reused.

> The OAuth scope is **send-only** (`gmail.send`). Gmail's free tier allows 500 sends/day.
> In "Testing" mode the token expires every 7 days — delete `assets/gmail_token.json` and
> re-run to refresh, or publish the app to Production for a permanent token.
> If `GMAIL_APP_PASSWORD` is set, it takes precedence over OAuth.

---

## 4. Google Sheets (logs every send)

1. Same Google Cloud project → enable the **Google Sheets API**
2. **IAM & Admin → Service Accounts → Create** (name it anything)
3. Open the service account → **Keys → Add Key → JSON** → download
4. Save it as **`assets/sheets_service_account.json`**
5. Create a blank Google Sheet at https://sheets.google.com and copy its ID from the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_IS_THE_ID`**`/edit`
6. **Share** the sheet with the service-account email
   (`...@your-project.iam.gserviceaccount.com`) as **Editor**
7. In `.env`:
   ```
   SHEETS_SPREADSHEET_ID=THIS_IS_THE_ID
   ```

The header row and worksheet are created automatically on first log.

> **On the daily fit-ranking:** it scores roles with Gemini (step 1), throttled to the
> free tier's rate limits automatically (~150 roles ≈ 12 min). When the daily quota is
> spent, remaining roles fall back to a free keyword scorer — so ranking always completes
> at $0.

---

## 5. Resume + your profile

1. Drop your résumé at **`assets/resume.pdf`** (attached to every email).
2. Fill in your profile fields in `.env`:
   ```
   YOUR_NAME=Jane Doe
   YOUR_PHONE=+1 (555) 123-4567
   YOUR_LINKEDIN=https://linkedin.com/in/janedoe
   YOUR_EMAIL_PRIMARY=jane@gmail.com
   YOUR_EMAIL_ALT=jane@school.edu        # optional second email in the signature
   YOUR_BIO=a new-grad software engineer with ...
   ```

---

## 6. Verify (no emails sent)

```bash
source venv/bin/activate

python src/sheets_logger.py      # should print: Connected to worksheet
python src/prospeo_lookup.py     # should print recruiter search results
python src/email_generator.py    # prints a sample generated email
python src/outreach.py --company stripe.com --title "Software Engineer" --max 3
                                 # preview mode (default) — finds recruiters, drafts, no send
```

Add `--send` to `outreach.py` only when a preview looks right.

---

## 7. Daily automation (GitHub Actions — already wired)

`.github/workflows/daily.yml` runs the full daily loop at 12:00 UTC (8 AM ET): refresh
the public site with every current role, rank the freshest unsent roles by résumé fit,
and email the top 10. To set it up on a fork, add these **repository Secrets**
(Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | from step 1 (free — ranking + email writing) |
| `PROSPEO_API_KEY` | from step 2 |
| `GMAIL_APP_PASSWORD` | from step 3 Path A (required — the runner has no browser for OAuth) |
| `SENDER_EMAIL` | your Gmail address |
| `SHEETS_SPREADSHEET_ID` | from step 4 |
| `DIGEST_TO` | the address that receives the daily top-10 email |
| `RESUME_TEXT` | plain-text of your résumé (kept out of the public repo) |
| `YOUR_NAME` | your name |

Enable GitHub Pages (**Settings → Pages** → branch `main`, folder `/docs`) to publish the
job board. Test with **Actions tab → Daily job digest + site refresh → Run workflow**.

Prefer local cron instead? `0 8 * * * cd /path/to/h1b-job-agent && venv/bin/python
src/daily_workflow.py --to you@example.com >> logs/daily.log 2>&1`

---

## Google Sheet columns

| Col | Field | Filled |
|-----|-------|--------|
| A | Date Sent | auto |
| B | Company | auto |
| C | Job Title | auto |
| D | Job URL | auto |
| E | Date Posted | auto |
| F | H1B Signal | auto (`explicit mention` / `known sponsor`) |
| G | Recruiter Name | auto |
| H | Recruiter Title | auto |
| I | Recruiter Email | auto |
| J | Email Subject | auto |
| K | Status | **you** — Replied / No Response / Interview / Bounced |
| L | Notes | **you** — free text |
