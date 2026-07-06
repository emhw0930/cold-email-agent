# ============================================================
#  h1b_greenhouse.py
#  Bridge between the H-1B sponsor database and public ATS
#  job boards (Greenhouse, Lever, Ashby — via src/ats.py).
#
#  1. resolve_boards(): for the top-N sponsors, guess a board
#     slug from the employer name and probe each ATS; cache the
#     ones that exist (greenhouse_boards table: employer, ats, token).
#  2. valid_boards(): read back the cached, working boards.
#  3. daily_fresh_swe(): pull fresh explicit-junior SWE roles
#     across all cached boards, newest-first.
#
#  No ATS offers global name search, so slug-guess + probe is the
#  only mapping. Greenhouse is name-verified; Lever/Ashby are
#  verified by slug strength (len >= 4) since they expose no name.
# ============================================================

from __future__ import annotations

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor

import ats
import h1b_db

_STOP = {
    "inc", "incorporated", "corp", "corporation", "llc", "llp", "ltd",
    "limited", "co", "company", "us", "usa", "u", "s", "america", "americas",
    "american", "na", "north", "technology", "technologies", "solutions",
    "services", "service", "global", "group", "systems", "system", "software",
    "labs", "lab", "consulting", "the", "and", "of", "com", "platforms",
    "platform", "international", "worldwide", "holdings", "enterprise",
    "enterprises", "digital", "data", "cloud",
}

_SCHEMA_ATS = """
CREATE TABLE IF NOT EXISTS greenhouse_boards (
    employer TEXT PRIMARY KEY,
    board_token TEXT,
    ats TEXT,
    valid INTEGER DEFAULT 0,
    checked_at TEXT
);
"""


def _ensure_schema(conn):
    conn.executescript(_SCHEMA_ATS)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(greenhouse_boards)")}
    if "ats" not in cols:
        conn.execute("ALTER TABLE greenhouse_boards ADD COLUMN ats TEXT")
    conn.commit()


def candidate_tokens(employer: str) -> list[str]:
    """Guess plausible board slugs from a legal employer name (many variants)."""
    name = re.sub(r"[^a-z0-9 ]", " ", employer.lower())
    words = [w for w in name.split() if w and w not in _STOP]
    if not words:
        words = [w for w in re.sub(r"[^a-z0-9 ]", " ", employer.lower()).split() if w]
    cands = []
    if words:
        cands.append(words[0])                       # stripe
        if len(words) >= 2:
            cands.append(words[0] + words[1])        # torcrobotics
            cands.append(f"{words[0]}-{words[1]}")   # torc-robotics
            cands.append("".join(words))             # full concat
            cands.append("-".join(words))            # full hyphenated
            cands.append("".join(w[0] for w in words))  # acronym: ibm, gm
        # brand + common suffixes some boards use
        cands.append(words[0] + "hq")
        cands.append(words[0] + "careers")
    seen, out = set(), []
    for c in cands:
        if len(c) > 1 and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# Confirmed tech companies (also H-1B sponsors) on public boards, by ATS.
# Seeded directly so we don't depend on name-guessing them.
KNOWN_TECH_SLUGS = {
    "greenhouse": ["stripe", "databricks", "airbnb", "coinbase", "robinhood",
                   "discord", "brex", "samsara", "affirm", "instacart",
                   "dropbox", "figma", "benchling", "airtable", "asana",
                   "flexport", "nuro", "anduril", "sofi", "roblox", "pinterest",
                   "twitch", "snowflake", "datadog", "hashicorp", "verkada",
                   "waymo", "riotgames", "torcrobotics", "gustohq"],
    "lever": ["spotify", "veeva", "netflix", "plaid", "palantir"],
    "ashby": ["openai", "ramp", "notion", "linear", "vercel", "scale",
              "appliedintuition", "commure", "cursor", "mercor", "whatnot"],
}


# Confirmed Workday endpoints for big H-1B sponsors (tenant, pod, site).
# Workday has no name-search + unguessable site paths, so these are curated.
# Stored as a composite token "tenant|pod|site" (see ats._workday).
WORKDAY_ENDPOINTS = {
    "NVIDIA":       ("nvidia", "wd5", "NVIDIAExternalCareerSite"),
    "SALESFORCE":   ("salesforce", "wd12", "External_Career_Site"),
    "WORKDAY":      ("workday", "wd5", "Workday"),
    "COMCAST":      ("comcast", "wd5", "Comcast_Careers"),
    "MASTERCARD":   ("mastercard", "wd1", "CorporateCareers"),
    "HP":           ("hp", "wd5", "ExternalCareerSite"),
    "HPE":          ("hpe", "wd5", "Jobsathpe"),
    "PAYPAL":       ("paypal", "wd1", "jobs"),
    "BLACKROCK":    ("blackrock", "wd1", "BlackRock_Professional"),
    "TARGET":       ("target", "wd5", "targetcareers"),
    "CVS HEALTH":   ("cvshealth", "wd1", "CVS_Health_Careers"),
}


