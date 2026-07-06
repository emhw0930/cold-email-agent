# H1B Job Agent

Two tools for a new-grad job hunt that needs H-1B sponsorship, sharing the same plumbing
(Prospeo for contacts, Claude for writing, Gmail for sending, Google Sheets for logging):

1. **Daily job digest** — every morning, pull fresh entry-level SWE roles from companies
   that are *both* confirmed H-1B sponsors *and* hiring right now, rank them against your
   résumé, and email you a clean HTML digest.
2. **Cold-email outreach** — for a company you applied to, find the right recruiters and
   hiring managers, draft a tailored email to each (résumé attached), **preview before
   sending**, and log every send to a Google Sheet.

Both are **human-in-the-loop**: nothing goes out without your explicit OK. Personalized,
targeted outreach beats spray-and-pray.

```
Daily digest:  H-1B sponsor DB → their ATS boards → fresh junior SWE roles → rank vs. résumé → email you
                (USCIS data)      (Greenhouse/Lever/Ashby)                     (Claude)

Outreach:      find people → get their emails → draft tailored email → preview → send → log
                (web/LinkedIn)  (Prospeo /                (Claude)         (you)   (Gmail) (Sheets)
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

# 3a. Daily digest — preview a run without emailing
python src/daily_job_email.py --to you@example.com --dry-run

# 3b. Outreach — preview drafts for a role (no emails sent)
python src/outreach.py --company stripe.com --title "Software Engineer" --max 5

# 4. Send for real (add --send to outreach)
python src/outreach.py --company stripe.com --title "Software Engineer" --max 5 --send
```

First send opens a browser once for Gmail consent; the token is cached afterward.

**Full setup walkthrough → [docs/SETUP.md](docs/SETUP.md)**

---

## Part 1 — Daily job digest

Finds roles at companies that are *both* H-1B sponsors *and* actively hiring juniors, then
emails you a ranked digest. Intended to run daily from launchd/cron.

```bash
python src/daily_job_email.py --to you@example.com              # all roles, emailed
python src/daily_job_email.py --to you@example.com --top 20     # cap at 20
python src/daily_job_email.py --to you@example.com --new-only   # only roles not seen before
python src/daily_job_email.py --to you@example.com --rank       # Claude-rank by résumé fit
python src/daily_job_email.py --to you@example.com --dry-run    # print instead of emailing
```

How it's built:

- **`h1b_db.py`** loads the USCIS H-1B Employer Data Hub CSV into SQLite
  (`data/h1b_employers.db`) and ranks sponsors by *New Employment Approvals* — fresh
  (often cap-subject) hires, not renewals.
  ```bash
  python src/h1b_db.py --csv "Employer Information.csv" --top 25   # (re)build the DB
  ```
- **`ats.py` + `h1b_greenhouse.py`** map each sponsor to its public **Greenhouse / Lever /
  Ashby** board (slug-guess + probe, since no ATS offers global search), cache the working
  boards, and pull fresh explicit-junior SWE roles.
  ```bash
  python src/h1b_greenhouse.py --resolve --n 500   # discover + cache boards
  python src/h1b_greenhouse.py --list              # show cached boards
  python src/h1b_greenhouse.py --jobs              # fresh junior roles across all boards
  ```
  Cached board list lives in `data/h1b_sponsors.json`.
- **`fit_ranker.py`** drops roles whose JD explicitly says "no sponsorship," then (with
  `--rank`) has Claude score each 0–100 against your résumé with a one-line reason.
- **`outreach_server.py`** is a local web server (http://127.0.0.1:8770) behind the
  "Email recruiters" button in the digest — it opens a review page with an AI-drafted
  email and a recruiter-email builder, and only sends when you click Send.
  ```bash
  python src/outreach_server.py
  ```

---

## Part 2 — Cold-email outreach

You applied to a role; reach a few recruiters and hiring managers there.

### 1. Targeted outreach — `src/outreach.py` (recommended)
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
h1b-job-agent/
├── README.md              ← you are here
├── .env.example           ← copy to .env; all secrets live in .env (gitignored)
├── requirements.txt
├── assets/                ← gitignored: OAuth JSON, service account, resume.pdf
├── data/
│   ├── h1b_employers.db   ← SQLite of USCIS sponsors (gitignored; rebuild from CSV)
│   └── h1b_sponsors.json  ← cached sponsor → confirmed ATS board mapping
├── docs/
│   ├── SETUP.md           ← one-time setup (API keys, OAuth, Sheets)
│   ├── AGENT.md           ← how to drive this with Claude / Cowork
│   └── PROMPTS.md         ← ready-to-paste prompts for an AI assistant
└── src/
    ├── config.py          ← loads config/secrets from .env
    │   # ── Daily digest ──
    ├── h1b_db.py          ← USCIS H-1B CSV → SQLite, ranked by new approvals
    ├── ats.py             ← unified reader for Greenhouse/Lever/Ashby APIs
    ├── h1b_greenhouse.py  ← sponsor → ATS board resolver + fresh-role puller
    ├── fit_ranker.py      ← sponsorship gate + Claude résumé-fit ranking
    ├── daily_job_email.py ← builds + emails the daily HTML digest
    ├── outreach_server.py ← local review server behind the digest's outreach button
    │   # ── Cold outreach ──
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
| `ANTHROPIC_API_KEY` | Claude API key (writes emails, ranks fit) |
| `PROSPEO_API_KEY` | Prospeo key (recruiter email lookup) |
| `SENDER_EMAIL` | Gmail address you send from |
| `SHEETS_SPREADSHEET_ID` | Target Google Sheet ID |
| `YOUR_NAME` / `YOUR_PHONE` / `YOUR_LINKEDIN` | Signature fields |
| `YOUR_EMAIL_PRIMARY` / `YOUR_EMAIL_ALT` | Emails shown in the signature |
| `EMAIL_MODEL` | Claude model for generation (default `claude-haiku-4-5`) |
| `VERIFIED_ONLY` | `true` (default) = only send to Prospeo-verified emails |
| `DRY_RUN` | `true` = never actually send |

---

## Automate the daily digest

Run `daily_job_email.py` each morning via launchd (macOS) or cron:

```bash
crontab -e
# every day at 8am:
0 8 * * * cd /path/to/h1b-job-agent && /path/to/venv/bin/python src/daily_job_email.py --to you@example.com --new-only --rank >> logs/daily.log 2>&1
```

---

## Security

- **Secrets are never committed** — `.env`, `assets/*.json`, `assets/resume.pdf`, and
  `data/*.db` are gitignored.
- Gmail scope is limited to **send-only**.
- If a key is ever exposed, rotate it immediately (Anthropic / Prospeo / Google consoles).

---

## Notes & limits

- **Prospeo free tier** ≈ 75 credits/month. Prefer *verified* emails; guessed/pattern
  addresses can bounce. See [docs/AGENT.md](docs/AGENT.md) for the one-reveal-then-pattern strategy.
- **Gmail free tier** allows 500 sends/day.
- ATS board mapping is slug-guess + probe; Greenhouse is name-verified, Lever/Ashby are
  verified by slug strength since they expose no company name.
- This sends real email to real people — keep it targeted and personalized.
```
