# MCP server — tool contract & reference

The project ships an [MCP](https://modelcontextprotocol.io) server (`src/mcp/server.py`,
built on `FastMCP`) that exposes the agent's pipeline as typed tools. Any MCP client —
Claude Code, Claude Desktop — can call them in a chat, no terminal needed.

It is a **thin layer**: every tool is a small wrapper over the existing `jobs` / `ranking`
/ `core` packages. No business logic lives in the server, so the tools stay in lockstep
with the pipeline and read the same local SQLite DBs the daily job maintains.

## Design: tools split by risk

Tools are tiered so the human-in-the-loop rule holds no matter which client connects:

- **Tier 1 — read-only** (safe to auto-run): `search_jobs`, `top_matches`,
  `company_h1b_lookup`, `retrieve_experience`, `sent_outreach`.
- **Tier 2 — draft / lookup** (produce text or fetch a contact, never act):
  `draft_cold_email`, `guess_recruiter_emails`, `prospeo_lookup`.
- **Tier 3 — action** (ACTUALLY SEND): `send_batch`, `check_bounces`,
  `retry_bounced_emails`.
- **Observability:** `recent_actions` — reads the audit log (see below).

**Safety model: the server can't send by *accident*.** Send is a *separate* tool from
draft — you pass the exact, already-reviewed subject + body, so a tool can never
draft-and-send in one call. And every Tier-3 tool **no-ops unless `confirm=True`**: with
`confirm=False` (the default) it returns a dry-run preview/plan and sends nothing. A human
still reviews every message before `confirm=True`. (`check_bounces` is read-only.)

## Transport

stdio (JSON-RPC over stdin/stdout). The client launches the server as a child process;
there is no network port and nothing is exposed off the machine.

```bash
python -m src.mcp.server        # needs: pip install mcp
```

## Tool contract

Every tool's full docstring is the source of truth (visible to the client). Signatures and
representative I/O below.

### `company_h1b_lookup(name: str) -> dict`
A company's H-1B approvals + DOL certified-wage percentiles, for "does X sponsor, and what
do they pay?". Matching is **whole-word / token-based** — a query is never conflated with a
company that merely shares a substring (e.g. `Truist` is not folded into `Altruist`).
`match_quality` is `exact` | `strong` | `weak`; `weak` adds a `match_note`, and any other
plausible companies are surfaced in `other_matches` so the caller can disambiguate.

```jsonc
// company_h1b_lookup("Databricks")
{
  "query": "Databricks",
  "found": true,
  "matched_name": "Databricks INC",
  "match_quality": "exact",
  "is_h1b_sponsor": true,
  "new_employment_approvals": 21,   // fresh cap-subject hires
  "total_approvals": 172,
  "denials": 1,
  "states": ["CA"],
  "wage_median": 166962,            // annual USD, DOL LCA certified
  "wage_p25": 145642,
  "wage_p75": 189592,
  "wage_sample_size": 155
}
```
A company with no USCIS record returns `{"query": ..., "found": false, "note": ...}`.

### `search_jobs(keyword="", min_fit=0, new_only=False, limit=25) -> list[dict]`
Search the daily-ranked sponsor job pool (best-fit first). Every company is a confirmed
H-1B sponsor; postings flagged "no sponsorship" are dropped.

### `top_matches(n=10) -> list[dict]`
The top-N roles by résumé fit. Thin alias over `search_jobs`.

```jsonc
// top_matches(2)[0]
{
  "title": "Software Engineer, AI Platform - New Grad",
  "company": "nuro",
  "ats": "greenhouse",
  "fit_score": 96,
  "fit_reason": "keyword: new-grad, software engineer",
  "first_seen": "2026-07-05",
  "url": "https://nuro.ai/careersitem?gh_jid=7351066"
}
```

### `retrieve_experience(jd_text: str, k=5) -> list[dict]`
RAG over the candidate's curated experience corpus (hybrid semantic + keyword). Returns
`[{text, source, score}]` best-first — the grounding behind a tailored pitch. `[]` if the
corpus is empty.

### `sent_outreach(company="", status="", include_body=False, limit=50) -> list[dict]`
Read-only view over `data/outreach_state.db` — the cold emails you've already sent.
Answers "who have I emailed at X?", "which sends bounced/exhausted?". Filter by company
substring and/or `status` (`sent`/`retried`/`exhausted`/`send_error`); newest first. The
DB is private (gitignored); the tool returns `[]` if nothing has been logged.

```jsonc
// sent_outreach(company="HP IQ", limit=1)
{
  "name": "Vikrant Shokeen",
  "company": "HP IQ",
  "email": "vikrant.shokeen@hp-iq.com",
  "status": "sent",
  "attempts": 1,
  "tried": ["vikrant.shokeen@hp-iq.com"],   // every address pattern tried
  "subject": "AI Software Engineer — Ethan Wu",
  "sent_at": "2026-07-22T01:46:43+00:00"
}
```

### `draft_cold_email(company, title, jd_text="", recruiter_name="", recruiter_title="Recruiter", requisition="") -> dict`
A recruiter cold-email grounded in the candidate's most relevant real experience (RAG),
never mentioning visa/sponsorship. **Drafts only** — returns `{subject, body, note}`; the
server never sends.

