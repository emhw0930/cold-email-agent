#!/usr/bin/env python3
# ============================================================
#  jobs_site.py
#  Generate a single self-contained static webpage listing all
#  current board roles (Workday + Greenhouse/Lever/Ashby) with
#  client-side search, source filters, new-grad toggle, and sort.
#
#  No server, no dependencies — the data is embedded as JSON and
#  the page runs on vanilla JS. Open the output file in a browser.
#
#  Usage:
#    python src/jobs_site.py                 # -> site/index.html
#    python src/jobs_site.py --out foo.html  # custom path
#    python src/jobs_site.py --open          # build + open in browser
# ============================================================

from __future__ import annotations

import argparse
import datetime as dt
import json
import webbrowser
from pathlib import Path

import h1b_greenhouse as hg
import config

OUT_DEFAULT = Path(config.PROJECT_ROOT) / "site" / "index.html"


def collect() -> list[dict]:
    """Current roles from the original board sources (the 944-style set)."""
    jobs = hg.daily_fresh_swe(us_only=True, include_aggregator=False,
                              include_custom=False)
    out = []
    for j in jobs:
        out.append({
            "company": (j.get("company") or "").split("|")[0],  # clean Workday token
            "title": j.get("title", ""),
            "location": j.get("location", "") or "Location N/A",
            "url": j.get("url", ""),
            "source": j.get("ats", ""),
            "new_grad": bool(j.get("new_grad")),
            "is_new": bool(j.get("is_new")),
            "first_seen": j.get("first_seen", "") or j.get("updated_at", ""),
        })
    return out


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fresh SWE Roles &middot; {count}</title>
<style>
  :root {{
    --bg:#f6f8fa; --card:#fff; --border:#e6e8eb; --ink:#1f2328; --muted:#6b7280;
    --accent:#1a56c4; --accent-bg:#e8f0fe;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width:1280px; margin:0 auto; padding:24px 18px 60px; }}
  h1 {{ font-size:26px; margin:0 0 4px; letter-spacing:-.01em; }}
  .sub {{ color:var(--muted); font-size:14px; margin-bottom:18px; }}
  .controls {{ position:sticky; top:0; z-index:10; background:var(--bg);
    padding:12px 0; border-bottom:1px solid var(--border); margin-bottom:18px;
    display:flex; flex-wrap:wrap; gap:10px; align-items:center; }}
  input[type=search], select {{ font-size:14px; padding:9px 12px; border:1px solid var(--border);
    border-radius:9px; background:#fff; color:var(--ink); }}
  input[type=search] {{ flex:1; min-width:220px; }}
  .chip {{ font-size:13px; padding:7px 13px; border:1px solid var(--border); border-radius:18px;
    background:#fff; cursor:pointer; user-select:none; text-transform:capitalize; }}
  .chip.on {{ background:var(--accent-bg); border-color:var(--accent); color:var(--accent); font-weight:600; }}
  label.tog {{ font-size:13px; color:var(--muted); display:flex; align-items:center; gap:6px; cursor:pointer; }}
  .count {{ font-size:13px; color:var(--muted); margin:0 0 14px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:12px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:12px;
    padding:15px 16px; display:flex; flex-direction:column; gap:7px; text-decoration:none; color:inherit;
    transition:box-shadow .12s, transform .12s; }}
  .card:hover {{ box-shadow:0 4px 14px rgba(0,0,0,.08); transform:translateY(-1px); }}
  .co {{ display:flex; align-items:center; gap:9px; }}
  .mono {{ width:32px; height:32px; border-radius:8px; background:var(--accent-bg); color:#3c4043;
    font-weight:700; font-size:14px; display:flex; align-items:center; justify-content:center; flex:0 0 auto; }}
  .co-name {{ font-size:13px; font-weight:600; color:#3c4043; }}
  .title {{ font-size:15px; font-weight:650; line-height:1.3; color:var(--accent); }}
  .loc {{ font-size:12.5px; color:var(--muted); }}
  .meta {{ display:flex; align-items:center; gap:7px; flex-wrap:wrap; margin-top:2px; }}
  .badge {{ font-size:10px; font-weight:700; letter-spacing:.03em; padding:2px 7px; border-radius:10px; }}
  .b-src {{ text-transform:capitalize; }}
  .b-new {{ background:#e6f4ea; color:#137333; }}
  .b-grad {{ background:#e8f0fe; color:#1a56c4; }}
  .seen {{ font-size:11px; color:#9aa0a6; margin-left:auto; }}
  .empty {{ text-align:center; color:var(--muted); padding:60px 0; }}
  footer {{ margin-top:34px; color:#9aa0a6; font-size:12px; line-height:1.6; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Fresh SWE Roles</h1>
  <div class="sub">Entry-level &amp; early-career Software Engineer roles from H-1B sponsor boards
    (Greenhouse, Lever, Ashby, Workday) &middot; US-only &middot; generated {date}</div>

  <div class="controls">
    <input type="search" id="q" placeholder="Search title, company, or location…" autocomplete="off">
    <span id="chips"></span>
    <label class="tog"><input type="checkbox" id="gradOnly"> New-grad only</label>
    <select id="sort">
      <option value="seen">Newest first</option>
      <option value="company">Company A–Z</option>
      <option value="title">Title A–Z</option>
    </select>
  </div>

  <div class="count" id="count"></div>
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" style="display:none">No roles match your filters.</div>

  <footer>
    {count} roles &middot; sourced from public ATS boards of top H-1B sponsors.
    Click a card to open the original posting. Data is a snapshot from {date}; regenerate with
    <code>python src/jobs_site.py</code>.
  </footer>
</div>

<script>
const JOBS = {data};
const SOURCES = [...new Set(JOBS.map(j => j.source))].sort();
let active = new Set(SOURCES);   // all sources on by default

const el = id => document.getElementById(id);
const grid = el('grid'), q = el('q'), gradOnly = el('gradOnly'), sortSel = el('sort'),
      countEl = el('count'), emptyEl = el('empty'), chips = el('chips');

// source filter chips
SOURCES.forEach(s => {{
  const c = document.createElement('span');
  c.className = 'chip on'; c.textContent = s; c.dataset.src = s;
  c.onclick = () => {{ c.classList.toggle('on');
    c.classList.contains('on') ? active.add(s) : active.delete(s); render(); }};
  chips.appendChild(c);
}});

function monogram(name) {{ const l = (name.trim()[0] || '?').toUpperCase(); return l; }}
function esc(s) {{ return (s||'').replace(/[&<>"]/g, m => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[m])); }}

function render() {{
  const term = q.value.trim().toLowerCase();
  const grad = gradOnly.checked;
  let rows = JOBS.filter(j =>
    active.has(j.source) &&
    (!grad || j.new_grad) &&
    (!term || (j.title+' '+j.company+' '+j.location).toLowerCase().includes(term)));

  const s = sortSel.value;
  rows.sort((a,b) => s === 'company' ? a.company.localeCompare(b.company)
    : s === 'title' ? a.title.localeCompare(b.title)
    : (b.first_seen||'').localeCompare(a.first_seen||''));

  countEl.textContent = rows.length + ' role' + (rows.length!==1?'s':'') +
    (rows.length!==JOBS.length ? ' of ' + JOBS.length : '');
  emptyEl.style.display = rows.length ? 'none' : 'block';

  grid.innerHTML = rows.map(j => `
    <a class="card" href="${{esc(j.url)}}" target="_blank" rel="noopener">
      <div class="co"><div class="mono">${{monogram(j.company)}}</div>
        <div class="co-name">${{esc(j.company)}}</div></div>
      <div class="title">${{esc(j.title)}}</div>
      <div class="loc">${{esc(j.location)}}</div>
      <div class="meta">
        <span class="badge b-src">${{esc(j.source)}}</span>
        ${{j.new_grad ? '<span class="badge b-grad">NEW GRAD</span>' : ''}}
        ${{j.is_new ? '<span class="badge b-new">NEW</span>' : ''}}
        <span class="seen">${{esc(j.first_seen)}}</span>
      </div>
    </a>`).join('');
}}

q.oninput = render; gradOnly.onchange = render; sortSel.onchange = render;
render();
</script>
</body>
</html>
"""


def build(jobs: list[dict], generated: str) -> str:
    data = json.dumps(jobs, ensure_ascii=False).replace("</", "<\\/")
    return _PAGE.format(count=len(jobs), date=generated, data=data)


def main():
    ap = argparse.ArgumentParser(description="Build a static webpage of all current roles")
    ap.add_argument("--out", default=str(OUT_DEFAULT), help="output HTML path")
    ap.add_argument("--open", action="store_true", help="open the page in a browser after building")
    args = ap.parse_args()

    jobs = collect()
    html = build(jobs, dt.date.today().strftime("%B %-d, %Y"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"✅ Wrote {len(jobs)} roles to {out}  ({out.stat().st_size//1024} KB)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
