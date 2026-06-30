# PROMPT.md — Ready-to-paste prompts

Drop one of these into Claude Code / Cowork from the repo root to start a run.
The agent should read **[AGENT.md](AGENT.md)** first — that holds the full workflow,
defaults, and guardrails. These are just the kickoff prompts.

---

## 1. Standard outreach (paste a company + JD)

```
Read AGENT.md, then run the outreach workflow for this role.

Company: <COMPANY NAME>
Domain:  <company.com>
Role:    <EXACT JOB TITLE> on the <TEAM> team
JD:
<PASTE FULL JOB DESCRIPTION HERE>

Find 5–10 US-based recruiters/early-career/campus recruiters (and a hiring
manager on this team if obvious). FIRST find the company's email pattern ONLINE
(web search / known formats) and use that to construct the addresses. Only if I
tell you the pattern is wrong (bounces), spend ONE Prospeo reveal to determine
the correct pattern, then reconstruct the rest from it. Draft a ~100–120 word
tailored email per person, lead with UC Berkeley CS + Genuine Parts Company
(Fortune 200), tie my resume to this JD, sign with both emails (gmail +
berkeley), no GitHub. SHOW me every draft and WAIT — do not send until I say
"send".
```

---

## 2. Reply to a recruiter who responded

```
Read AGENT.md. A recruiter replied — help me draft a response.

Recruiter: <NAME>, <TITLE> at <COMPANY>
Their message:
<PASTE THEIR EMAIL>

Write a concise, gracious reply that keeps the door open. Show me the draft to
copy — do not send.
```

---

## 3. Find more contacts at a company already started

```
Read AGENT.md. I already emailed <COMPANY> using the pattern <PATTERN>.
Find 5 more US-based recruiters/managers for the <ROLE> role, reuse the known
pattern (no new Prospeo reveals unless the pattern looks inconsistent), draft
emails, and show me before sending.
```

---

## Non-negotiables (every prompt)
- **Always show the full draft(s) and wait for an explicit "send" — no exceptions.**
- Prefer Prospeo-VERIFIED emails; warn before sending to guessed/pattern addresses.
- Find the email pattern ONLINE first and use it; only fall back to a Prospeo reveal if I say the pattern is wrong (save free-tier credits).
- Never mention H1B unless the company is a confirmed sponsor.
- Never commit or paste secrets (they live only in `.env`).