### `guess_recruiter_emails(first_name, last_name, domain) -> dict`
Likely addresses for a name, most-common pattern first. Guesses — some will bounce.

```jsonc
// guess_recruiter_emails("Jane", "Doe", "stripe.com")
{
  "candidates": ["jane.doe@stripe.com", "jdoe@stripe.com", "jane@stripe.com", ...],
  "best_guess": "jane.doe@stripe.com",
  "note": "Pattern guesses, best-first — may bounce. Verify before sending."
}
```

### `prospeo_lookup(company_name, company_domain) -> dict`
Finds one recruiter with a **verified** email at a domain via the Prospeo API — the
verified-email counterpart to `guess_recruiter_emails` (which only pattern-guesses).
**Optional:** needs `PROSPEO_API_KEY`; without it returns `{found: false, note}` and you fall
back to guessing. Reveals at most one email (protects credits). Returns
`{found, name, first_name, title, email, linkedin_url, company}` on a hit.
```jsonc
// prospeo_lookup("Stripe", "stripe.com")   — when the key is unset:
{ "found": false,
  "note": "PROSPEO_API_KEY not configured — Prospeo is optional. Use guess_recruiter_emails for pattern-based addresses." }
```

### `send_batch(drafts, confirm=False) -> dict`
⚠️ **Tier 3 — actually sends a whole BATCH** of cold emails in one call (résumé
auto-attached to each) and logs every send. **One call = one confirmation for all
recipients** — a single human approval covers the batch. Does **not** draft — each item is
an already-reviewed draft `{"to","name","subject","body","company"?,"first"?,"last"?}`.
Rows with a bad `to`/`subject`/`body` are validated out *before* anything sends. **No-ops
unless `confirm=True`**: with `confirm=False` it returns a preview of every recipient and
sends nothing.
```jsonc
// send_batch([{to:"a@x.com", name:"Ann Lee", subject:"…", body:"…"}, …])  # confirm defaults False
{ "sent": false, "requires": "confirm=True", "count": 1,
  "note": "DRY RUN — nothing sent. Review this whole batch, then call again with confirm=True to send ALL of them.",
  "preview": [ { "to": "a@x.com", "name": "Ann Lee", "company": "", "subject": "…" } ],
  "invalid": [] }
// with confirm=True →
{ "sent": true, "count": 3, "sent_ok": 3,
  "results": [ { "to": "a@x.com", "name": "Ann Lee", "status": "sent" }, … ] }
```

### `check_bounces(since_days=3) -> dict`
⚠️ **Tier 3 — read-only.** Scans your inbox over IMAP for hard-bounce notices and reports
which sent addresses bounced, cross-referenced with the send log (name + company). Resends
nothing. Needs `GMAIL_APP_PASSWORD`. Returns `{bounced, count, matched:[{name, company,
email, status, attempts}]}`.

### `retry_bounced_emails(confirm=False, max_retries=3, since_days=3) -> dict`
⚠️ **Tier 3 — the retry-on-rebounce mechanism.** Resends bounced emails using the *next
untried* address pattern (`first.last → flast → first@ → …`); run it again after a
re-bounce and it advances to the next pattern, marking a recruiter `exhausted` when patterns
or `max_retries` run out. **No-ops unless `confirm=True`** — `confirm=False` returns the
plan only. Returns `{resent, count, actions:[…]}`.

## Action log (observability)

Every tool call is recorded to a private SQLite log, `data/agent_log.db`, by a single
decorator (`src/mcp/audit.py` → `audited`, applied via `audited_tool`). Each row captures
`ts, tool, args, ok, duration_ms, result, error` — reads, drafts, sends and dry-runs alike.
Arguments and results are **truncated/summarized** (no full email bodies) and the DB is
gitignored (args can include recipient addresses). Logging is wrapped in `try/except` so a
logging failure can never break a tool call.

### `recent_actions(tool="", ok_only=False, limit=50) -> list[dict]`
Reads that log, newest first — "what did my agent do?" / "did anything error?". Filter by a
tool-name substring and/or `ok_only`.
```jsonc
// recent_actions(tool="company_h1b_lookup", limit=1)
{
  "ts": "2026-07-22T02:10:04+00:00",
  "tool": "company_h1b_lookup",
  "ok": true,
  "duration_ms": 18,
  "args": "{\"name\": \"Databricks\"}",
  "result": "{\"found\": true, \"matched_name\": \"Databricks INC\"}",
  "error": null
}
```

## Register with a client

**Claude Code:**
```bash
claude mcp add h1b-agent -- \
  bash -c "cd /ABS/PATH/h1b-job-agent && exec ./venv/bin/python -m src.mcp.server"
```

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "h1b-agent": {
      "command": "bash",
      "args": ["-c", "cd /ABS/PATH/h1b-job-agent && exec ./venv/bin/python -m src.mcp.server"]
    }
  }
}
```
The `bash -c "cd … && …"` wrapper is required because `-m src.mcp.server` must run from the
project root for the `src` package to import.

Then ask, in any chat: *"Does Databricks sponsor H-1B and what do they pay?"* or
*"show my top matches."*

## Tests

```bash
python -m unittest tests.test_mcp_lookup       # stdlib only, no pytest
```
Covers the whole-word matcher (including the Truist/Altruist regression) and the
end-to-end lookup (skips gracefully if the USCIS DB is absent).
