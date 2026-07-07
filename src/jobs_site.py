#!/usr/bin/env python3
# ============================================================
#  jobs_site.py
#  Generate a single self-contained static webpage listing all
#  current board roles (Workday + Greenhouse/Lever/Ashby) with
#  client-side search, source filters, new-grad toggle, and sort.
#
#  Design: dark glassmorphism — frosted translucent cards over a
#  soft gradient "void", typography-driven hierarchy (Space
#  Grotesk display + Inter body), purposeful hover motion.
#
#  No server, no dependencies — the data is embedded as JSON and
#  the page runs on vanilla JS. Open the output file in a browser.
#
#  Usage:
#    python src/jobs_site.py                 # -> docs/index.html (GitHub Pages)
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

# docs/ because GitHub Pages can only serve a branch's root or /docs folder.
OUT_DEFAULT = Path(config.PROJECT_ROOT) / "docs" / "index.html"

# Big-name employers surfaced as "browse yourself" links (site hero + email):
# their boards are huge (Amazon) or block scraping (Google/Apple/Tesla/
# Bloomberg). Single source of truth — daily_job_email imports this.
# (name, url, brand color for the pill dot)
BROWSE_LINKS = [
    ("Amazon",    "https://www.amazon.jobs/en/search?base_query=software+engineer&loc_query=United+States", "#ff9900"),
    ("Google",    "https://www.google.com/about/careers/applications/jobs/results/?q=software%20engineer&hl=en&target_level=EARLY&location=United%20States", "#4285f4"),
    ("Microsoft", "https://jobs.careers.microsoft.com/global/en/search?q=software%20engineer&lc=United%20States", "#00a4ef"),
    ("Apple",     "https://jobs.apple.com/en-us/search?search=software%20engineer&location=united-states-USA", "#b8bfc6"),
    ("Tesla",     "https://www.tesla.com/careers/search/?query=software%20engineer&region=5", "#e82127"),
    ("Bloomberg", "https://bloomberg.avature.net/careers/SearchJobs/Software%20engineer?1686=%5B55478%5D&1686_format=2312&listFilterMode=1&jobRecordsPerPage=12&", "#9d7bff"),
    ("LinkedIn",  "https://www.linkedin.com/jobs/search/?f_C=1337&f_E=2%2C3&keywords=software%20engineer&location=United%20States&origin=JOB_SEARCH_PAGE_JOB_FILTER", "#0a66c2"),
]


def collect() -> list[dict]:
    """Current roles from the original board sources."""
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


def _browse_pills() -> str:
    return "".join(
        f'<a class="pill" href="{url}" target="_blank" rel="noopener">'
        f'<span class="dot" style="background:{color}"></span>{name}'
        f'<span class="arrow">&#8599;</span></a>'
        for name, url, color in BROWSE_LINKS)