def resolve_workday(db_path: str = h1b_db.DB_PATH) -> int:
    """Probe & cache the curated Workday endpoints. Returns count added."""
    conn = h1b_db.connect(db_path)
    _ensure_schema(conn)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    added = 0
    for name, (tenant, pod, site) in WORKDAY_ENDPOINTS.items():
        token = f"{tenant}|{pod}|{site}"
        if ats.fetch("workday", token):
            conn.execute(
                """INSERT INTO greenhouse_boards (employer, board_token, ats, valid, checked_at)
                   VALUES (?,?,?,1,?)
                   ON CONFLICT(employer) DO UPDATE SET
                     board_token=excluded.board_token, ats=excluded.ats,
                     valid=1, checked_at=excluded.checked_at""",
                (name, token, "workday", now))
            added += 1
    conn.commit()
    conn.close()
    return added


def resolve_known(db_path: str = h1b_db.DB_PATH) -> int:
    """Probe & cache the curated known-good tech slugs. Returns count added."""
    conn = h1b_db.connect(db_path)
    _ensure_schema(conn)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    added = 0
    for prov, slugs in KNOWN_TECH_SLUGS.items():
        for slug in slugs:
            if ats.fetch(prov, slug):
                conn.execute(
                    """INSERT INTO greenhouse_boards (employer, board_token, ats, valid, checked_at)
                       VALUES (?,?,?,1,?)
                       ON CONFLICT(employer) DO UPDATE SET
                         board_token=excluded.board_token, ats=excluded.ats,
                         valid=1, checked_at=excluded.checked_at""",
                    (f"[known] {slug}", slug, prov, now))
                added += 1
    conn.commit()
    conn.close()
    return added


def _names_match(employer: str, board_name: str) -> bool:
    def toks(s: str) -> set[str]:
        return {w for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()
                if len(w) > 2 and w not in _STOP}
    return bool(toks(employer) & toks(board_name))


def _resolve_one(employer: str):
    """Return (employer, ats, token) for the first provider that verifies, else (employer, None, None)."""
    for tok in candidate_tokens(employer):
        # Greenhouse — verified by company name
        if ats.fetch("greenhouse", tok):
            name = ats.board_name("greenhouse", tok)
            if name and _names_match(employer, name):
                return employer, "greenhouse", tok
        # Lever / Ashby — no name endpoint; require a strong slug (len >= 4)
        if len(tok) >= 4:
            for prov in ("lever", "ashby"):
                if ats.fetch(prov, tok):
                    return employer, prov, tok
    return employer, None, None


def resolve_boards(n: int = 500, by: str = "new_approval",
                   db_path: str = h1b_db.DB_PATH) -> dict:
    """Find & cache working ATS boards for the top-N sponsors. Idempotent."""
    conn = h1b_db.connect(db_path)
    _ensure_schema(conn)
    # skip anything already probed (valid OR invalid) so we only check new ranks
    already = {r["employer"] for r in conn.execute(
        "SELECT employer FROM greenhouse_boards")}

    sponsors = h1b_db.top_sponsors(n, by, db_path)
    todo = [s for s in sponsors if s["employer"] not in already]
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    found = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for emp, prov, tok in ex.map(lambda s: _resolve_one(s["employer"]), todo):
            conn.execute(
                """INSERT INTO greenhouse_boards (employer, board_token, ats, valid, checked_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(employer) DO UPDATE SET
                     board_token=excluded.board_token, ats=excluded.ats,
                     valid=excluded.valid, checked_at=excluded.checked_at""",
                (emp, tok, prov, 1 if tok else 0, now))
            if tok:
                found.append((emp, prov, tok))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM greenhouse_boards WHERE valid = 1").fetchone()[0]
    conn.close()
    return {"checked": len(todo), "found": len(found), "total_valid": total, "boards": found}


def valid_boards(db_path: str = h1b_db.DB_PATH) -> list[tuple[str, str]]:
    """Cached working boards as (ats, token) pairs."""
    conn = h1b_db.connect(db_path)
    _ensure_schema(conn)
    rows = conn.execute(
        "SELECT ats, board_token FROM greenhouse_boards WHERE valid = 1 AND board_token IS NOT NULL").fetchall()
    conn.close()
    return [(r["ats"] or "greenhouse", r["board_token"]) for r in rows]


