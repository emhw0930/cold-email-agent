# ============================================================
#  company_lookup.py
#  Builds docs/employers.json — the dataset behind the website's
#  "company lookup" search box. Joins two things by a normalized
#  employer name:
#    • H-1B case counts   — from the `employers` table (USCIS Hub)
#    • Certified H-1B wages — from the `employer_wages` table (DOL LCA,
#      loaded once by scripts/import_lca_wages.py)
#
#  Records are emitted as compact arrays to keep the file small:
#    [name, state, total_approvals, new_approvals, wage_median,
#     wage_p25, wage_p75, n_lca]
#  Missing values are 0. The client fuzzy-matches on `name`.
# ============================================================

from __future__ import annotations

import json
import re
from pathlib import Path

from src.core import config
from src.core import h1b_db

_SUFFIX = re.compile(r"\b(INC|LLC|LLP|LP|LTD|CORP|CORPORATION|CO|COMPANY|PC|PLLC|"
                     r"THE|USA|US|NA|NORTH AMERICA)\b")


def norm(name: str) -> str:
    """Normalize an employer name for cross-dataset matching: uppercase, drop
    punctuation, strip common corporate suffixes, collapse whitespace. MUST match
    the normalization used when the wage table was built."""
    s = re.sub(r"[^A-Z0-9& ]", " ", (name or "").upper())
    s = _SUFFIX.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _titlecase(name: str) -> str:
    """USCIS names are ALL CAPS; make them presentable, preserving short acronyms."""
    return " ".join(w if (len(w) <= 3 and w.isupper()) else w.capitalize()
                    for w in name.split())


def build(db_path: str | None = None, out_path: str | None = None) -> dict:
    db_path = db_path or h1b_db.DB_PATH
    out_path = out_path or str(Path(config.PROJECT_ROOT) / "docs" / "employers.json")
    conn = h1b_db.connect(db_path)

    # 1) aggregate USCIS case counts per normalized employer
    cases: dict[str, dict] = {}
    for r in conn.execute("SELECT employer, state, new_approval, total_approvals, "
                          "new_denial FROM employers"):
        k = norm(r["employer"])
        if not k:
            continue
        c = cases.setdefault(k, {"name": _titlecase(r["employer"]),
                                 "state": r["state"] or "", "new": 0, "tot": 0, "den": 0})
        c["new"] += r["new_approval"] or 0
        c["tot"] += r["total_approvals"] or 0
        c["den"] += r["new_denial"] or 0

    # 2) wages per normalized employer (may be empty if DOL data not imported)
    wages: dict[str, dict] = {}
    try:
        for r in conn.execute("SELECT employer_norm, sample_name, state, n_lca, "
                              "wage_median, wage_p25, wage_p75 FROM employer_wages"):
            wages[r["employer_norm"]] = dict(r)
    except Exception:
        pass  # table absent → cases-only build
    conn.close()

    # 3) union → compact records
    both = 0
    rows = []
    for k in set(cases) | set(wages):
        c = cases.get(k)
        w = wages.get(k)
        if c and w:
            both += 1
        name = (w["sample_name"] if w else None) or (c["name"] if c else k)
        state = (c["state"] if c else "") or (w["state"] if w else "")
        rows.append([
            name, state,
            c["tot"] if c else 0, c["new"] if c else 0,
            w["wage_median"] if w else 0, w["wage_p25"] if w else 0,
            w["wage_p75"] if w else 0, w["n_lca"] if w else 0,
        ])
    # sort by name for stable diffs
    rows.sort(key=lambda r: r[0].lower())

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"fields": ["name", "state", "total_approvals", "new_approvals",
                              "wage_median", "wage_p25", "wage_p75", "n_lca"],
                   "rows": rows}, f, separators=(",", ":"))

    stats = {"total": len(rows), "with_cases": len(cases), "with_wages": len(wages),
             "with_both": both, "bytes": Path(out_path).stat().st_size}
    return stats


if __name__ == "__main__":
    s = build()
    print(f"Wrote {s['total']} companies to docs/employers.json "
          f"({s['bytes']//1024} KB)")
    print(f"  with case data : {s['with_cases']}")
    print(f"  with wage data : {s['with_wages']}")
    print(f"  with both      : {s['with_both']}")
