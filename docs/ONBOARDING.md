# Onboarding — get the H-1B Job Agent MCP server running

A step-by-step for a first-time user. By the end you'll be able to ask, in a normal
Claude chat, things like *"Does Stripe sponsor H-1B and what do they pay?"* or
*"show my top job matches"* — no terminal needed once it's set up.

> **The quickest possible start:** do steps 1, 2, 5, 6 and skip everything about keys and
> résumés. The **lookup and job-search tools work immediately** off the H-1B database that
> ships with the repo — no API keys, no résumé. Add the rest only when you want the agent to
> *draft* and *send* cold emails.

---

## What you need

- **Python 3.11 or newer** (`python3 --version` to check)
- **git**
- The **Claude Desktop** app (or Claude Code)
- *(Optional)* API keys — only for drafting/sending email; see step 3.

---

## Step 1 — Clone and install

```bash
cd ~/Desktop
git clone https://github.com/emhw0930/cold-email-agent.git h1b-job-agent
cd h1b-job-agent
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
```

> **If `import mcp` later fails** with a message about *"mcp 2.x / FastMCP renamed to
> MCPServer"*, pin the MCP library back to v1 — this server is built on the v1 API:
> ```bash
> ./venv/bin/python -m pip install "mcp>=1.28.0,<2"
> ```

## Step 2 — Create your `.env`

```bash
cp .env.example .env
```

You can leave everything blank for now. The file just needs to exist. What each key unlocks:

| Key | Unlocks | Get it |
|-----|---------|--------|
| *(none)* | **Company H-1B lookup + job search** — works out of the box | — |
| `GEMINI_API_KEY` | **Drafting** cold emails (and better résumé retrieval) | aistudio.google.com — free |
| `PROSPEO_API_KEY` | **Verified** recruiter emails (instead of guessed) | prospeo.io — optional |
| `SENDER_EMAIL` + `GMAIL_APP_PASSWORD` | Actually **sending** email | myaccount.google.com → Security → App passwords (needs 2-Step Verification) |

Also fill in the signature fields (`YOUR_NAME`, `YOUR_LINKEDIN`, …) if you'll send email.

## Step 3 — Résumé (optional — only for drafting/sending)

There is **no résumé needed** for the lookup and job-search tools. It matters only for the
cold-email side, and it's two *separate* files, both of which stay **private** (they're
gitignored — never committed or pushed):

- **`assets/experience.json`** — your résumé as a list of short **bullets** (not a PDF).
  This is what grounds the drafted emails: the writer is allowed to use *only* these facts,
  as an anti-fabrication guardrail. Create it by copying the template and editing it:
  ```bash
  cp assets/experience.example.json assets/experience.json
  # then edit assets/experience.json — one accomplishment per entry
  ```
  The search index (`data/resume_kb.db`) builds itself from this file automatically the
  first time a drafting tool runs. Without this file, drafts still work but are generic, and
  `retrieve_experience` returns nothing.

- **`assets/resume.pdf`** — your actual résumé PDF. It's **attached** to emails when you
  send. Just drop your PDF at that path. If it's missing, sending still works — it just
  goes out without an attachment (with a warning).

## Step 4 — Verify it works

```bash
./venv/bin/python -m unittest tests.test_mcp_lookup
```

You should see `OK`. That confirms the server and the H-1B database load correctly.

## Step 5 — Register with Claude

**Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json`
and add the `h1b-agent` server (use **your own** absolute path to the folder):

```json
{
  "mcpServers": {
    "h1b-agent": {
      "command": "bash",
      "args": [
        "-c",
        "cd /Users/YOUR_USERNAME/Desktop/h1b-job-agent && exec ./venv/bin/python -m src.mcp.server"
      ]
    }
  }
}
```

If you already have other servers under `mcpServers`, add `h1b-agent` **alongside** them —
don't replace the block.

**Claude Code** (CLI) — instead of editing the file, run:

```bash
claude mcp add h1b-agent -- \
  bash -c "cd /Users/YOUR_USERNAME/Desktop/h1b-job-agent && exec ./venv/bin/python -m src.mcp.server"
```

> The `bash -c "cd … && …"` wrapper is required: the server must launch **from the project
> root** or the `src` package won't import.

## Step 6 — Restart Claude and try it

Fully **quit and reopen** Claude (MCP servers are read at startup). Then ask in any chat:

- *"Does Nvidia sponsor H-1B, and what's the median wage?"*
- *"Show my top 5 H-1B-sponsor job matches."*
- *"Draft a cold email to a recruiter at Stripe for a New Grad SWE role."* *(needs a Gemini key + `experience.json`)*

---

## What the tools can do

| Tier | Tools | What they do |
|------|-------|--------------|
| **Read** (safe) | `search_jobs`, `top_matches`, `company_h1b_lookup`, `retrieve_experience`, `sent_outreach` | Query jobs, sponsors, wages, and your send history |
| **Draft** | `draft_cold_email`, `guess_recruiter_emails`, `prospeo_lookup` | Write emails / find contacts — **never sends** |
| **Send** | `send_batch`, `check_bounces`, `retry_bounced_emails` | Actually send / track bounces |

**You can't send by accident.** Drafting and sending are separate tools, and every sending
tool does nothing unless it's called with `confirm=True` — otherwise it just returns a
preview. A human reviews every message before it goes out. And with no Gmail credentials in
`.env`, sending isn't possible at all.

---

## Troubleshooting

- **`No module named 'mcp.server.fastmcp'` / "this is mcp 2.x"** → run the pin from step 1:
  `pip install "mcp>=1.28.0,<2"`.
- **The tools don't show up in Claude** → you must fully **quit and reopen** the app after
  editing the config. Double-check the absolute path and that the JSON is valid.
- **`ModuleNotFoundError: src`** → the launch command must `cd` into the project root first
  (that's what the `bash -c "cd … && …"` wrapper does).
- **Company lookups look outdated** → the H-1B data is a snapshot committed in the repo; it's
  as current as the repo's `data/h1b_employers.db`.

Full tool reference and I/O examples: [MCP.md](MCP.md). Key/secret details: [SETUP.md](SETUP.md).
