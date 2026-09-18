# H-1B Job Agent — MCP Server

**The H-1B Job Agent exposed as typed [Model Context Protocol](https://modelcontextprotocol.io)
tools.** Point any MCP client — **Claude Desktop** or **[Claude Code](https://claude.com/claude-code)** —
at the server and, straight from a chat, search jobs at confirmed H-1B sponsors, look up a
company's H-1B approvals and certified wages, draft résumé-grounded cold emails, send a
reviewed batch (only when you confirm), and track bounces, replies, and applications.
No terminal, no web UI.

Everything that sends is **human-in-the-loop**: draft and send are *separate* tools, and
every send/retry tool **no-ops unless you pass `confirm=True`** — so nothing goes out by
accident. The job and company data is *derived from the USCIS H-1B Employer Data Hub*, so a
role can't surface unless that employer actually sponsored H-1B.

---

## Quickstart

```bash
git clone https://github.com/emhw0930/cold-email-agent.git h1b-job-agent
cd h1b-job-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt        # includes the MCP runtime (mcp, pinned <2)
cp .env.example .env                   # add your keys + signature (see docs/SETUP.md)
python -m src.mcp.server               # serves on stdio
```

The server reads the SQLite DBs that ship **committed** in the repo (USCIS-derived sponsor +
wage data and a ranked job pool), so the read-only tools work on first run. Then register it
with a client (below) and ask, in any chat:
*"Does Verkada sponsor H-1B and what do they pay?"* or *"show my top matches."*

**First time? Full step-by-step walkthrough → [docs/ONBOARDING.md](docs/ONBOARDING.md).**

---

## Tools

| Tool | Tier | What it does |
|------|------|--------------|
| `search_jobs(keyword, min_fit, new_only)` | read | Search the ranked sponsor job pool |
| `top_matches(n)` | read | Top-N roles by résumé fit |
| `company_h1b_lookup(name)` | read | A company's H-1B approvals + certified wage percentiles |
| `retrieve_experience(jd_text)` | read | RAG: résumé bullets most relevant to a JD |
| `sent_outreach(company, status)` | read | Cold emails you've already sent (who, status, bounces) |
| `draft_cold_email(company, title, jd_text, recruiter_name)` | draft | RAG-grounded recruiter email (subject + body) — **returns text only** |
| `guess_recruiter_emails(first, last, domain)` | draft | Likely email addresses for a name, best-pattern first |
| `prospeo_lookup(company_name, domain)` | draft | A recruiter + **verified** email via Prospeo (optional key; falls back to guessing) |
| `send_batch(drafts, confirm)` | **send** | Sends a whole batch of reviewed emails in ONE call (résumé attached) + logs each — **no-op unless `confirm=True`**, so one approval covers all recipients |
| `check_bounces(since_days)` | send | Read-only: which sent addresses bounced (IMAP), matched to the send log |
| `retry_bounced_emails(confirm, …)` | send | Resend bounced emails with the next address pattern — **no-op unless `confirm=True`** |
| `recent_actions(tool, ok_only)` | log | Read the agent's own action log — every tool call, newest first |
| `check_replies(since_days)` | reply | Scan inbox for human replies, classify (interview/rejected/replied), update the send log |
| `needs_followup(days)` | reply | Delivered + no-reply + aged — your follow-up candidates |
| `outreach_stats()` | reply | Response funnel per company: sent → replied → interview/rejected + rate % |
| `record_application(company, role, jd_text, …)` | apps | Log a job application (stores the full JD) to `applications.db` |
| `list_applications(company, outcome, stage)` | apps | Query tracked applications, newest first |
| `application_stats()` | apps | Application funnel: applied → cold_email → phone_screen → oa → rounds |

**Safety model — the server can't send by *accident*.** Send is a *separate* tool from
draft (you pass reviewed bodies — no draft-and-send in one call); `send_batch` takes the
whole batch in one call so a single approval covers all recipients; and every send/retry
tool **no-ops unless `confirm=True`**, returning a dry-run preview otherwise. A human
reviews every message before `confirm=True`.

---

## Register with a client

Register with **Claude Code**:
```bash
claude mcp add h1b-agent -- \
  bash -c "cd /ABS/PATH/h1b-job-agent && exec ./venv/bin/python -m src.mcp.server"
```
…or with **Claude Desktop** — add an `mcpServers` entry to
`~/Library/Application Support/Claude/claude_desktop_config.json` (see
[docs/MCP.md](docs/MCP.md#register-with-a-client)).

**Full tool contract, I/O examples, and design notes:** [docs/MCP.md](docs/MCP.md).
Tests: `python -m unittest tests.test_mcp_lookup`.

---

## Configuration

Everything lives in `.env` (loaded by `src/core/config.py`); see `.env.example` for the full
list. The variables the server uses:

| Variable | What it is |
|----------|-----------|
| `GEMINI_API_KEY` | Google AI Studio key (free tier) — the LLM behind `draft_cold_email` and résumé retrieval. Without it, drafting falls back to a template |
| `PROSPEO_API_KEY` | *Optional* — Prospeo key for verified recruiter lookup; empty = pattern-guessing only |
| `SENDER_EMAIL` | Gmail address you send from |
| `GMAIL_APP_PASSWORD` | *Optional* — Gmail App Password; sends via SMTP (headless). Unset = browser OAuth |
| `YOUR_NAME` / `YOUR_PHONE` / `YOUR_LINKEDIN` | Signature fields |
| `YOUR_EMAIL_PRIMARY` / `YOUR_EMAIL_ALT` | Emails shown in the signature |
| `SHEETS_SPREADSHEET_ID` | *Optional* — Sheet ID for the outreach log; empty = no log/sheet dedup |
| `GEMINI_MODEL` | Gemini model for drafting (default `gemini-2.5-flash-lite`) |
| `VERIFIED_ONLY` | `true` (default) = only send to Prospeo-verified emails |
| `DRY_RUN` | `true` = never actually send |

---

## Project structure

The server lives in `src/mcp/server.py` and reuses the agent's shared modules; it reads the
committed SQLite DBs in `data/`.

```
h1b-job-agent/
├── .env.example           ← copy to .env; all secrets live in .env (gitignored)
├── requirements.txt
├── data/
│   ├── h1b_employers.db   ← USCIS sponsors + certified wages + ranked job pool (committed)
│   ├── resume_kb.db       ← RAG vectors for résumé bullets (gitignored, private)
│   └── outreach_state.db  ← cold-email send/bounce state (gitignored, private)
└── src/
    ├── core/              ← config, free-tier Gemini client, USCIS H-1B DB, Gmail sender
    ├── ranking/           ← résumé-fit scoring + RAG retrieval (resume_kb)
    ├── outreach/          ← recruiter lookup (Prospeo), email drafting, bounce retry
    └── mcp/
        └── server.py      ← the MCP tools (search · lookup · draft · send · track)
```

Run modules as `python -m src.<pkg>.<module>`; imports are absolute (`from src.core import
config`), so every folder is a package with an `__init__.py`.

> The repo also contains the daily digest/site pipeline (`src/jobs`, `src/digest`) that keeps
> `data/h1b_employers.db` fresh — the read tools query whatever it last produced.

---

## Security

- **Secrets are never committed** — `.env`, `assets/*.json`, and `assets/resume.pdf` are
  gitignored.
- `data/h1b_employers.db` **is** committed on purpose — it holds only public USCIS-derived
  data (sponsors, wages, the ranked job pool). No personal data.
- Gmail: the OAuth path is scoped **send-only**; the app-password path is a separate 16-char
  credential you can revoke anytime at myaccount.google.com without touching your real password.
- If a key is ever exposed, rotate it immediately (Google AI Studio / Prospeo consoles).

---

## Notes & limits

- **Prospeo free tier** ≈ 75 credits/month. Prefer *verified* emails; guessed/pattern
  addresses can bounce. See [docs/AGENT.md](docs/AGENT.md) for the one-reveal-then-pattern strategy.
- **Gmail free tier** allows 500 sends/day.
- `send_batch` / `retry_bounced_emails` send real email to real people — keep it targeted and
  personalized, and a human reviews every draft before `confirm=True`.
