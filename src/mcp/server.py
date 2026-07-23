# ============================================================
#  server.py — MCP server for the H-1B job agent
#
#  Exposes the project as typed tools any MCP client (Claude Desktop,
#  Claude Code, …) can call. This is a THIN layer: every tool is a small
#  wrapper over the existing packages (jobs / ranking / core) — no business
#  logic lives here.
#
#  Tools are split by risk so the human-in-the-loop rule survives regardless
#  of which client connects:
#    TIER 1 — read-only, safe to auto-run:
#        search_jobs · top_matches · company_h1b_lookup · retrieve_experience
#        · sent_outreach
#    TIER 2 — draft (produce text, never act):
#        draft_cold_email · guess_recruiter_emails
#    TIER 3 — action (ACTUALLY SEND): send_batch · check_bounces
#        · retry_bounced_emails. Safety is "can't send by ACCIDENT": send is a
#        separate tool from draft (you pass reviewed bodies — no draft-and-send
#        in one call), send_batch sends the whole batch in ONE call so one
#        approval covers all recipients, and every action tool no-ops unless
#        confirm=True.
#
#  Run (stdio transport):
#    python -m src.mcp.server
#  Register with Claude Code:
#    claude mcp add h1b-agent -- /path/to/venv/bin/python -m src.mcp.server
# ============================================================

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src.core import h1b_db
from src.jobs.company_lookup import norm, _titlecase
from src.ranking import resume_kb
from src.outreach.email_generator import generate_email_body, generate_subject
from src.outreach.bounce_retry import (candidate_emails, STATE_DB, record_send,
                                       fetch_bounced, retry_bounced)
from src.core.gmail_sender import send_email
from src.outreach.prospeo_lookup import find_recruiter
from src.outreach import reply_tracker
from src.outreach import applications
from src.mcp.audit import audited, read_actions

mcp = FastMCP("h1b-job-agent")


def audited_tool(*targs, **tkwargs):
    """Like @mcp.tool() but every call is also written to the action log. The
    `audited` wrapper is applied INSIDE mcp.tool() so FastMCP registers the
    logged callable while still reading the original signature for its schema."""
    def deco(fn):
        return mcp.tool(*targs, **tkwargs)(audited(fn))
    return deco


def _match_score(query_key: str, cand_norm: str) -> int:
    """Score how well a candidate employer (already norm()'d) matches the query
    (already norm()'d), on a 0-100 scale using WHOLE-WORD tokens.

    This deliberately does NOT treat a within-word substring as a match — the
    old `key in cand` test made "TRUIST" match "ALTRUIST" and silently fold a
    different company's numbers in. Matching on token sets fixes that:
        100 exact · 80 query is a whole-word subset of candidate
        70  candidate is a whole-word subset of query · else token overlap · 0 none
    """
    if not cand_norm or not query_key:
        return 0
    if cand_norm == query_key:
        return 100
    q, c = set(query_key.split()), set(cand_norm.split())
    if q and q <= c:                       # every query word appears whole in cand
        return max(60, 80 - (len(c) - len(q)))
    if c and c <= q:                       # candidate is a whole-word subset of query
        return max(55, 70 - (len(q) - len(c)))
    inter = q & c
    if inter:                              # partial overlap (e.g. one shared word)
        return int(50 * len(inter) / max(len(q), len(c)))
    return 0


# ── TIER 1: read-only tools ──────────────────────────────────
@audited_tool()
def search_jobs(keyword: str = "", min_fit: int = 0,
                new_only: bool = False, limit: int = 25) -> list[dict]:
    """Search the daily-ranked H-1B-sponsor job pool.

    Returns roles the pipeline has already scraped and scored against the
    résumé, best-fit first. Every company is a confirmed H-1B sponsor.

    Args:
        keyword: case-insensitive substring matched against title + company
                 (empty = all).
        min_fit: only roles with fit_score >= this (0-100).
        new_only: only roles first seen today.
        limit: max rows to return.
    """
    import datetime as dt
    conn = h1b_db.connect()
    try:
        rows = conn.execute(
            "SELECT title, company, ats, url, fit_score, fit_reason, first_seen, "
            "no_sponsor FROM seen_jobs WHERE fit_score IS NOT NULL "
            "ORDER BY fit_score DESC").fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    today = dt.date.today().isoformat()
    kw = keyword.lower().strip()
    out = []
    for r in rows:
        if r["no_sponsor"]:
            continue
        if (r["fit_score"] or 0) < min_fit:
            continue
        if new_only and (r["first_seen"] or "") != today:
            continue
        hay = f"{r['title']} {r['company']}".lower()
        if kw and kw not in hay:
            continue
        out.append({
            "title": r["title"],
            "company": (r["company"] or "").split("|")[0],  # clean Workday token
            "ats": r["ats"],
            "fit_score": r["fit_score"], "fit_reason": r["fit_reason"],
            "first_seen": r["first_seen"], "url": r["url"],
        })
        if len(out) >= limit:
            break
    return out