# Template uses __TOKEN__ placeholders (not str.format) so the CSS/JS braces
# don't need escaping.
_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fresh SWE Roles · __COUNT__</title>
<meta name="description" content="Entry-level software engineer roles at verified H-1B sponsors, refreshed daily.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#070b14; --ink:#e8edf6; --muted:#93a0b4; --faint:#5d6a80;
    --glass:rgba(255,255,255,.045); --glass-hi:rgba(255,255,255,.08);
    --line:rgba(255,255,255,.09); --line-hi:rgba(255,255,255,.22);
    --indigo:#6366f1; --sky:#38bdf8; --violet:#a78bfa;
    --grad:linear-gradient(135deg,#6366f1,#38bdf8);
    --r-lg:20px; --r-md:14px;
  }
  * { box-sizing:border-box; }
  html { scroll-behavior:smooth; }
  body {
    margin:0; background:var(--bg); color:var(--ink);
    font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    font-size:15px; line-height:1.5;
    -webkit-font-smoothing:antialiased;
  }
  /* soft gradient "void" glows */
  .glow { position:fixed; inset:0; z-index:-1; pointer-events:none;
    background:
      radial-gradient(52rem 30rem at 12% -8%, rgba(99,102,241,.16), transparent 60%),
      radial-gradient(44rem 26rem at 88% 4%, rgba(56,189,248,.10), transparent 60%),
      radial-gradient(60rem 34rem at 50% 115%, rgba(167,139,250,.10), transparent 60%); }
  .wrap { max-width:1320px; margin:0 auto; padding:44px 22px 80px; }
  a { color:inherit; }

  /* ── Hero ─────────────────────────────────────────── */
  .eyebrow { font-size:12px; font-weight:600; letter-spacing:.14em;
    text-transform:uppercase; color:var(--sky); margin-bottom:14px; }
  h1 { font-family:'Space Grotesk',sans-serif; font-size:clamp(34px,5vw,54px);
    font-weight:700; letter-spacing:-.02em; line-height:1.05; margin:0 0 12px; }
  h1 .accent { background:var(--grad); -webkit-background-clip:text;
    background-clip:text; color:transparent; }
  .sub { color:var(--muted); font-size:16px; max-width:640px; margin:0 0 22px; }
  .stats { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:34px; }
  .stat { padding:7px 15px; border-radius:999px; font-size:13px; font-weight:600;
    background:var(--glass); border:1px solid var(--line); color:var(--muted); }
  .stat b { color:var(--ink); font-weight:700; }
  .stat.hot b { color:#4ade80; }

  /* ── Browse-direct pills (top of page) ────────────── */
  .browse { border:1px solid var(--line); border-radius:var(--r-lg);
    background:var(--glass); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
    padding:20px 22px 16px; margin-bottom:30px; }
  .browse h2 { font-family:'Space Grotesk',sans-serif; font-size:17px;
    font-weight:600; margin:0 0 3px; }
  .browse p { color:var(--faint); font-size:13px; margin:0 0 14px; }
  .pills { display:flex; flex-wrap:wrap; gap:9px; }
  .pill { display:inline-flex; align-items:center; gap:8px; padding:9px 16px;
    border-radius:999px; background:var(--glass); border:1px solid var(--line);
    font-size:13.5px; font-weight:600; text-decoration:none;
    transition:border-color .18s, background .18s, transform .18s; }
  .pill:hover { border-color:var(--line-hi); background:var(--glass-hi);
    transform:translateY(-1px); }
  .pill .dot { width:8px; height:8px; border-radius:50%; flex:0 0 auto; }
  .pill .arrow { color:var(--faint); font-size:12px; transition:color .18s, transform .18s; }
  .pill:hover .arrow { color:var(--sky); transform:translate(1px,-1px); }

  /* ── Sticky glass toolbar ─────────────────────────── */
  .toolbar { position:sticky; top:12px; z-index:20; display:flex; flex-wrap:wrap;
    gap:10px; align-items:center; padding:12px 14px; margin-bottom:14px;
    border:1px solid var(--line); border-radius:var(--r-lg);
    background:rgba(11,16,28,.72); backdrop-filter:blur(18px);
    -webkit-backdrop-filter:blur(18px); }
  input[type=search] { flex:1; min-width:200px; padding:10px 15px; font:inherit;
    font-size:14px; color:var(--ink); background:var(--glass);
    border:1px solid var(--line); border-radius:var(--r-md); outline:none;
    transition:border-color .18s, box-shadow .18s; }
  input[type=search]::placeholder { color:var(--faint); }
  input[type=search]:focus { border-color:var(--indigo);
    box-shadow:0 0 0 3px rgba(99,102,241,.22); }
  .chip { padding:8px 15px; border-radius:999px; font-size:13px; font-weight:600;
    text-transform:capitalize; cursor:pointer; user-select:none;
    background:transparent; border:1px solid var(--line); color:var(--muted);
    transition:all .18s; }
  .chip:hover { border-color:var(--line-hi); color:var(--ink); }
  .chip.on { background:rgba(99,102,241,.16); border-color:rgba(99,102,241,.55);
    color:#c7d2fe; }
  label.tog { display:flex; align-items:center; gap:7px; font-size:13px;
    font-weight:500; color:var(--muted); cursor:pointer; }
  label.tog input { accent-color:var(--indigo); width:15px; height:15px; }
  select { padding:9px 12px; font:inherit; font-size:13px; color:var(--ink);
    background:var(--glass); border:1px solid var(--line);
    border-radius:var(--r-md); outline:none; cursor:pointer; }
  select option { background:#0d1322; }
  .count { font-size:13px; color:var(--faint); margin:0 4px 16px; }

  /* ── Job cards ────────────────────────────────────── */
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(295px,1fr));
    gap:14px; }
  .card { display:flex; flex-direction:column; gap:9px; padding:18px;
    border-radius:var(--r-lg); text-decoration:none;
    background:var(--glass); border:1px solid var(--line);
    backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);
    transition:transform .18s, border-color .18s, background .18s, box-shadow .18s; }
  .card:hover { transform:translateY(-3px); border-color:var(--line-hi);
    background:var(--glass-hi);
    box-shadow:0 14px 34px rgba(0,0,0,.36), 0 0 0 1px rgba(99,102,241,.14); }
  .co { display:flex; align-items:center; gap:10px; }
  .mono { width:36px; height:36px; border-radius:11px; flex:0 0 auto;
    display:flex; align-items:center; justify-content:center;
    font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:15px;
    color:#fff; }
  .co-name { font-size:13px; font-weight:600; color:var(--muted);
    text-transform:capitalize; overflow:hidden; text-overflow:ellipsis;
    white-space:nowrap; }
  .title { font-size:15.5px; font-weight:600; line-height:1.34; color:var(--ink); }
  .card:hover .title { background:var(--grad); -webkit-background-clip:text;
    background-clip:text; color:transparent; }
  .loc { font-size:12.5px; color:var(--faint); overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap; }
  .meta { display:flex; align-items:center; gap:7px; flex-wrap:wrap;
    margin-top:auto; padding-top:6px; }
  .badge { font-size:10px; font-weight:700; letter-spacing:.05em;
    text-transform:uppercase; padding:3px 9px; border-radius:999px; }
  .b-new { background:rgba(74,222,128,.14); color:#4ade80;
    border:1px solid rgba(74,222,128,.3); }
  .b-grad { background:rgba(167,139,250,.14); color:#c4b5fd;
    border:1px solid rgba(167,139,250,.3); }
  .b-src { background:var(--glass); border:1px solid var(--line); }
  .seen { font-size:11px; color:var(--faint); margin-left:auto; }
  .empty { grid-column:1/-1; text-align:center; color:var(--faint);
    padding:70px 0; border:1px dashed var(--line); border-radius:var(--r-lg); }

  footer { margin-top:44px; padding-top:22px; border-top:1px solid var(--line);
    color:var(--faint); font-size:12.5px; line-height:1.7; }
  footer code { background:var(--glass); border:1px solid var(--line);
    border-radius:6px; padding:1px 7px; font-size:11.5px; }

  @media (max-width:640px) {
    .wrap { padding:30px 14px 60px; }
    .toolbar { top:8px; }
  }
</style>
</head>
<body>
<div class="glow"></div>
<div class="wrap">

  <div class="eyebrow">H-1B sponsor job board · updated __DATE__</div>
  <h1>Fresh SWE roles, <span class="accent">every day</span></h1>
  <p class="sub">Entry-level &amp; early-career software engineer roles pulled from the
    public boards of verified H-1B sponsors — Greenhouse, Lever, Ashby &amp; Workday. US-only.</p>
  <div class="stats" id="stats"></div>

  <section class="browse">
    <h2>Browse these employers directly</h2>
    <p>Big or scrape-blocked boards — open each one&rsquo;s software-engineer search and filter yourself.</p>
    <div class="pills">__PILLS__</div>
  </section>

  <div class="toolbar">
    <input type="search" id="q" placeholder="Search title, company, or location…" autocomplete="off">
    <span id="chips" style="display:contents"></span>
    <label class="tog"><input type="checkbox" id="gradOnly"> New-grad only</label>
    <select id="sort">
      <option value="seen">Newest first</option>
      <option value="company">Company A–Z</option>
      <option value="title">Title A–Z</option>
    </select>
  </div>

  <div class="count" id="count"></div>
  <div class="grid" id="grid"></div>

  <footer>
    __COUNT__ roles · sourced from the public ATS boards of top H-1B sponsors ·
    refreshed daily by GitHub Actions. Click a card to open the original posting.
    &ldquo;First seen&rdquo; is the date this board first spotted the posting.
    Rebuild locally with <code>python src/jobs_site.py</code>.
  </footer>
</div>

<script>
const JOBS = __DATA__;
const SOURCES = [...new Set(JOBS.map(j => j.source))].sort();
let active = new Set(SOURCES);

const el = id => document.getElementById(id);
const grid = el('grid'), q = el('q'), gradOnly = el('gradOnly'),
      sortSel = el('sort'), countEl = el('count'), chips = el('chips');

// header stats
(() => {
  const nNew = JOBS.filter(j => j.is_new).length;
  const nGrad = JOBS.filter(j => j.new_grad).length;
  el('stats').innerHTML =
    `<span class="stat"><b>${JOBS.length}</b> open roles</span>` +
    (nNew ? `<span class="stat hot"><b>${nNew}</b> new today</span>` : '') +
    `<span class="stat"><b>${nGrad}</b> explicit new-grad</span>` +
    `<span class="stat"><b>${SOURCES.length}</b> sources</span>`;
})();

// source filter chips
SOURCES.forEach(s => {
  const c = document.createElement('span');
  c.className = 'chip on'; c.textContent = s;
  c.onclick = () => { c.classList.toggle('on');
    c.classList.contains('on') ? active.add(s) : active.delete(s); render(); };
  chips.appendChild(c);
});

const esc = s => (s||'').replace(/[&<>"]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));
// deterministic gradient per company
function monoStyle(name) {
  let h = 0; for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `background:linear-gradient(135deg,hsl(${h},62%,52%),hsl(${(h+45)%360},62%,40%))`;
}

function render() {
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

  grid.innerHTML = rows.length ? rows.map(j => `
    <a class="card" href="${esc(j.url)}" target="_blank" rel="noopener">
      <div class="co">
        <div class="mono" style="${monoStyle(j.company)}">${esc((j.company.trim()[0]||'?').toUpperCase())}</div>
        <div class="co-name">${esc(j.company)}</div>
      </div>
      <div class="title">${esc(j.title)}</div>
      <div class="loc">${esc(j.location)}</div>
      <div class="meta">
        <span class="badge b-src">${esc(j.source)}</span>
        ${j.new_grad ? '<span class="badge b-grad">New grad</span>' : ''}
        ${j.is_new ? '<span class="badge b-new">New</span>' : ''}
        <span class="seen">${esc(j.first_seen)}</span>
      </div>
    </a>`).join('')
    : '<div class="empty">No roles match your filters.</div>';
}

q.oninput = render; gradOnly.onchange = render; sortSel.onchange = render;
render();
</script>
</body>
</html>
"""


def build(jobs: list[dict], generated: str) -> str:
    data = json.dumps(jobs, ensure_ascii=False).replace("</", "<\\/")
    return (_PAGE
            .replace("__COUNT__", str(len(jobs)))
            .replace("__DATE__", generated)
            .replace("__PILLS__", _browse_pills())
            .replace("__DATA__", data))


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
