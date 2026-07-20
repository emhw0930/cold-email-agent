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
#    TIER 1 (this file) — read-only, safe to auto-run:
#        search_jobs · top_matches · company_h1b_lookup · retrieve_experience
#    TIER 2/3 (later) — draft / send: kept as SEPARATE tools so a client can
#        never draft-and-send in one step (draft returns text; send is its own
#        explicit call with the reviewed body).
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

mcp = FastMCP("h1b-job-agent")


# ── TIER 1: read-only tools ──────────────────────────────────
@mcp.tool()
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


@mcp.tool()
def top_matches(n: int = 10) -> list[dict]:
    """Return the top-N roles by résumé fit from the scored sponsor pool
    (drops any posting flagged 'no sponsorship'). Best fit first."""
    return search_jobs(keyword="", min_fit=0, new_only=False, limit=n)


@mcp.tool()
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

    exact, sub = [], []
    for r in emp_rows:
        n = norm(r["employer"])
        if n == key:
            exact.append(r)
        elif key and (key in n or n in key):
            sub.append(r)
    matched = exact or sub

    # wages: exact normalized key first, then a substring fallback
    wage = conn.execute(
        "SELECT sample_name, state, n_lca, wage_median, wage_p25, wage_p75 "
        "FROM employer_wages WHERE employer_norm = ?", (key,)).fetchone()
    if not wage:
        for r in conn.execute(
                "SELECT sample_name, state, n_lca, wage_median, wage_p25, wage_p75 "
                "FROM employer_wages WHERE employer_norm LIKE ?", (f"%{first}%",)):
            wn = r["employer_norm"] if "employer_norm" in r.keys() else ""
            if key in norm(r["sample_name"]) or norm(r["sample_name"]) in key:
                wage = r
                break
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

    result = {
        "query": name,
        "found": True,
        "matched_name": display,
        "match_quality": "exact" if exact else "partial",
        "is_h1b_sponsor": tot_appr > 0,
        "new_employment_approvals": new_appr,
        "total_approvals": tot_appr,
        "denials": denials,
        "states": states,
    }
    if wage:
        result["wage_median"] = wage["wage_median"]
        result["wage_p25"] = wage["wage_p25"]
        result["wage_p75"] = wage["wage_p75"]
        result["wage_sample_size"] = wage["n_lca"]
    else:
        result["wage_note"] = "no DOL LCA certified-wage data for this employer"
    return result


@mcp.tool()
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


if __name__ == "__main__":
    mcp.run()  # stdio transport
