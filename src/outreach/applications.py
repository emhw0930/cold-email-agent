# ============================================================
#  applications.py
#  Track job applications — the database version of the Google Sheets tracker.
#  Distinct from cold-email sends (outreach_state.db): this is "roles I applied
#  to and how far each got", one row per application.
#
#  Private, gitignored (data/applications.db) — personal job-search data.
#
#  Public interface:
#    import_csv(path, replace=True) -> int      # load the exported Sheets CSV
#    list_applications(company, outcome, stage, limit) -> list[dict]
#    stats() -> dict                            # funnel: applied → … → rounds
# ============================================================

from __future__ import annotations

import csv
import datetime as dt
import re
import sqlite3
from pathlib import Path

from src.core import config

DB_PATH = str(Path(config.PROJECT_ROOT) / "data" / "applications.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  company          TEXT NOT NULL,
  role             TEXT,
  location         TEXT,
  link             TEXT,
  applied_date     TEXT,          -- ISO YYYY-MM-DD
  application_week TEXT,          -- e.g. 2026-W28
  note             TEXT,
  cold_email       TEXT,          -- raw stage markers, preserved verbatim
  phone_screen     TEXT,
  oa               TEXT,
  round1           TEXT,
  round2           TEXT,
  furthest_stage   TEXT,          -- applied|cold_email|phone_screen|oa|round1|round2
  outcome          TEXT,          -- active|rejected|closed
  jd_text          TEXT,          -- full job description (when logged from a JD)
  created_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_app_company ON applications(company);
CREATE INDEX IF NOT EXISTS idx_app_outcome ON applications(outcome);
"""

# progression order for furthest_stage
_STAGE_ORDER = ["applied", "cold_email", "phone_screen", "oa", "round1", "round2"]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    # migrate older DBs that predate a column (idempotent)
    cols = {r[1] for r in c.execute("PRAGMA table_info(applications)")}
    if "jd_text" not in cols:
        c.execute("ALTER TABLE applications ADD COLUMN jd_text TEXT")
        c.commit()
    return c


def _iso_week(iso_date: str) -> str:
    try:
        y, w, _ = dt.date.fromisoformat(iso_date).isocalendar()
        return f"{y}-W{w:02d}"
    except Exception:
        return ""


def _norm_date(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s or None


def _derive(cold: str, phone: str, oa: str, r1: str, r2: str) -> tuple[str, str]:
    """Furthest stage reached + outcome, from the sparse stage markers."""
    reached = {"applied": True, "cold_email": bool(cold), "phone_screen": bool(phone),
               "oa": bool(oa), "round1": bool(r1), "round2": bool(r2)}
    furthest = "applied"
    for st in _STAGE_ORDER:
        if reached[st]:
            furthest = st
    blob = " ".join([cold, phone, oa, r1, r2]).lower()
    outcome = "rejected" if "reject" in blob else ("closed" if "closed" in blob else "active")
    return furthest, outcome


def import_csv(path: str, replace: bool = True) -> int:
    """Load an exported Google-Sheets tracker CSV. Auto-finds the header row
    (the one containing 'Company'), maps by column name, normalizes dates, and
    derives furthest_stage/outcome. replace=True clears the table first (a clean
    re-import); replace=False appends. Returns rows imported."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    hidx = next((i for i, r in enumerate(rows) if "Company" in [c.strip() for c in r]), 0)
    idx = {h.strip(): j for j, h in enumerate(rows[hidx])}

    def g(r: list, name: str) -> str:
        j = idx.get(name)
        return r[j].strip() if (j is not None and len(r) > j) else ""

    c = _conn()
    if replace:
        c.execute("DELETE FROM applications")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    n = 0
    for r in rows[hidx + 1:]:
        company = g(r, "Company")
        if not company:
            continue
        cold, phone, oa = g(r, "Cold Email"), g(r, "Phone Screen"), g(r, "OA")
        r1, r2 = g(r, "1st round"), g(r, "2nd round")
        furthest, outcome = _derive(cold, phone, oa, r1, r2)
        c.execute(
            "INSERT INTO applications (company, role, location, link, applied_date, "
            "application_week, note, cold_email, phone_screen, oa, round1, round2, "
            "furthest_stage, outcome, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (company, g(r, "Role"), g(r, "Location"), g(r, "Link"),
             _norm_date(g(r, "Date")), g(r, "Application Week"), g(r, "Note"),
             cold, phone, oa, r1, r2, furthest, outcome, now))
        n += 1
    c.commit()
    c.close()
    _auto_sync()                                    # mirror to Google Sheet
    return n