def valid_tokens(db_path: str = h1b_db.DB_PATH) -> list[str]:
    return [tok for _, tok in valid_boards(db_path)]


_SEEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    job_key TEXT PRIMARY KEY,
    first_seen TEXT,
    title TEXT, company TEXT, ats TEXT, url TEXT
);
"""


def _annotate_seen(jobs: list[dict], db_path: str) -> list[dict]:
    """Stamp each job with first_seen (the first date WE saw it) and is_new.

    This is the reliable 'new' signal — it works across all ATSs regardless of
    whether they expose created_at. First run seeds history (everything 'new');
    after that only genuinely new-to-you postings are flagged.
    """
    conn = h1b_db.connect(db_path)
    conn.executescript(_SEEN_SCHEMA)
    today = dt.date.today().isoformat()
    for j in jobs:
        key = f"{j['ats']}:{j['job_id']}"
        row = conn.execute("SELECT first_seen FROM seen_jobs WHERE job_key=?", (key,)).fetchone()
        if row:
            j["first_seen"] = row["first_seen"]
            j["is_new"] = False
        else:
            conn.execute(
                "INSERT INTO seen_jobs (job_key, first_seen, title, company, ats, url) VALUES (?,?,?,?,?,?)",
                (key, today, j["title"], j["company"], j["ats"], j["url"]))
            j["first_seen"] = today
            j["is_new"] = True
    conn.commit()
    conn.close()
    return jobs


def daily_fresh_swe(us_only: bool = True, limit: int | None = None,
                    new_only: bool = False, db_path: str = h1b_db.DB_PATH) -> list[dict]:
    """Explicit-junior SWE roles across all cached boards.

    Sorted by first-seen (newest-to-you first), then last-updated. Set
    new_only=True to return only postings first seen today.
    """
    boards = valid_boards(db_path)
    if not boards:
        raise RuntimeError("No resolved boards yet. Run: python src/h1b_greenhouse.py --resolve")

    def _one(board):
        prov, tok = board
        out = []
        for j in ats.fetch(prov, tok):
            if not ats.is_junior_swe(j["title"]):
                continue
            if us_only and not ats.is_us(j["location"]):
                continue
            j["new_grad"] = ats.is_explicit_junior(j["title"])  # tag explicit new-grad/I
            out.append(j)
        return out

    results = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for batch in ex.map(_one, boards):
            results.extend(batch)

    # de-dup identical postings across weak/duplicate slugs
    seen_keys, deduped = set(), []
    for j in results:
        k = f"{j['ats']}:{j['job_id']}"
        if k not in seen_keys:
            seen_keys.add(k)
            deduped.append(j)

    _annotate_seen(deduped, db_path)
    if new_only:
        deduped = [j for j in deduped if j["is_new"]]
    # explicit new-grad first, then newest-seen, then last-updated
    deduped.sort(key=lambda r: (bool(r.get("new_grad")), r["first_seen"], r["updated_at"]),
                 reverse=True)
    return deduped[:limit] if limit else deduped


# ── CLI ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolve", action="store_true")
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--jobs", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="re-probe everything (clear cache)")
    args = ap.parse_args()

    if args.fresh:
        c = h1b_db.connect(); c.execute("DELETE FROM greenhouse_boards"); c.commit(); c.close()
        print("cache cleared")

    if args.resolve:
        print(f"Probing Greenhouse/Lever/Ashby for top {args.n} sponsors...")
        r = resolve_boards(args.n)
        k = resolve_known()
        w = resolve_workday()
        print(f"\nChecked {r['checked']}, found {r['found']} from sponsors + {k} known tech + {w} Workday.")
        print(f"Total boards cached: {valid_boards() and len(valid_boards())}\n")
        for emp, prov, tok in sorted(r["boards"], key=lambda x: (x[1], x[2])):
            print(f"   {prov:<11} {tok:<20} <- {emp}")

    if args.list:
        b = valid_boards()
        for prov, tok in sorted(b):
            print(f"{prov:<11} {tok}")
        print(f"\n{len(b)} boards cached")

    if args.jobs:
        jobs = daily_fresh_swe()
        print(f"\nFresh explicit-junior SWE roles: {len(jobs)}\n")
        for j in jobs[:40]:
            print(f"{j['updated_at']}  [{j['ats']}:{j['company']}]  {j['title']} - {j['location']}")
            print(f"        {j['url']}")