@audited_tool()
def top_matches(n: int = 10) -> list[dict]:
    """Return the top-N roles by résumé fit from the scored sponsor pool
    (drops any posting flagged 'no sponsorship'). Best fit first."""
    return search_jobs(keyword="", min_fit=0, new_only=False, limit=n)


@audited_tool()
def company_h1b_lookup(name: str) -> dict:
    """Look up a company's H-1B sponsorship record and certified wages.

    Aggregates USCIS H-1B Employer Data Hub approvals (across fiscal years)
    and DOL LCA certified-wage percentiles for the best-matching employer.
    Use it to answer "does <company> sponsor H-1B, and what do they pay?"

    Returns is_h1b_sponsor, new_employment_approvals (fresh cap-subject hires),
    total_approvals, denials, and wage_median / p25 / p75 (annual USD) when DOL
    wage data is available.
    """
    key = norm(name)
    if not key:
        return {"query": name, "found": False, "note": "empty/unparseable name"}

    first = key.split()[0]
    conn = h1b_db.connect()
    emp_rows = conn.execute(
        "SELECT employer, state, new_approval, total_approvals, new_denial "
        "FROM employers WHERE UPPER(employer) LIKE ?", (f"%{first}%",)).fetchall()

    # Group candidate rows by normalized employer name, then score each DISTINCT
    # company as a whole. We only ever aggregate rows within the single best
    # company — never across two different companies that happen to share a
    # substring (the old Truist/Altruist bug summed both together).
    groups: dict[str, list] = {}
    for r in emp_rows:
        groups.setdefault(norm(r["employer"]), []).append(r)
    scored = sorted(
        ((_match_score(key, g), g, rows) for g, rows in groups.items()),
        key=lambda t: t[0], reverse=True)
    scored = [t for t in scored if t[0] > 0]

    best = scored[0] if scored else None
    matched = best[2] if best else []
    # Other distinct companies that also matched — surfaced so the caller can
    # disambiguate instead of trusting a single guess.
    alternatives = [_titlecase(rows[0]["employer"]) for _, _, rows in scored[1:4]]

    # wages: exact normalized key first, then a whole-word-scored fallback
    wage = conn.execute(
        "SELECT sample_name, state, n_lca, wage_median, wage_p25, wage_p75 "
        "FROM employer_wages WHERE employer_norm = ?", (key,)).fetchone()
    if not wage:
        best_w = 0
        for r in conn.execute(
                "SELECT sample_name, state, n_lca, wage_median, wage_p25, wage_p75 "
                "FROM employer_wages WHERE employer_norm LIKE ?", (f"%{first}%",)):
            s = _match_score(key, norm(r["sample_name"]))
            if s >= 70 and s > best_w:      # require a strong whole-word match
                wage, best_w = r, s
    conn.close()

    if not matched and not wage:
        return {"query": name, "found": False,
                "note": "no USCIS H-1B record found — likely not an H-1B sponsor, "
                        "or the legal name differs from the brand name"}

    new_appr = sum(r["new_approval"] or 0 for r in matched)
    tot_appr = sum(r["total_approvals"] or 0 for r in matched)
    denials = sum(r["new_denial"] or 0 for r in matched)
    states = sorted({r["state"] for r in matched if r["state"]})
    display = _titlecase(matched[0]["employer"]) if matched else (
        wage["sample_name"] if wage else name)

    top_score = best[0] if best else 0
    quality = ("exact" if top_score >= 100 else
               "strong" if top_score >= 70 else "weak")

    result = {
        "query": name,
        "found": True,
        "matched_name": display,
        "match_quality": quality,
        "is_h1b_sponsor": tot_appr > 0,
        "new_employment_approvals": new_appr,
        "total_approvals": tot_appr,
        "denials": denials,
        "states": states,
    }
    if quality == "weak":
        result["match_note"] = ("only a loose name match — verify this is the "
                                "company you meant before relying on it")
    if alternatives:
        result["other_matches"] = alternatives
    if wage:
        result["wage_median"] = wage["wage_median"]
        result["wage_p25"] = wage["wage_p25"]
        result["wage_p75"] = wage["wage_p75"]
        result["wage_sample_size"] = wage["n_lca"]
    else:
        result["wage_note"] = "no DOL LCA certified-wage data for this employer"
    return result


