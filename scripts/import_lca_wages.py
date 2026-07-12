#!/usr/bin/env python3
# ============================================================
#  import_lca_wages.py  (run occasionally, not part of the daily flow)
#  Loads certified H-1B wages from one or more DOL OFLC "LCA Disclosure
#  Data" spreadsheets into the employer_wages table, then anyone can run
#  `python src/company_lookup.py` to rebuild docs/employers.json.
#
#  Get the .xlsx files (one per fiscal year) from:
#    https://www.dol.gov/agencies/eta/foreign-labor/performance
#  e.g. LCA_Disclosure_Data_FY2024_Q4.xlsx , ..._FY2025_Q4.xlsx
#
#  Usage:
#    python scripts/import_lca_wages.py path/to/FY2024.xlsx path/to/FY2025.xlsx
#
#  Notes:
#    - Column POSITIONS differ between fiscal years, so columns are resolved
#      by header NAME per file.
#    - Wages are annualized (Hour×2080, Week×52, Bi-Weekly×26, Month×12) and
#      obvious errors (<$20k or >$1M) are dropped.
#    - Certified and "Certified - Withdrawn" cases are both counted (the
#      withdrawal is usually administrative; the offered wage is still valid).
# ============================================================
import sys, statistics, sqlite3, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import openpyxl
import h1b_db
from company_lookup import norm   # single source of truth for name normalization

MULT = {"Year": 1, "Hour": 2080, "Week": 52, "Bi-Weekly": 26, "Month": 12, "Day": 260}
NEEDED = ("CASE_STATUS", "EMPLOYER_NAME", "EMPLOYER_STATE",
          "WAGE_RATE_OF_PAY_FROM", "WAGE_UNIT_OF_PAY")


def main(paths: list[str]) -> None:
    if not paths:
        sys.exit("usage: import_lca_wages.py <lca_file.xlsx> [more.xlsx ...]")
    t0 = time.time()
    wages: dict[str, list[float]] = {}
    disp: dict[str, tuple[str, str]] = {}
    kept = 0
    for path in paths:
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb.active
        it = ws.iter_rows(min_row=1, values_only=True)
        header = [str(h) if h else "" for h in next(it)]
        col = {name: header.index(name) for name in NEEDED}
        cS, cE, cSt, cW, cU = (col["CASE_STATUS"], col["EMPLOYER_NAME"],
                               col["EMPLOYER_STATE"], col["WAGE_RATE_OF_PAY_FROM"],
                               col["WAGE_UNIT_OF_PAY"])
        for row in it:
            if (row[cS] or "") not in ("Certified", "Certified - Withdrawn"):
                continue
            unit, w = row[cU], row[cW]
            if w is None or unit not in MULT:
                continue
            try:
                annual = float(w) * MULT[unit]
            except (TypeError, ValueError):
                continue
            if annual < 20000 or annual > 1_000_000:
                continue
            k = norm(row[cE])
            if not k:
                continue
            wages.setdefault(k, []).append(annual)
            disp.setdefault(k, (str(row[cE]).strip(), row[cSt] or ""))
            kept += 1
        wb.close()
        print(f"  {Path(path).name}: running total {kept} records, "
              f"{len(wages)} employers ({time.time()-t0:.0f}s)")

    conn = h1b_db.connect(h1b_db.DB_PATH)
    conn.execute("DROP TABLE IF EXISTS employer_wages")
    conn.execute("""CREATE TABLE employer_wages (
        employer_norm TEXT PRIMARY KEY, sample_name TEXT, state TEXT,
        n_lca INTEGER, wage_median INTEGER, wage_p25 INTEGER, wage_p75 INTEGER)""")
    rows = []
    for k, lst in wages.items():
        lst.sort()
        rows.append((k, disp[k][0], disp[k][1], len(lst),
                     int(statistics.median(lst)),
                     int(lst[len(lst) // 4]), int(lst[(len(lst) * 3) // 4])))
    conn.executemany("INSERT INTO employer_wages VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    print(f"Stored {len(rows)} employer wage rows in {time.time()-t0:.0f}s. "
          f"Now run: python src/company_lookup.py")


if __name__ == "__main__":
    main(sys.argv[1:])
