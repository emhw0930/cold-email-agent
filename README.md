# H1B Job Agent

**Live job board → https://emhw0930.github.io/cold-email-agent/** (all current roles,
refreshed daily by GitHub Actions)

Two tools for a new-grad job hunt that needs H-1B sponsorship, sharing the same plumbing
(Gemini/Claude for ranking, Claude for writing, Prospeo for contacts, Gmail for sending,
Google Sheets for logging):

1. **Daily job digest** — every morning, pull fresh entry-level SWE roles from companies
   that are *both* confirmed H-1B sponsors *and* hiring right now, publish ALL of them to
   the public site, rank the newest against your résumé, and email you the **top 10**
   (never repeating a role you've already been sent).
2. **Cold-email outreach** — for a company you applied to, find the right recruiters and
   hiring managers, draft a tailored email to each (résumé attached), **preview before
   sending**, and log every send to a Google Sheet.

Both are **human-in-the-loop**: nothing goes out without your explicit OK. Personalized,
targeted outreach beats spray-and-pray.

```
Daily digest:  H-1B sponsor DB → their ATS boards → fresh junior SWE roles → rank vs. résumé → email top 10
                (USCIS data)      (Greenhouse/Lever/                          (Gemini free tier,
                                   Ashby/Workday)                              or Claude)

Outreach:      find people → get their emails → draft tailored email → preview → send → log
                (web/LinkedIn)  (Prospeo /                (Claude)         (you)   (Gmail) (Sheets)
                                 known pattern)
```

Big employers whose boards can't be scraped (Google, Microsoft, Apple, Tesla, Bloomberg,
LinkedIn) plus Amazon are linked as "browse yourself" pills at the top of the site and in
each digest email.

---

## Quick start

```bash
# 1. Install
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure (see docs/SETUP.md for where each value comes from)
cp .env.example .env          # keys + profile; GMAIL_APP_PASSWORD is the easiest send path
#   assets/sheets_service_account.json (Sheets)
#   assets/resume.pdf                  (your résumé)
#   assets/gmail_credentials.json     (only if using the OAuth path instead of app password)

# 3a. Daily digest — preview a run without emailing
python src/daily_job_email.py --to you@example.com --dry-run

# 3b. Outreach — preview drafts for a role (no emails sent)
python src/outreach.py --company stripe.com --title "Software Engineer" --max 5

# 4. Send for real (add --send to outreach)
python src/outreach.py --company stripe.com --title "Software Engineer" --max 5 --send
```

With `GMAIL_APP_PASSWORD` set, sending just works (SMTP). On the OAuth path instead, the
first send opens a browser once for consent and caches a token.

**Full setup walkthrough → [docs/SETUP.md](docs/SETUP.md)**

---

## Part 1 — Daily job digest

Finds roles at companies that are *both* H-1B sponsors *and* actively hiring juniors,
publishes them all to the public site, and emails you the top 10 by résumé fit.

### Fully automated (GitHub Actions — the way it runs in production)

`.github/workflows/daily.yml` runs every day at 12:00 UTC (8 AM ET):

1. Pull every current junior SWE role from the cached sponsor boards
   (Greenhouse / Lever / Ashby / Workday)