@audited_tool()
def retrieve_experience(jd_text: str, k: int = 5) -> list[dict]:
    """Retrieve the résumé bullets most relevant to a job description (RAG).

    Runs hybrid semantic + keyword search over the candidate's curated
    experience corpus. Use it to ground a tailored pitch, cover letter, or
    cold email in the candidate's real, most-relevant experience for a role.
    Returns [{text, source, score}] best-first, or [] if the corpus is empty.
    """
    if not (jd_text or "").strip():
        return []
    return resume_kb.retrieve(jd_text, k=k)


@audited_tool()
def sent_outreach(company: str = "", status: str = "",
                  include_body: bool = False, limit: int = 50) -> list[dict]:
    """Look up cold-outreach emails you've already sent, from the local send log.

    Read-only view over data/outreach_state.db (the same record the bounce-retry
    flow maintains). Use it to answer "who have I already emailed at <company>?",
    "which of my sends bounced / got exhausted?", or "what did I send to <person>?".

    Args:
        company: case-insensitive substring filter on company name (empty = all).
        status:  filter by send status — sent | retried | exhausted | send_error
                 (empty = all).
        include_body: also return the full email body (off by default to keep
                 results small; a short subject-line preview is always included).
        limit:   max rows, most-recent first.

    Returns [{name, company, email, status, reply_status, attempts, tried,
    subject, sent_at, (body)}], newest first — or [] if nothing has been logged.
    reply_status (no_reply|replied|interview|rejected|auto_reply) comes from
    check_replies; it is "no_reply" until you run that.
    """
    import os, sqlite3
    if not os.path.exists(STATE_DB):
        return []
    conn = sqlite3.connect(STATE_DB)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(outreach_sends)")}
        has_reply = "reply_status" in cols          # migration may not have run yet
        reply_sel = ", reply_status, last_reply_at" if has_reply else ""
        rows = conn.execute(
            "SELECT name, company, current_email, status, attempts, tried, "
            f"subject, body, updated_at{reply_sel} FROM outreach_sends "
            "ORDER BY updated_at DESC").fetchall()
    except sqlite3.OperationalError:
        return []                      # table not created yet
    finally:
        conn.close()

    comp = company.lower().strip()
    st = status.lower().strip()
    out = []
    for r in rows:
        if comp and comp not in (r["company"] or "").lower():
            continue
        if st and st != (r["status"] or "").lower():
            continue
        rec = {
            "name": r["name"],
            "company": r["company"],
            "email": r["current_email"],
            "status": r["status"],
            "reply_status": (r["reply_status"] if has_reply else "no_reply") or "no_reply",
            "attempts": r["attempts"],
            "tried": [e for e in (r["tried"] or "").split(",") if e],
            "subject": r["subject"],
            "sent_at": r["updated_at"],
        }
        if has_reply and r["last_reply_at"]:
            rec["last_reply_at"] = r["last_reply_at"]
        if include_body:
            rec["body"] = r["body"]
        out.append(rec)
        if len(out) >= limit:
            break
    return out


# ── TIER 2: draft tools (produce text — NEVER send) ──────────
@audited_tool()
def draft_cold_email(company: str, title: str, jd_text: str = "",
                     recruiter_name: str = "", recruiter_title: str = "Recruiter",
                     requisition: str = "") -> dict:
    """Draft a personalized recruiter cold-email for a role — subject + body.

    The body is grounded in the candidate's most relevant REAL experience for
    this job (RAG over their résumé), never mentions visa/sponsorship, and makes
    one low-friction ask. Uses Gemini when available, else a plain template.

    IMPORTANT: this only DRAFTS — it does not send anything. The draft is meant
    to be reviewed by the human, who sends it themselves. There is intentionally
    no send tool in this server. Returns {subject, body}.
    """
    first = (recruiter_name or "").split()[0] if recruiter_name else ""
    job = {
        "title": title, "company": company,
        "description_snippet": (jd_text or "")[:600],
        "req": requisition, "job_id": "",
    }
    recruiter = {
        "name": recruiter_name or "there",
        "first_name": first,
        "title": recruiter_title or "Recruiter",
        "email": "",
    }
    return {
        "subject": generate_subject(job),
        "body": generate_email_body(job, recruiter),
        "note": "DRAFT ONLY — review, then send from your own mail client. "
                "This server never sends.",
    }


