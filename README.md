# H-1B Job Agent

**A personal job agent for anyone who needs H-1B sponsorship — in *any* field.**
It builds you a daily job board of roles at confirmed H-1B sponsors, emails you the ones
that best match your résumé, and helps you cold-email recruiters. Drive the whole thing by
**talking to [Claude Code](https://claude.com/claude-code)** — you don't need to know the
terminal.

> **Example live board (software-engineer setup):** https://emhw0930.github.io/cold-email-agent/

Two tools, one shared pipeline:

1. **Daily job board + digest** — every morning it pulls fresh roles from companies that
   are *both* confirmed H-1B sponsors *and* hiring right now, publishes them all to your
   own public website, ranks them against your résumé, and emails you the **top 10** (never
   repeating a role you've already been sent).
2. **Cold-email outreach** — for a company you applied to, it finds recruiters/hiring
   managers, drafts a tailored email each (résumé attached), lets you **preview before
   sending**, and logs every send.

```
Daily board:  H-1B sponsor DB → their job boards → fresh in-field roles → rank vs. résumé → email top 10
               (USCIS data)      (Greenhouse/Lever/                        (Gemini free tier,
                                  Ashby/Workday)                            keyword fallback)
```

Everything is **human-in-the-loop** (no cold email sends without your OK) and **free to
run by default** (Gemini's free tier for ranking, a local keyword fallback, free GitHub
Actions + Pages, Gmail for sending).

---

## 🔒 The guarantee: it only ever searches H-1B sponsors

The company list is *derived from the USCIS H-1B Employer Data Hub* — a role can't appear
unless that employer actually sponsored H-1B — and a second filter drops any posting whose
description says "no sponsorship." **This holds no matter your field or résumé.** Switching
to a different job type never weakens it.

---

## Use it with Claude Code (recommended — no terminal needed)

1. **Fork this repo** (or click *Use this template*), then open your copy in **Claude Code**
   (or claude.ai/code). It auto-reads [`CLAUDE.md`](CLAUDE.md) and knows how to run everything.
2. **Just tell it what you need.** For example:

   > *"Set this up for me. I'm a new-grad **mechanical engineer** who needs H-1B
   > sponsorship. Here's my résumé (attached). Send my daily digest to jane@example.com."*

   Claude Code will: save your résumé, fill in your profile and keys, **switch the role
   filters to your field** (it edits the code for you — mechanical, data, finance, nursing,
   anything), set up the free daily automation, and show you a preview before anything sends.
3. **Then just talk to it day to day:**
   - *"Show me today's best-fit roles."*
   - *"I applied to Stripe for a Mechanical Engineer role — find 5 recruiters and draft cold
     emails, but let me review before sending."*
   - *"Change my digest to send at noon"* / *"add more sponsor companies."*

New users get the ~490 pre-resolved sponsor boards for free (they ship committed in the
repo), so there's nothing to build before your first run.

**Setup keys & secrets walkthrough → [docs/SETUP.md](docs/SETUP.md)**

---

## Prefer the terminal? (advanced / manual)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                    # keys + profile (see docs/SETUP.md)
#   assets/resume.pdf                                   # your résumé

python -m src.digest.daily_workflow --to you@example.com --dry-run   # preview site + top-10, no send
python -m src.digest.daily_workflow --to you@example.com             # the full daily run
python -m src.outreach.outreach --company stripe.com --title "Software Engineer" --max 5   # outreach preview
```

To target a non-software field manually, edit the role-title keywords in `src/jobs/ats.py`
(`_POSITIVE` / `_JUNIOR` / `_SENIOR` / `_NONSOFTWARE`) and the "Fresh SWE roles" display
strings in `src/digest/jobs_site.py` and `src/digest/daily_job_email.py` — or just ask Claude Code to.

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
   rate limits); when the daily quota is spent it falls back to the free
   keyword scorer so every role is still scored
5. Email the **top 10** (via Gmail SMTP app password) and mark them sent

Required repo secrets: `GEMINI_API_KEY` (aistudio.google.com — free),
`PROSPEO_API_KEY`, `SENDER_EMAIL`, `GMAIL_APP_PASSWORD` (myaccount.google.com →
Security → App passwords), `SHEETS_SPREADSHEET_ID`, `DIGEST_TO`, `RESUME_TEXT`,
`YOUR_NAME`. Trigger a run manually from the Actions tab (`workflow_dispatch`) to test.

### Run it yourself

```bash
python -m src.digest.daily_workflow --to you@example.com --dry-run   # site + top-10 preview, no send
python -m src.digest.daily_workflow --to you@example.com             # the full daily run
python -m src.digest.jobs_site --open                                # just rebuild + open the site
python -m src.digest.daily_job_email --to you@example.com --top 20   # classic digest (no site)
```

How it's built:

- **`h1b_db.py`** loads the USCIS H-1B Employer Data Hub CSV into SQLite
  (`data/h1b_employers.db`) and ranks sponsors by *New Employment Approvals* — fresh
  (often cap-subject) hires, not renewals.
  ```bash
  python -m src.core.h1b_db --csv "Employer Information.csv" --top 25   # (re)build the DB
  ```
- **`ats.py` + `h1b_greenhouse.py`** map each sponsor to its public **Greenhouse / Lever /
  Ashby** board (slug-guess + probe, since no ATS offers global search), cache the working
  boards, and pull fresh explicit-junior SWE roles.
  ```bash
  python -m src.jobs.h1b_greenhouse --resolve --n 500   # discover + cache boards
  python -m src.jobs.h1b_greenhouse --list              # show cached boards
  python -m src.jobs.h1b_greenhouse --jobs              # fresh junior roles across all boards
  ```
  Cached board list lives in `data/h1b_sponsors.json`.
- **`fit_ranker.py`** drops roles whose JD explicitly says "no sponsorship" (free regex),
  then scores each role 0–100 against your résumé with a one-line reason — via Gemini's
  free tier (`gemini.py`). When the quota is spent it falls back to a free deterministic
  keyword scorer, so ranking always completes at $0.
- **`jobs_site.py`** generates the public site ([docs/index.html](docs/index.html)) —
  a single self-contained page with client-side search, source filters, a new-grad
  toggle, and sort, in a dark glassmorphism design. GitHub Pages serves it from `docs/`.
- **`outreach_server.py`** is a local web server (http://127.0.0.1:8770) behind the
  "Email recruiters" button in the digest — it opens a review page with an AI-drafted
  email and a recruiter-email builder, and only sends when you click Send.
  ```bash
  python -m src.outreach.outreach_server
  ```

---

## Part 2 — Cold-email outreach

You applied to a role; reach a few recruiters and hiring managers there.

### 1. Targeted outreach — `src/outreach/outreach.py` (recommended)
```bash
python -m src.outreach.outreach --company <domain> --title "<job title>" [--jd jd.txt] [--max N] [--send]
```
- Defaults to **preview** (safe). Add `--send` to actually send.
- Only sends to **Prospeo-verified** emails by default (avoids bounces);
  `--allow-unverified` to override.
- De-duplicates by recipient — won't email the same person twice.

### 2. Review server — `src/outreach/outreach_server.py`
A local web page (http://127.0.0.1:8770) behind the digest's "Email recruiters" button:
paste recruiter names, pick the company's email pattern, preview AI-drafted emails, and
send + log — nothing goes out until you click Send.
```bash
python -m src.outreach.outreach_server
```

---

## Project structure

`src/` is a Python package split into five subpackages by responsibility. Data flows
left-to-right through them: **jobs** sources roles → **ranking** scores them →
**digest** publishes + emails → (**outreach** handles the separate cold-email half),
all on top of **core** shared infrastructure.

```
h1b-job-agent/
├── README.md              ← you are here
├── .env.example           ← copy to .env; all secrets live in .env (gitignored)
├── requirements.txt
├── .github/workflows/
│   └── daily.yml          ← daily 8am ET automation (site refresh + top-10 email)
├── assets/                ← gitignored personal files (résumé, OAuth JSON, keys)
│   ├── resume.pdf         ← your résumé (fit ranking reads it)
│   ├── experience.json    ← curated résumé bullets → the RAG knowledge base
│   └── experience.example.json  ← committed template showing the schema
├── data/
│   ├── h1b_employers.db   ← SQLite: USCIS sponsors + wages + board cache + emailed/seen
│   │                        dedup state (COMMITTED — the Action needs it)
│   ├── h1b_sponsors.json  ← cached sponsor → confirmed ATS board mapping
│   ├── resume_kb.db       ← RAG vectors for résumé bullets (gitignored, private)
│   └── outreach_state.db  ← cold-email send/bounce state (gitignored, private)
├── docs/                  ← served by GitHub Pages
│   ├── index.html         ← the public job board (regenerated daily)
│   ├── employers.json     ← 53K-company H-1B + salary lookup dataset
│   ├── tracker.html       ← private, browser-only application tracker
│   └── SETUP.md · AGENT.md · PROMPTS.md   ← setup + AI-assistant docs
└── src/
    ├── core/              ← shared infrastructure
    │   ├── config.py        ← loads config/secrets from .env; repo-root resolution
    │   ├── gemini.py        ← free-tier Gemini client: LLM + embeddings
    │   ├── h1b_db.py        ← USCIS H-1B CSV → SQLite, ranked by new approvals
    │   └── gmail_sender.py  ← Gmail send: SMTP app-password or OAuth + attachments
    ├── jobs/              ← sourcing sponsor roles
    │   ├── ats.py           ← Greenhouse/Lever/Ashby/Workday board readers
    │   ├── h1b_greenhouse.py← board resolver, fresh-role puller, emailed-dedup
    │   └── company_lookup.py← builds docs/employers.json (H-1B + salary data)
    ├── ranking/           ← résumé fit + retrieval
    │   ├── fit_ranker.py    ← sponsorship gate + Gemini(free) fit scoring, keyword fallback
    │   └── resume_kb.py     ← RAG: semantic + keyword retrieval over résumé bullets
    ├── digest/            ← the daily half  (entry point)
    │   ├── daily_workflow.py← THE daily entry point: site + rank + email top 10
    │   ├── jobs_site.py     ← generates the public site (docs/index.html)
    │   └── daily_job_email.py← builds + emails the HTML top-10 digest
    └── outreach/          ← the cold-email half (interactive)
        ├── outreach.py      ← targeted, human-in-the-loop CLI  (start here)
        ├── outreach_server.py← local review server behind the digest's outreach button
        ├── prospeo_lookup.py← recruiter search + verified-email reveal
        ├── email_generator.py← Gemini emails, grounded in ranking.resume_kb (template fallback)
        ├── sheets_logger.py ← Google Sheets logging + dedup
        └── bounce_retry.py  ← detects bounced sends, retries alternate address patterns
```

Run any module as `python -m src.<pkg>.<module>` (e.g. `python -m src.digest.daily_workflow`).
Imports are absolute (`from src.core import config`), so every folder is a package with an
`__init__.py`.

---

## Configuration

Everything lives in `.env` (loaded by `src/core/config.py`); see `.env.example` for the full list.

| Variable | What it is |
|----------|-----------|
| `GEMINI_API_KEY` | Google AI Studio key — the only LLM (free tier). Writes outreach emails **and** ranks daily fit. Without it, ranking uses the keyword scorer and outreach uses a template |
| `GMAIL_APP_PASSWORD` | *Optional* — Gmail App Password; sends via SMTP (headless, used by the Action). Unset = browser OAuth |
| `PROSPEO_API_KEY` | Prospeo key (recruiter email lookup) |
| `SENDER_EMAIL` | Gmail address you send from |
| `SHEETS_SPREADSHEET_ID` | Target Google Sheet ID |
| `YOUR_NAME` / `YOUR_PHONE` / `YOUR_LINKEDIN` | Signature fields |
| `YOUR_EMAIL_PRIMARY` / `YOUR_EMAIL_ALT` | Emails shown in the signature |
| `GEMINI_MODEL` | Gemini model for ranking + emails (default `gemini-2.5-flash-lite`) |
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
0 8 * * * cd /path/to/h1b-job-agent && /path/to/venv/bin/python -m src.digest.daily_workflow --to you@example.com >> logs/daily.log 2>&1
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
- If a key is ever exposed, rotate it immediately (Google AI Studio / Prospeo consoles).

---

## Notes & limits

- **Prospeo free tier** ≈ 75 credits/month. Prefer *verified* emails; guessed/pattern
  addresses can bounce. See [docs/AGENT.md](docs/AGENT.md) for the one-reveal-then-pattern strategy.
- **Gmail free tier** allows 500 sends/day.
- ATS board mapping is slug-guess + probe; Greenhouse is name-verified, Lever/Ashby are
  verified by slug strength since they expose no company name.
- This sends real email to real people — keep it targeted and personalized.
```