2. Regenerate the **public site** ([docs/index.html](docs/index.html) →
   https://emhw0930.github.io/cold-email-agent/) with ALL of them
3. Drop anything already emailed in a past digest (dedup state lives in
   `data/h1b_employers.db`, committed back after each run)
4. Rank the freshest 150 against the résumé + drop "no sponsorship" JDs —
   **free via Gemini** (`gemini-2.5-flash-lite`, throttled to the free tier's
   rate limits) when `GEMINI_API_KEY` is set, else Claude Haiku (~$0.35/run)
5. Email the **top 10** (via Gmail SMTP app password) and mark them sent

Required repo secrets: `ANTHROPIC_API_KEY`, `PROSPEO_API_KEY`, `SENDER_EMAIL`,
`GMAIL_APP_PASSWORD` (myaccount.google.com → Security → App passwords),
`SHEETS_SPREADSHEET_ID`, `DIGEST_TO`, `RESUME_TEXT`, `YOUR_NAME`.
Optional: `GEMINI_API_KEY` (aistudio.google.com) — when set, fit-ranking runs on
Gemini's free tier (`gemini-2.5-flash-lite`) instead of the paid Claude API.
Trigger a run manually from the Actions tab (`workflow_dispatch`) to test.

### Run it yourself

```bash
python src/daily_workflow.py --to you@example.com --dry-run   # site + top-10 preview, no send
python src/daily_workflow.py --to you@example.com             # the full daily run
python src/jobs_site.py --open                                # just rebuild + open the site
python src/daily_job_email.py --to you@example.com --top 20   # classic digest (no site)
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
- **`fit_ranker.py`** drops roles whose JD explicitly says "no sponsorship" (free regex),
  then scores each role 0–100 against your résumé with a one-line reason — via Gemini's
  free tier when `GEMINI_API_KEY` is set, else Claude. A Gemini failure leaves roles
  unscored rather than silently spending Claude credits.
- **`jobs_site.py`** generates the public site ([docs/index.html](docs/index.html)) —
  a single self-contained page with client-side search, source filters, a new-grad
  toggle, and sort, in a dark glassmorphism design. GitHub Pages serves it from `docs/`.
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
├── .github/workflows/
│   └── daily.yml          ← daily 8am ET automation (site refresh + top-10 email)
├── assets/                ← gitignored: OAuth JSON, service account, resume.pdf
├── data/
│   ├── h1b_employers.db   ← SQLite: USCIS sponsors + board cache + emailed/seen
│   │                        dedup state (COMMITTED — the Action needs it)
│   └── h1b_sponsors.json  ← cached sponsor → confirmed ATS board mapping
├── docs/                  ← served by GitHub Pages
│   ├── index.html         ← the public job board (regenerated daily)
│   ├── SETUP.md           ← one-time setup (API keys, OAuth, Sheets)
│   ├── AGENT.md           ← how to drive this with Claude / Cowork
│   └── PROMPTS.md         ← ready-to-paste prompts for an AI assistant
└── src/
    ├── config.py          ← loads config/secrets from .env
    │   # ── Daily digest ──
    ├── daily_workflow.py  ← THE daily entry point: site + rank + email top 10
    ├── h1b_db.py          ← USCIS H-1B CSV → SQLite, ranked by new approvals
    ├── ats.py             ← Greenhouse/Lever/Ashby/Workday/Amazon board readers
    ├── h1b_greenhouse.py  ← board resolver, fresh-role puller, emailed-dedup
    ├── fit_ranker.py      ← sponsorship gate + Gemini(free)/Claude fit ranking
    ├── jobs_site.py       ← generates the public site (docs/index.html)
    ├── daily_job_email.py ← builds + emails the HTML digest
    ├── outreach_server.py ← local review server behind the digest's outreach button
    │   # ── Cold outreach ──
    ├── outreach.py        ← targeted, human-in-the-loop CLI  (start here)
    ├── graph_workflow.py  ← LangGraph version of the pipeline
    ├── main.py            ← batch job-discovery pipeline
    ├── job_discovery.py   ← LinkedIn/Indeed scraping + H1B filtering
    ├── prospeo_lookup.py  ← recruiter search + verified-email reveal
    ├── email_generator.py ← Claude-written tailored emails
    ├── gmail_sender.py    ← Gmail send: SMTP app-password or OAuth + attachments
    └── sheets_logger.py   ← Google Sheets logging + dedup
```

---

## Configuration

Everything lives in `.env` (loaded by `src/config.py`); see `.env.example` for the full list.

| Variable | What it is |
|----------|-----------|
| `ANTHROPIC_API_KEY` | Claude API key (writes outreach emails; ranking fallback) |
| `GEMINI_API_KEY` | *Optional* — Google AI Studio key; makes daily fit-ranking **free** (Gemini free tier) |
| `GMAIL_APP_PASSWORD` | *Optional* — Gmail App Password; sends via SMTP (headless, used by the Action). Unset = browser OAuth |
| `PROSPEO_API_KEY` | Prospeo key (recruiter email lookup) |
| `SENDER_EMAIL` | Gmail address you send from |
| `SHEETS_SPREADSHEET_ID` | Target Google Sheet ID |
| `YOUR_NAME` / `YOUR_PHONE` / `YOUR_LINKEDIN` | Signature fields |
| `YOUR_EMAIL_PRIMARY` / `YOUR_EMAIL_ALT` | Emails shown in the signature |
| `EMAIL_MODEL` | Claude model for outreach generation (default `claude-haiku-4-5`) |
| `GEMINI_MODEL` | Gemini model for ranking (default `gemini-2.5-flash-lite`) |
| `VERIFIED_ONLY` | `true` (default) = only send to Prospeo-verified emails |
| `DRY_RUN` | `true` = never actually send |

---

## Automation

**Already automated** — `.github/workflows/daily.yml` runs the full daily loop in GitHub
Actions at 8 AM ET (see Part 1). No machine needs to be on. Test a run anytime from the
repo's **Actions tab → Daily job digest + site refresh → Run workflow**.

Prefer running from your own machine instead? cron/launchd works too:

```bash
crontab -e
# every day at 8am:
0 8 * * * cd /path/to/h1b-job-agent && /path/to/venv/bin/python src/daily_workflow.py --to you@example.com >> logs/daily.log 2>&1
```

(If both run, dedup keeps them consistent — but pull before local runs so the
`data/h1b_employers.db` state doesn't diverge from what the Action commits back.)

---

## Security

- **Secrets are never committed** — `.env`, `assets/*.json`, and `assets/resume.pdf` are
  gitignored; in CI they come from GitHub Secrets (résumé text included, so it stays out
  of this public repo).
- `data/h1b_employers.db` **is** committed on purpose — it holds only public USCIS-derived
  data plus which job IDs were already emailed. No personal data.
- Gmail: the OAuth path is scoped **send-only**; the app-password path is a separate
  16-char credential you can revoke anytime at myaccount.google.com without touching
  your real password.
- If a key is ever exposed, rotate it immediately (Anthropic / Google / Prospeo consoles).

---

## Notes & limits

- **Prospeo free tier** ≈ 75 credits/month. Prefer *verified* emails; guessed/pattern
  addresses can bounce. See [docs/AGENT.md](docs/AGENT.md) for the one-reveal-then-pattern strategy.
- **Gmail free tier** allows 500 sends/day.
- ATS board mapping is slug-guess + probe; Greenhouse is name-verified, Lever/Ashby are
  verified by slug strength since they expose no company name.
- This sends real email to real people — keep it targeted and personalized.
```