def add_application(company: str, role: str = "", location: str = "", link: str = "",
                    note: str = "", jd_text: str = "", applied_date: str = "",
                    cold_email: str = "", phone_screen: str = "", oa: str = "",
                    round1: str = "", round2: str = "") -> dict:
    """Log ONE new application. `applied_date` defaults to today (accepts
    M/D/YYYY or ISO); application_week is derived. Stores the full `jd_text` when
    given. Returns the inserted row {id, company, role, applied_date, ...}."""
    if not (company or "").strip():
        return {"error": "company is required"}
    date_iso = _norm_date(applied_date) or dt.date.today().isoformat()
    furthest, outcome = _derive(cold_email, phone_screen, oa, round1, round2)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    c = _conn()
    cur = c.execute(
        "INSERT INTO applications (company, role, location, link, applied_date, "
        "application_week, note, cold_email, phone_screen, oa, round1, round2, "
        "furthest_stage, outcome, jd_text, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (company.strip(), role.strip(), location.strip(), link.strip(), date_iso,
         _iso_week(date_iso), note.strip(), cold_email, phone_screen, oa, round1,
         round2, furthest, outcome, jd_text, now))
    row_id = cur.lastrowid
    c.commit()
    c.close()
    _auto_sync()                                    # mirror to Google Sheet
    return {"id": row_id, "company": company.strip(), "role": role.strip(),
            "applied_date": date_iso, "application_week": _iso_week(date_iso),
            "furthest_stage": furthest, "outcome": outcome,
            "jd_saved": bool(jd_text.strip())}


