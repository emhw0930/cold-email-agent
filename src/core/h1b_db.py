# ============================================================
#  h1b_db.py
#  Load the USCIS H-1B Employer Data Hub CSV into a local
#  SQLite database and rank the top sponsors.
#
#  The USCIS export is UTF-16, TAB-separated, one row per
#  employer per fiscal year, with approval/denial counts split
#  by petition type. For job-seeking, "New Employment Approval"
#  is the key column — it counts fresh (often cap-subject) hires,
#  not renewals of existing employees.
#
#  Public interface:
#    load_csv(csv_path, db_path=DB_PATH) -> int          # rows loaded
#    top_sponsors(n=500, db_path=DB_PATH) -> list[dict]  # ranked
#    connect(db_path=DB_PATH) -> sqlite3.Connection
# ============================================================

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

# Locate the repo root by walking up to the dir holding requirements.txt, so the
# DB path is correct regardless of this module's depth. Kept self-contained (no
# config import) so loading the DB layer never requires the app's secrets.
def _find_root(start: Path) -> Path:
    for p in (start, *start.parents):
        if (p / "requirements.txt").exists():
            return p
    return start.parents[-1]


DB_PATH = str(_find_root(Path(__file__).resolve()) / "data" / "h1b_employers.db")

# USCIS column header -> our snake_case column
_COLUMNS = {
    "Fiscal Year": "fiscal_year",
    "Employer (Petitioner) Name": "employer",
    "Tax ID": "tax_id",
    "Industry (NAICS) Code": "naics",
    "Petitioner City": "city",
    "Petitioner State": "state",
    "Petitioner Zip Code": "zip",
    "New Employment Approval": "new_approval",
    "New Employment Denial": "new_denial",
    "Continuation Approval": "cont_approval",
    "Continuation Denial": "cont_denial",
    "Change with Same Employer Approval": "same_change_approval",
    "Change with Same Employer Denial": "same_change_denial",
    "New Concurrent Approval": "concurrent_approval",
    "New Concurrent Denial": "concurrent_denial",
    "Change of Employer Approval": "change_employer_approval",
    "Change of Employer Denial": "change_employer_denial",
    "Amended Approval": "amended_approval",
    "Amended Denial": "amended_denial",
}

_INT_COLS = [c for c in _COLUMNS.values()
             if c.endswith(("approval", "denial")) or c == "fiscal_year"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS employers (
    id INTEGER PRIMARY KEY,
    fiscal_year INTEGER,
    employer TEXT,
    tax_id TEXT,
    naics TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    new_approval INTEGER DEFAULT 0,
    new_denial INTEGER DEFAULT 0,
    cont_approval INTEGER DEFAULT 0,
    cont_denial INTEGER DEFAULT 0,
    same_change_approval INTEGER DEFAULT 0,
    same_change_denial INTEGER DEFAULT 0,
    concurrent_approval INTEGER DEFAULT 0,
    concurrent_denial INTEGER DEFAULT 0,
    change_employer_approval INTEGER DEFAULT 0,
    change_employer_denial INTEGER DEFAULT 0,
    amended_approval INTEGER DEFAULT 0,
    amended_denial INTEGER DEFAULT 0,
    total_approvals INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_new_approval ON employers(new_approval DESC);
CREATE INDEX IF NOT EXISTS idx_total ON employers(total_approvals DESC);
CREATE INDEX IF NOT EXISTS idx_employer ON employers(employer);

-- Cache of resolved Greenhouse boards (filled by greenhouse token resolver)
CREATE TABLE IF NOT EXISTS greenhouse_boards (
    employer TEXT PRIMARY KEY,
    board_token TEXT,
    valid INTEGER DEFAULT 0,       -- 1 = board exists and returned jobs
    checked_at TEXT
);
"""

_APPROVAL_COLS = [c for c in _COLUMNS.values() if c.endswith("approval")]


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _to_int(v: str) -> int:
    try:
        return int((v or "0").strip() or 0)
    except ValueError:
        return 0


def load_csv(csv_path: str, db_path: str = DB_PATH) -> int:
    """Load the USCIS CSV (UTF-16, tab-separated) into SQLite. Returns rows loaded."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    conn.executescript(_SCHEMA)
    conn.execute("DELETE FROM employers")  # full refresh

    with open(csv_path, encoding="utf-16") as f:
        reader = csv.DictReader(f, delimiter="\t")
        # normalize header whitespace
        reader.fieldnames = [(h or "").strip() for h in reader.fieldnames]

        cols = list(_COLUMNS.values()) + ["total_approvals"]
        placeholders = ",".join("?" for _ in cols)
        sql = f"INSERT INTO employers ({','.join(cols)}) VALUES ({placeholders})"

        rows = []
        for raw in reader:
            rec = {}
            for header, col in _COLUMNS.items():
                val = (raw.get(header) or "").strip()
                rec[col] = _to_int(val) if col in _INT_COLS else val
            if not rec["employer"]:
                continue  # skip blank/aggregate rows
            rec["total_approvals"] = sum(rec[c] for c in _APPROVAL_COLS)
            rows.append(tuple(rec[c] for c in cols))

        conn.executemany(sql, rows)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM employers").fetchone()[0]
    conn.close()
    return n


def top_sponsors(n: int = 500, by: str = "new_approval",
                 db_path: str = DB_PATH) -> list[dict]:
    """Top N employers ranked by `by` (new_approval | total_approvals)."""
    assert by in ("new_approval", "total_approvals")
    conn = connect(db_path)
    rows = conn.execute(
        f"""SELECT employer, city, state, naics, new_approval, total_approvals
            FROM employers
            WHERE {by} > 0
            ORDER BY {by} DESC
            LIMIT ?""", (n,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── CLI ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Load USCIS H-1B CSV into SQLite")
    ap.add_argument("--csv", required=True, help="path to USCIS Employer Information.csv")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    count = load_csv(args.csv, args.db)
    print(f"✅ Loaded {count:,} employers into {args.db}\n")
    print(f"Top {args.top} by NEW employment approvals:")
    for i, e in enumerate(top_sponsors(args.top, "new_approval", args.db), 1):
        print(f"{i:>3}. {e['new_approval']:>6}  {e['employer']}  ({e['city']}, {e['state']})")