@audited_tool()
def guess_recruiter_emails(first_name: str, last_name: str, domain: str) -> dict:
    """Generate likely email addresses for a person at a company domain, ordered
    most-common pattern first (first.last, flast, first, …).

    Use when you have a recruiter's name (e.g. from LinkedIn) but not their
    address. These are GUESSES — verify before relying on them; some will bounce.
    Returns {candidates: [...], best_guess: str}.
    """
    cands = candidate_emails(first_name, last_name, domain)
    return {
        "candidates": cands,
        "best_guess": cands[0] if cands else "",
        "note": "Pattern guesses, best-first — may bounce. Verify before sending.",
    }


@audited_tool()
def prospeo_lookup(company_name: str, company_domain: str) -> dict:
    """Find ONE recruiter with a VERIFIED email at a company, via the Prospeo API.

    Unlike guess_recruiter_emails (which pattern-guesses and may bounce), this
    returns a real, verified address when Prospeo has one for a recruiter at the
    domain. It ranks people toward recruiter/talent titles and reveals at most one
    email to protect API credits.

    OPTIONAL: needs PROSPEO_API_KEY. Without it the tool returns found=false and
    you should fall back to guess_recruiter_emails. Costs Prospeo credits when it
    reveals an email.

    Returns {found, name, first_name, title, email, linkedin_url, company} on a
    hit, else {found: false, note}.
    """
    from src.core import config
    if not (getattr(config, "PROSPEO_API_KEY", "") or "").strip():
        return {"found": False,
                "note": "PROSPEO_API_KEY not configured — Prospeo is optional. "
                        "Use guess_recruiter_emails for pattern-based addresses."}
    if not (company_domain or "").strip():
        return {"found": False,
                "note": "company_domain is required, e.g. 'stripe.com'"}
    try:
        r = find_recruiter(company_name, company_domain)
    except Exception as e:
        return {"found": False, "error": f"{type(e).__name__}: {e}"}
    if not r:
        return {"found": False,
                "note": "no recruiter with a verifiable email found for this domain"}
    return {"found": True, **r}


# ── TIER 3: action tools (ACTUALLY SEND — confirm-gated) ─────
#
#   These are the only tools that touch the outside world. The safety model is
#   NO LONGER "the server can't send" — it's "the server can't send by accident":
#     • send is SEPARATE from draft — you pass already-reviewed subject + bodies
#       (a tool can never draft-and-send in one call);
#     • send_batch takes the WHOLE batch in one call, so a single human approval
#       covers every recipient (one confirmation, not one per email);
#     • every action tool refuses to act unless confirm=True — with confirm
#       False it returns a dry-run preview/plan and sends nothing.
#   A human still reviews every message before confirm=True. Keep it that way.