def _norm_company(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def sync_cold_email_from_outreach() -> dict:
    """Reconcile applications.cold_email with the actual cold-email send log
    (outreach_state.db): for every application whose company matches a company we
    emailed, mark cold_email = 'sent (<n> contacts)' and recompute furthest_stage/
    outcome. Matches on normalized company name. Returns a summary including
    outreach companies that had NO matching application (likely name mismatches)."""
    import os
    import sqlite3 as _sql
    from src.outreach.bounce_retry import STATE_DB
    if not os.path.exists(STATE_DB):
        return {"updated": 0, "note": "no outreach_state.db found"}

    oc = _sql.connect(STATE_DB)
    counts: dict[str, dict] = {}
    for (comp,) in oc.execute("SELECT company FROM outreach_sends"):
        k = _norm_company(comp)
        if not k:
            continue
        d = counts.setdefault(k, {"display": comp, "n": 0})
        d["n"] += 1
    oc.close()

    c = _conn()
    rows = c.execute("SELECT id, company, cold_email, phone_screen, oa, round1, "
                     "round2 FROM applications").fetchall()
    updated, matched = 0, set()
    for r in rows:
        k = _norm_company(r["company"])
        info = counts.get(k)
        if not info:
            continue
        matched.add(k)
        marker = f"sent ({info['n']} contacts)"
        if (r["cold_email"] or "").strip().lower().startswith("sent"):
            continue                                   # already synced
        furthest, outcome = _derive(marker, r["phone_screen"], r["oa"],
                                    r["round1"], r["round2"])
        c.execute("UPDATE applications SET cold_email=?, furthest_stage=?, outcome=? "
                  "WHERE id=?", (marker, furthest, outcome, r["id"]))
        updated += 1
    c.commit()
    c.close()
    _auto_sync()                                    # mirror to Google Sheet

    unmatched = sorted(counts[k]["display"] for k in counts if k not in matched)
    return {"updated": updated,
            "matched_companies": sorted(counts[k]["display"] for k in matched),
            "unmatched_outreach_companies": unmatched,
            "note": "unmatched = you cold-emailed them but no application row "
                    "matched by name (often a spelling difference)."}


def list_applications(company: str = "", outcome: str = "", stage: str = "",
                      limit: int = 100) -> list[dict]:
    """Recent applications, newest first, filtered by company substring / outcome
    / furthest_stage."""
    import os
    if not os.path.exists(DB_PATH):
        return []
    c = _conn()
    rows = c.execute("SELECT company, role, location, applied_date, application_week, "
                     "furthest_stage, outcome, note FROM applications "
                     "ORDER BY applied_date DESC, id DESC").fetchall()
    c.close()
    comp, out_f, st = company.lower().strip(), outcome.lower().strip(), stage.lower().strip()
    res = []
    for r in rows:
        if comp and comp not in (r["company"] or "").lower():
            continue
        if out_f and out_f != (r["outcome"] or "").lower():
            continue
        if st and st != (r["furthest_stage"] or "").lower():
            continue
        res.append(dict(r))
        if len(res) >= limit:
            break
    return res


def stats() -> dict:
    """Application funnel + outcome/stage breakdowns."""
    import os
    if not os.path.exists(DB_PATH):
        return {"total": 0}
    c = _conn()
    total = c.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    by_stage = dict(c.execute("SELECT furthest_stage, COUNT(*) FROM applications "
                              "GROUP BY furthest_stage").fetchall())
    by_outcome = dict(c.execute("SELECT outcome, COUNT(*) FROM applications "
                                "GROUP BY outcome").fetchall())
    reached = {}
    for st in ("cold_email", "phone_screen", "oa", "round1", "round2"):
        reached[st] = c.execute(
            f"SELECT COUNT(*) FROM applications WHERE {st} != '' AND {st} IS NOT NULL"
        ).fetchone()[0]
    c.close()
    return {"total": total, "reached": reached,
            "by_furthest_stage": by_stage, "by_outcome": by_outcome}


# ── Google Sheets mirror (auto-refresh) ──────────────────────
SHEET_TAB = "Job Applications"
_SHEET_HEADERS = ["Company", "Role", "Location", "Applied", "Week", "Furthest Stage",
                  "Outcome", "Cold Email", "Phone Screen", "OA", "1st Round",
                  "2nd Round", "Note"]
_SHEET_COLS = ("company", "role", "location", "applied_date", "application_week",
               "furthest_stage", "outcome", "cold_email", "phone_screen", "oa",
               "round1", "round2", "note")


def export_to_sheet(tab: str = SHEET_TAB) -> dict:
    """Mirror all applications to a tab in the configured Google Sheet, sorted
    NEWEST-CREATED first (created_at desc, then applied_date desc). No-op if
    Sheets isn't configured. Returns {written, url} or {skipped, reason}."""
    try:
        from src.outreach.sheets_logger import sheets_available
        if not sheets_available():
            return {"skipped": True, "reason": "Sheets not configured"}
        import gspread
        from google.oauth2.service_account import Credentials
    except Exception as e:
        return {"skipped": True, "reason": f"sheets unavailable: {e}"}

    creds = Credentials.from_service_account_file(
        config.SHEETS_SERVICE_ACCOUNT_PATH,
        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(config.SHEETS_SPREADSHEET_ID)
    try:
        ws = ss.worksheet(tab)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=tab, rows=500, cols=len(_SHEET_HEADERS))

    c = _conn()
    rows = c.execute(
        f"SELECT {', '.join(_SHEET_COLS)} FROM applications "
        "ORDER BY created_at DESC, applied_date DESC").fetchall()
    c.close()
    data = [_SHEET_HEADERS] + [[r[k] or "" for k in _SHEET_COLS] for r in rows]
    ws.update(data, value_input_option="RAW")
    ws.format(f"A1:{chr(64 + len(_SHEET_HEADERS))}1", {"textFormat": {"bold": True}})
    return {"written": len(rows), "url": ss.url + f"#gid={ws.id}"}


def _auto_sync():
    """Best-effort mirror to the sheet; never let a Sheets error break a write."""
    try:
        export_to_sheet()
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Job applications DB")
    ap.add_argument("--import", dest="imp", metavar="CSV", help="import a Sheets CSV export")
    ap.add_argument("--append", action="store_true", help="with --import, append instead of replace")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.imp:
        n = import_csv(a.imp, replace=not a.append)
        print(f"Imported {n} applications into {DB_PATH}")
    if a.stats:
        import json
        print(json.dumps(stats(), indent=2))
