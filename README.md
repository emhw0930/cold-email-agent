# Cold Email Agent

Find the right recruiters at a company you've applied to, write a tailored cold email to
each (with your résumé attached), preview before sending, and log every send to a Google
Sheet.

Built **human-in-the-loop**: it drafts and shows you each email so nothing goes out without
your OK. Personalized outreach beats spray-and-pray.

```
Find recruiters → get their emails → draft a tailored email → preview → send → log
   (Prospeo)        (Prospeo /        (Claude)              (you)    (Gmail)  (Sheets)
                     known pattern)
```

---

## Quick start

```bash
# 1. Install
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure (see docs/SETUP.md for where each value comes from)
cp .env.example .env          # fill in your keys + profile
#   assets/gmail_credentials.json      (Gmail OAuth)
#   assets/sheets_service_account.json (Sheets)
#   assets/resume.pdf                  (your résumé)

# 3. Preview outreach for a role (no emails sent)
python src/outreach.py --company stripe.com --title "Software Engineer" --max 5

# 4. Send for real
python src/outreach.py --company stripe.com --title "Software Engineer" --max 5 --send
```

First send opens a browser once for Gmail consent; the token is cached afterward.

**Full setup walkthrough → [docs/SETUP.md](docs/SETUP.md)**

---

## The three ways to run it

### 1. Targeted outreach — `src/outreach.py` (recommended)
You applied to a role; reach a few recruiters there.
```bash
python src/outreach.py --company <domain> --title "<job title>" [--jd jd.txt] [--max N] [--send]
```
- Defaults to **preview** (safe). Add `--send` to actually send.
- Only sends to **Prospeo-verified** emails by default (avoids bounces);
  `--allow-unverified` to override.
- De-duplicates by recipient — won't email the same person twice.

### 2. LangGraph workflow — `src/graph_workflow.py`
The same pipeline as a graph, with a real human-in-the-loop `interrupt()` at the review step.
```bash
python src/graph_workflow.py --company stripe.com --title "Software Engineer" --max 5         # preview
python src/graph_workflow.py --company stripe.com --title "Software Engineer" --max 5 --send  # approve + send
```
`find_recruiters → generate_emails → human_review (interrupt) → send_and_log`

### 3. Batch discovery — `src/main.py` (optional)
Auto-discovers fresh H1B-sponsoring entry-level jobs (LinkedIn/Indeed scraping) and runs the
pipeline across them.
```bash
python src/main.py --dry-run --max 5     # preview
python src/main.py --max 5               # send
```

---

## Project structure

```
cold-email-agent/
├── README.md              ← you are here
├── .env.example           ← copy to .env; all secrets live in .env (gitignored)
├── requirements.txt
├── assets/                ← gitignored: OAuth JSON, service account, resume.pdf
├── data/
│   └── h1b_sponsors.json  ← employer → approx. annual H-1B approvals (sponsor signal)
├── docs/
│   ├── SETUP.md           ← one-time setup (API keys, OAuth, Sheets)
│   ├── AGENT.md           ← how to drive this with Claude / Cowork
│   └── PROMPTS.md         ← ready-to-paste prompts for an AI assistant
└── src/
    ├── config.py          ← loads config/secrets from .env
    ├── outreach.py        ← targeted, human-in-the-loop CLI  (start here)
    ├── graph_workflow.py  ← LangGraph version of the pipeline
    ├── main.py            ← batch job-discovery pipeline
    ├── job_discovery.py   ← LinkedIn/Indeed scraping + H1B filtering
    ├── prospeo_lookup.py  ← recruiter search + verified-email reveal
    ├── email_generator.py ← Claude-written tailored emails
    ├── gmail_sender.py    ← Gmail send + résumé attachment + OAuth
    └── sheets_logger.py   ← Google Sheets logging + dedup
```

---

## Configuration

Everything lives in `.env` (loaded by `src/config.py`); see `.env.example` for the full list.

| Variable | What it is |
|----------|-----------|
| `ANTHROPIC_API_KEY` | Claude API key (writes emails) |
| `PROSPEO_API_KEY` | Prospeo key (recruiter email lookup) |
| `SENDER_EMAIL` | Gmail address you send from |
| `SHEETS_SPREADSHEET_ID` | Target Google Sheet ID |
| `YOUR_NAME` / `YOUR_PHONE` / `YOUR_LINKEDIN` | Signature fields |
| `YOUR_EMAIL_PRIMARY` / `YOUR_EMAIL_ALT` | Emails shown in the signature |
| `EMAIL_MODEL` | Claude model for generation (default `claude-haiku-4-5`) |
| `VERIFIED_ONLY` | `true` (default) = only send to Prospeo-verified emails |
| `DRY_RUN` | `true` = never actually send |

---

## Security

- **Secrets are never committed** — `.env` and `assets/*.json` / `assets/resume.pdf` are gitignored.
- Gmail scope is limited to **send-only**.
- If a key is ever exposed, rotate it immediately (Anthropic / Prospeo / Google consoles).

---

## Notes & limits

- **Prospeo free tier** ≈ 75 credits/month. Prefer *verified* emails; guessed/pattern
  addresses can bounce. See [docs/AGENT.md](docs/AGENT.md) for the one-reveal-then-pattern strategy.
- **Gmail free tier** allows 500 sends/day.
- This sends real email to real people — keep it targeted and personalized.