@audited_tool()
def send_batch(drafts: list[dict], confirm: bool = False) -> dict:
    """⚠️ ACTUALLY SENDS a BATCH of reviewed cold emails in ONE call (résumé
    auto-attached to each) and logs every send. One call = one confirmation for
    the whole batch, so a single human approval covers all recipients.

    This does NOT draft — each item is an already-reviewed draft:
        {"to": str, "name": str, "subject": str, "body": str,
         "company"?: str, "first"?: str, "last"?: str}

    Guard rail: it will NOT send unless confirm=True. With confirm=False
    (default) it returns {"sent": false, "preview": [...]} listing every
    recipient/subject and sends nothing, so a human reviews the whole batch
    first. Only pass confirm=True after that review. Each send is logged so
    check_bounces / retry_bounced_emails can track and re-send bounces.

    Returns {sent, count, results:[{to, name, status}], (errors)}.
    """
    if not isinstance(drafts, list) or not drafts:
        return {"sent": False, "error": "drafts must be a non-empty list"}

    # Normalize + validate every item up front so a bad row can't half-send.
    items, preview, bad = [], [], []
    for i, d in enumerate(drafts):
        to = (str(d.get("to", "")) or "").strip()
        name = str(d.get("name", "") or "")
        subject = str(d.get("subject", "") or "")
        body = str(d.get("body", "") or "")
        company = str(d.get("company", "") or "")
        first = str(d.get("first", "") or "")
        last = str(d.get("last", "") or "")
        if not first and not last and name:           # derive names for the log
            parts = name.split()
            first = parts[0] if parts else ""
            last = parts[-1] if len(parts) > 1 else ""
        if "@" not in to or not subject or not body:
            bad.append({"index": i, "to": to,
                        "why": "missing/invalid to, subject, or body"})
            continue
        items.append({"to": to, "name": name, "subject": subject, "body": body,
                      "company": company, "first": first, "last": last,
                      "domain": to.split("@")[-1]})
        preview.append({"to": to, "name": name, "company": company,
                        "subject": subject})

    if not confirm:
        return {"sent": False, "requires": "confirm=True",
                "count": len(items),
                "note": "DRY RUN — nothing sent. Review this whole batch, then "
                        "call again with confirm=True to send ALL of them.",
                "preview": preview,
                "invalid": bad}

    results = []
    for it in items:
        ok = send_email(it["to"], it["name"] or "there", it["subject"], it["body"])
        record_send(it["first"], it["last"], it["domain"], it["name"],
                    it["company"], it["subject"], it["body"], it["to"],
                    status="sent" if ok else "send_error")
        results.append({"to": it["to"], "name": it["name"],
                        "status": "sent" if ok else "send_error"})

    sent_ok = sum(1 for r in results if r["status"] == "sent")
    out = {"sent": True, "count": len(results), "sent_ok": sent_ok,
           "results": results,
           "note": "Logged. Check bounces later with check_bounces; "
                   "re-send with retry_bounced_emails if any bounce."}
    if bad:
        out["skipped_invalid"] = bad
    return out


@audited_tool()
def check_bounces(since_days: int = 3) -> dict:
    """Scan your inbox for hard-bounce notices and report which of your sent
    outreach addresses bounced, cross-referenced with the send log (name +
    company). READ-ONLY — reads mail over IMAP, resends nothing. Requires
    GMAIL_APP_PASSWORD to be configured.
    """
    import os, sqlite3
    try:
        bounced = fetch_bounced(since_days)
    except Exception as e:
        return {"error": str(e), "bounced": [], "matched": [],
                "note": "IMAP read failed — is GMAIL_APP_PASSWORD set?"}

    matched = []
    if bounced and os.path.exists(STATE_DB):
        conn = sqlite3.connect(STATE_DB)
        conn.row_factory = sqlite3.Row
        try:
            for r in conn.execute("SELECT name, company, current_email, status, "
                                  "attempts FROM outreach_sends"):
                if (r["current_email"] or "").lower() in bounced:
                    matched.append({"name": r["name"], "company": r["company"],
                                    "email": r["current_email"],
                                    "status": r["status"], "attempts": r["attempts"]})
        finally:
            conn.close()
    return {"bounced": sorted(bounced), "count": len(bounced), "matched": matched,
            "note": "Read-only. Use retry_bounced_emails(confirm=True) to re-send "
                    "these with the next address pattern."}


@audited_tool()
def retry_bounced_emails(confirm: bool = False, max_retries: int = 3,
                         since_days: int = 3) -> dict:
    """Re-send bounced cold emails using the NEXT untried address pattern
    (first.last → flast → first@ → …). This IS the retry-on-rebounce mechanism:
    run it again after a re-bounce and it advances to the next pattern; when the
    patterns or max_retries are exhausted it marks the recruiter 'exhausted' and
    stops. Guard rail: with confirm=False (default) it returns the PLAN only and
    sends nothing; set confirm=True to actually resend.
    """
    try:
        actions = retry_bounced(max_retries=max_retries, dry_run=not confirm,
                                since_days=since_days)
    except Exception as e:
        return {"error": str(e), "actions": [],
                "note": "IMAP read failed — is GMAIL_APP_PASSWORD set?"}
    return {"resent": bool(confirm), "count": len(actions), "actions": actions,
            "note": ("PLAN ONLY (dry run) — call with confirm=True to send"
                     if not confirm else "resent with next patterns")}


# ── Reply tracking: close the loop on responses ──────────────
@audited_tool()
def check_replies(since_days: int = 14) -> dict:
    """Scan your inbox for HUMAN replies to your cold emails and record each one's
    outcome. Answers "who replied, and are they interested?".

    Reads mail over IMAP (needs GMAIL_APP_PASSWORD), matches inbound mail to the
    send log by from-address (or a reply to one of your subjects), skips bounces
    and out-of-office auto-replies, and classifies each real reply as
    interview | rejected | replied. It UPDATES reply_status in the send log so
    sent_outreach / outreach_stats reflect it. Does NOT send anything.

    Returns {count, replies: [{name, company, from, reply_status, subject,
    date, snippet}]}, newest first.
    """
    try:
        results = reply_tracker.scan_replies(since_days=since_days, apply=True)
    except Exception as e:
        return {"error": str(e), "replies": [],
                "note": "IMAP read failed — is GMAIL_APP_PASSWORD set?"}
    return {"count": len(results), "replies": results,
            "note": "reply_status updated in the send log for each match."}


@audited_tool()
def needs_followup(days: int = 6) -> list[dict]:
    """List delivered cold emails that have had NO reply and are at least `days`
    old (excludes bounced/exhausted). These are your follow-up candidates — a
    one-line nudge here tends to lift response rates. Read-only; sends nothing.
    Returns [{name, company, email, subject, sent_at, days_ago}], oldest first.
    """
    return reply_tracker.needs_followup(days=days)


@audited_tool()
def outreach_stats() -> dict:
    """The outreach response funnel — overall and per company: sent, bounced,
    replied, interview, rejected, no_reply, and response_rate %. Run check_replies
    first so the reply columns are populated. Read-only.
    """
    return reply_tracker.stats()


# ── Application tracking: log roles you applied to ───────────
@audited_tool()
def record_application(company: str, role: str = "", link: str = "",
                       location: str = "", jd_text: str = "", note: str = "",
                       applied_date: str = "") -> dict:
    """Log a job application to the applications DB (data/applications.db),
    storing the FULL job description in jd_text.

    Use this when the user pastes a JD they applied to / are pursuing — it records
    the role so it's tracked alongside the cold-email outreach. This does NOT draft
    or send anything (the outreach flow is separate and unchanged). applied_date
    defaults to today; application_week is derived. Returns the inserted row.
    """
    return applications.add_application(
        company=company, role=role, link=link, location=location,
        jd_text=jd_text, note=note, applied_date=applied_date)


@audited_tool()
def list_applications(company: str = "", outcome: str = "", stage: str = "",
                      limit: int = 100) -> list[dict]:
    """List tracked job applications (data/applications.db), newest first. Filter
    by company substring, outcome (active|rejected|closed), or furthest_stage
    (applied|cold_email|phone_screen|oa|round1|round2). Read-only.
    """
    return applications.list_applications(company=company, outcome=outcome,
                                          stage=stage, limit=limit)


@audited_tool()
def application_stats() -> dict:
    """The application funnel from the applications DB: total, how many reached
    each stage (cold_email → phone_screen → oa → round1 → round2), and outcome
    counts (active/rejected/closed). Read-only.
    """
    return applications.stats()


@audited_tool()
def refresh_applications_sheet() -> dict:
    """Push the applications DB to its Google Sheet tab ('Job Applications'),
    sorted newest-created first. record_application already auto-syncs on every
    new application; use this to force a refresh after status/stage edits. No-op
    if Sheets isn't configured. Returns {written, url} or {skipped, reason}.
    """
    return applications.export_to_sheet()


# ── Observability: read the agent's own action log ───────────
@audited_tool()
def recent_actions(tool: str = "", ok_only: bool = False,
                   limit: int = 50) -> list[dict]:
    """Read the agent's own action log — every MCP tool call (reads, drafts,
    sends, dry-runs), newest first. Answers "what did my agent do?" / "did
    anything error?". Filter by a tool-name substring and/or ok_only. Returns
    [{ts, tool, ok, duration_ms, args, result, error}], or [] if empty.
    """
    return read_actions(tool=tool, ok_only=ok_only, limit=limit)


if __name__ == "__main__":
    mcp.run()  # stdio transport
